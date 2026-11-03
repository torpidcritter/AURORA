import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { creds, login, totpCode } from "./helpers";

const admin = creds();

// Login is rate limited per account (5/min), so the suite signs in as the admin once and shares that session.
test.describe.serial("Aurora end to end", () => {
  let adminCtx: BrowserContext;
  let page: Page;

  test("unauthenticated users are sent to login; bad password is rejected", async ({ page }) => {
    await page.goto("/products");
    await expect(page).toHaveURL(/\/login\?next=%2Fproducts/);
    await page.getByLabel("Email").fill(admin.email);
    await page.getByLabel("Password").fill("Wrong-password-123");
    await page.getByRole("button", { name: /sign in/i }).click();
    await expect(page.getByText("Invalid email or password")).toBeVisible();
  });

  test("login sets httpOnly cookies only and lands on the dashboard", async ({ browser }) => {
    adminCtx = await browser.newContext();
    page = await adminCtx.newPage();
    const context = adminCtx;
    await login(page, admin.email, admin.password);
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    await expect(page.getByText("Inventory health")).toBeVisible();
    const cookies = await context.cookies();
    const at = cookies.find((c) => c.name === "aurora_at");
    expect(at?.httpOnly).toBe(true);
    expect(at?.sameSite).toBe("Strict");
    expect(await page.evaluate(() => document.cookie)).not.toContain("aurora_");
  });

  test("cross-site style mutation without the CSRF header is blocked by the BFF", async () => {
    const status = await page.evaluate(async () => (await fetch("/api/proxy/jobs/monitor", { method: "POST" })).status);
    expect(status).toBe(403);
  });

  test("product detail shows forecast, backtest, drivers and a recommendation; stock edits save", async () => {
    await page.goto("/products");
    await expect(page.getByRole("table", { name: "Products" })).toBeVisible();
    await page.getByRole("table", { name: "Products" }).getByRole("link").first().click();
    await expect(page.getByRole("heading", { level: 1 })).toContainText("·");
    await expect(page.getByText(/Calibrated interval|Uncalibrated interval/)).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole("table", { name: "Backtest metrics by method" })).toBeVisible();
    await expect(page.getByText(/Urgency \d+ \/ 100/)).toBeVisible();
    await page.getByLabel("Lead time (days)").fill("9");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("status").filter({ hasText: "Saved." })).toBeVisible();
    await expect(page.getByRole("definition").filter({ hasText: /^9 days$/ })).toBeVisible();
  });

  test("a launch plan forecasts a product from a similar one", async () => {
    await page.goto("/products");
    await page.getByRole("table", { name: "Products" }).getByRole("link").nth(1).click();
    await expect(page.getByRole("heading", { level: 1 })).toContainText("·");
    const sku = (await page.getByRole("heading", { level: 1 }).innerText()).split(" · ")[0];
    await page.getByRole("button", { name: "Plan a launch" }).click();
    await page.getByLabel("Launch date", { exact: true }).fill("2099-01-01");
    const picker = page.getByLabel("Similar product 1", { exact: true });
    await expect(picker.locator("option").nth(2)).toBeAttached();
    const options = await picker.locator("option").allTextContents();
    const other = options.find((o) => o.includes(" · ") && !o.startsWith(`${sku} `));
    await picker.selectOption({ label: other! });
    await page.getByLabel("Expected sales (% of similar products)").fill("50");
    await page.getByRole("button", { name: "Save launch plan" }).click();
    await expect(page.getByText("First 13 weeks")).toBeVisible();
    await expect(page.getByText("Not launched yet.")).toBeVisible();
    await page.goto("/launches");
    await expect(page.getByRole("table", { name: "Launch plans" })).toContainText(sku);
    await page.getByRole("table", { name: "Launch plans" }).getByRole("link", { name: sku }).click();
    await page.getByRole("button", { name: "Delete plan" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete plan" }).click();
    await expect(page.getByText(/No launch plan/)).toBeVisible();
  });

  test("a planned promotion is created with its own uplift and deleted", async () => {
    const name = `E2E promo ${Date.now()}`;
    await page.goto("/promotions");
    await page.getByRole("button", { name: "Plan a promotion" }).click();
    await page.getByLabel("Name", { exact: true }).fill(name);
    await page.getByLabel("First day").fill("2099-03-01");
    await page.getByLabel("Last day").fill("2099-03-07");
    const picker = page.getByLabel("Product 1", { exact: true });
    await expect(picker.locator("option").nth(1)).toBeAttached();
    await picker.selectOption({ index: 1 });
    await page.getByRole("button", { name: "Estimate uplift" }).click();
    await expect(page.getByRole("status").filter({ hasText: /from|No estimate/ })).toBeVisible();
    await page.getByLabel("Expected uplift (×)").fill("1.5");
    await page.getByRole("button", { name: "Create promotion" }).click();
    await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
    await expect(page.getByText("×1.5 (+50%)")).toBeVisible();
    await expect(page.getByText("entered by a planner")).toBeVisible();
    await page.getByRole("button", { name: "Delete promotion" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete promotion" }).click();
    await expect(page).toHaveURL(/\/promotions$/);
    await expect(page.getByText(name)).toHaveCount(0);
  });

  test("recommendations, risk matrix and scenario run", async () => {
    await page.goto("/inventory");
    await expect(page.getByRole("table", { name: "Inventory recommendations" })).toBeVisible({ timeout: 60_000 });
    await page.goto("/risk");
    await expect(page.getByText("Risk matrix")).toBeVisible({ timeout: 60_000 });
    await page.goto("/scenarios");
    await page.getByLabel("Product").selectOption({ index: 1 });
    await page.getByLabel("Add adjustment").selectOption("demand_increase");
    await page.getByRole("button", { name: "Run scenario" }).click();
    await expect(page.getByRole("table", { name: "Baseline versus scenario" })).toBeVisible({ timeout: 60_000 });
  });

  test("a category forecast adds up to its products", async () => {
    await page.goto("/category-forecasts");
    await page.getByLabel("Group").selectOption({ index: 1 });
    await page.getByLabel("Horizon (days)").selectOption("14");
    await page.getByRole("button", { name: "Forecast group" }).click();
    const table = page.getByRole("table", { name: "Product breakdown" });
    await expect(table).toBeVisible({ timeout: 90_000 });
    await expect(table.getByRole("row").filter({ hasText: "Total" })).toContainText("100%");
    await expect(page.getByText(/backtest periods/)).toBeVisible();
  });

  test("a budget plan stays within the budget", async () => {
    await page.goto("/budget-plan");
    await page.getByLabel("Budget", { exact: true }).fill("500");
    await page.getByRole("button", { name: "Plan orders" }).click();
    await expect(page.getByRole("table", { name: "Budget plan" })).toBeVisible({ timeout: 90_000 });
    await expect(page.getByText("Expected fill rate")).toBeVisible();
    await expect(page.getByText(/of \$500/)).toBeVisible();
  });

  test("an in-app digest is scheduled, sent now and readable", async () => {
    const name = `E2E digest ${Date.now() % 100_000}`;
    await page.goto("/digests");
    await page.getByLabel("Digest name").fill(name);
    await page.getByRole("button", { name: "Create digest" }).click();
    const row = page.getByRole("table", { name: "Digest schedules" }).getByRole("row").filter({ hasText: name });
    await expect(row).toContainText("Daily at 07:00 UTC");
    await row.getByRole("button", { name: "Send now" }).click();
    await expect(page.getByLabel("Digest content")).toContainText("Stockout risk", { timeout: 90_000 });
    await expect(page.getByRole("table", { name: "Recent digests" })).toContainText(name);
  });

  test("signals can be added and deleted with confirmation", async () => {
    await page.goto("/signals");
    const name = `E2E promo ${Date.now()}`;
    await page.getByLabel("Name").fill(name);
    await page.getByLabel("Start").fill("2030-01-01");
    await page.getByLabel("End").fill("2030-01-07");
    await page.getByRole("button", { name: "Add signal" }).click();
    const row = page.getByRole("row").filter({ hasText: name });
    await expect(row).toBeVisible();
    await row.getByRole("button", { name: "Delete" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
    await expect(row).toHaveCount(0);
  });

  test("supplier, purchase order from draft to received, overrides and overstock", async () => {
    const supplier = `E2E Supply ${Date.now()}`;
    await page.goto("/suppliers");
    await page.getByLabel("Name").fill(supplier);
    await page.getByLabel("Lead time (days)").fill("5");
    await page.getByRole("button", { name: "Add supplier" }).click();
    await expect(page.getByRole("row").filter({ hasText: supplier })).toBeVisible();

    // Link a product to the supplier and add a planner override on its page.
    await page.goto("/products");
    await page.getByRole("table", { name: "Products" }).getByRole("link").nth(1).click();
    await page.getByLabel("Supplier", { exact: true }).selectOption({ label: `${supplier} (5 d lead time)` });
    await page.getByRole("button", { name: "Update supplier" }).click();
    await expect(page.getByRole("button", { name: "Update supplier" })).toBeDisabled();
    await page.getByLabel("From", { exact: true }).fill("2031-01-01");
    await page.getByLabel("To", { exact: true }).fill("2031-01-07");
    await page.getByLabel("Reason", { exact: true }).fill("E2E launch");
    await page.getByRole("button", { name: "Add override" }).click();
    await expect(page.getByText("E2E launch")).toBeVisible();

    // New order: draft, add a line, approve, mark as sent, receive.
    await page.goto("/purchase-orders");
    await page.getByRole("button", { name: "New order" }).click();
    await expect(page).toHaveURL(/\/purchase-orders\/[0-9a-f-]{36}$/);
    await page.getByRole("button", { name: "Edit" }).click();
    await page.getByLabel("Supplier").selectOption({ label: supplier });
    await page.getByLabel("Add a product").selectOption({ index: 1 });
    await page.getByRole("button", { name: "Add line" }).click();
    await page.getByRole("button", { name: "Save draft" }).click();
    await expect(page.getByRole("table", { name: /^Lines of PO-/ })).toBeVisible();
    await page.getByRole("button", { name: "Approve" }).click();
    await expect(page.getByText("Approved", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Mark as sent" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Mark as sent" }).click();
    await expect(page.getByText("Sent", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Receive goods" }).click();
    await page.getByRole("button", { name: "Book into stock" }).click();
    await expect(page.getByRole("table", { name: /^Lines of PO-/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "Cancel order" })).toHaveCount(0); // fully received: nothing left to do
    const csv = await page.evaluate(async (href) => (await fetch(href)).text(), await page.getByRole("link", { name: "Download CSV" }).getAttribute("href") ?? "");
    expect(csv).toContain(supplier);
    // The received order now counts towards the supplier's measured reliability.
    await page.goto("/suppliers");
    await expect(page.getByRole("row").filter({ hasText: supplier })).toContainText("1 delivery");

    await page.goto("/overstock");
    await expect(page.getByRole("heading", { name: "Overstock and dead stock" })).toBeVisible();
    await expect(page.getByText("Excess stock value")).toBeVisible();
    await page.goto("/overrides");
    await expect(page.getByRole("table", { name: "Forecast overrides and their accuracy" })).toContainText("E2E launch");
  });

  test("a second location: stock moves between locations and totals stay the same", async () => {
    const code = `E2E-${Date.now() % 1_000_000}`;
    await page.goto("/locations");
    await page.getByLabel("Code", { exact: true }).fill(code);
    await page.getByLabel("Name", { exact: true }).fill("E2E store");
    await page.getByRole("button", { name: "Add location" }).click();
    await expect(page.getByRole("table", { name: "Locations" }).getByText(code)).toBeVisible();

    await page.goto("/products");
    await page.getByRole("table", { name: "Products" }).getByRole("link").nth(2).click();
    const table = page.getByRole("table", { name: "Stock by location" });
    await expect(table).toBeVisible();
    const total = await table.getByRole("row").filter({ hasText: "Total" }).getByRole("cell").nth(1).innerText();
    const before = Number(await table.getByRole("row").filter({ hasText: code }).getByRole("cell").nth(1).innerText());
    await page.getByLabel("Move from").selectOption({ index: 1 });
    await page.getByLabel("Move to").selectOption({ label: code });
    await page.getByLabel("Units", { exact: true }).fill("1");
    await page.getByRole("button", { name: "Transfer", exact: true }).click();
    await expect(table.getByRole("row").filter({ hasText: code }).getByRole("cell").nth(1)).toHaveText(String(before + 1));
    await expect(table.getByRole("row").filter({ hasText: "Total" }).getByRole("cell").nth(1)).toHaveText(total);
    await page.goto("/locations");
    await expect(page.getByRole("table", { name: "Recent stock transfers" })).toContainText(code);
  });

  test("training runs on the real worker and registers a version", async () => {
    test.setTimeout(600_000);
    await page.goto("/models");
    await page.getByLabel("Max epochs").fill("3");
    await page.getByRole("button", { name: "Start training" }).click();
    await expect(page.getByText(/^(queued|running)$/).first()).toBeVisible();
    await expect(page.getByText("succeeded")).toBeVisible({ timeout: 540_000 });
    await expect(page.getByRole("table", { name: "Model versions" })).toBeVisible();
  });

  test("admin creates a viewer; the viewer cannot see admin pages or change data", async ({ browser }) => {
    await page.goto("/admin/users");
    const email = `viewer.${Date.now()}@example.com`;
    const pw = "Viewer-E2E-pass1";
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Initial password").fill(pw);
    await page.getByRole("button", { name: "Create user" }).click();
    await expect(page.getByText(`Created ${email}`)).toBeVisible();

    const ctx = await browser.newContext();
    const vp = await ctx.newPage();
    await login(vp, email, pw);
    await expect(vp.getByRole("link", { name: "Users" })).toHaveCount(0);
    await vp.goto("/admin/users");
    await expect(vp.getByText("Only administrators can manage users.")).toBeVisible();
    // Backend enforcement, independent of the UI: a viewer's write is refused.
    const status = await vp.evaluate(async () =>
      (await fetch("/api/proxy/signals", {
        method: "POST",
        headers: { "content-type": "application/json", "x-aurora-csrf": "1" },
        body: JSON.stringify({ name: "x", signal_type: "other", start_date: "2030-01-01", end_date: "2030-01-02", magnitude: 0.1 }),
      })).status,
    );
    expect(status).toBe(403);
    await ctx.close();
  });

  test("an invited person creates their own account and signs in", async ({ browser }) => {
    await page.goto("/admin/users");
    const email = `invitee.${Date.now()}@example.com`;
    await page.getByLabel("Invitee e-mail").fill(email);
    await page.getByLabel("Invitee role").selectOption("operator");
    await page.getByRole("button", { name: "Send invitation" }).click();
    const url = await page.getByLabel("Invitation link", { exact: true }).inputValue();
    expect(url).toContain("/accept-invite#token=");
    await expect(page.getByRole("table", { name: "Invitations" })).toContainText(email);

    const ctx = await browser.newContext();
    const ip = await ctx.newPage();
    await ip.goto(`/accept-invite#${url.split("#")[1]}`);
    await expect(ip.getByText(/as operator/)).toBeVisible();
    await ip.getByLabel("Your name").fill("Invited Person");
    await ip.getByLabel("Choose a password").fill("Invitee-E2E-pass1");
    await ip.getByLabel("Repeat the password").fill("Invitee-E2E-pass1");
    await ip.getByRole("button", { name: "Create my account" }).click();
    await expect(ip.getByText(`Your account ${email} is ready`)).toBeVisible();
    await login(ip, email, "Invitee-E2E-pass1");
    await expect(ip.getByText("Getting started")).toBeVisible();
    await ctx.close();
  });

  test("a user turns on two-factor login and then needs a code to sign in", async ({ browser }) => {
    await page.goto("/admin/users");
    const email = `twofactor.${Date.now()}@example.com`;
    const pw = "TwoFactor-E2E-pass1";
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Initial password").fill(pw);
    await page.getByRole("button", { name: "Create user" }).click();
    await expect(page.getByText(`Created ${email}`)).toBeVisible();

    const ctx = await browser.newContext();
    const up = await ctx.newPage();
    await login(up, email, pw);
    await up.goto("/account");
    await up.locator("#tf-pw").fill(pw); // the password card on the same page has a field with the same label
    await up.getByRole("button", { name: "Set up two-factor login" }).click();
    await expect(up.getByRole("img", { name: "QR code for your authenticator app" })).toBeVisible();
    const secret = (await up.getByLabel("Setup key").textContent()) ?? "";
    await up.getByLabel("Code from the app").fill(await totpCode(secret));
    await up.getByRole("button", { name: "Turn on" }).click();
    await expect(up.getByRole("list", { name: "Recovery codes" }).getByRole("listitem")).toHaveCount(10);
    const recovery = (await up.getByRole("list", { name: "Recovery codes" }).getByRole("listitem").first().textContent()) ?? "";
    await up.getByRole("button", { name: "I have saved them" }).click();
    await expect(up.getByText(/10 recovery codes left/)).toBeVisible();
    await ctx.close();

    // Signing in now takes the password and then a code; a recovery code works too.
    const ctx2 = await browser.newContext();
    const p2 = await ctx2.newPage();
    await p2.goto("/login");
    await p2.getByLabel("Email").fill(email);
    await p2.getByLabel("Password").fill(pw);
    await p2.getByRole("button", { name: /sign in/i }).click();
    await p2.getByLabel("Authentication code").fill(recovery);
    await p2.getByRole("button", { name: /sign in/i }).click();
    await expect(p2).toHaveURL(/\/dashboard$/);
    await ctx2.close();

    await page.reload();
    // Users are listed oldest first, so on a reused test database the new one can be on a later page.
    const row = page.getByRole("row").filter({ hasText: email });
    const next = page.getByRole("navigation", { name: "Pagination" }).getByRole("button", { name: "Next" });
    await expect(page.getByRole("table", { name: "Users" })).toBeVisible();
    while (!(await row.count()) && (await next.count()) && (await next.isEnabled())) {
      await next.click();
      await page.waitForLoadState("networkidle");
    }
    await expect(row).toContainText("2FA on");
  });

  test("assistant answers from real data (built-in mode without an API key)", async () => {
    await page.goto("/assistant");
    await page.getByRole("button", { name: "Which products are at risk of stocking out?" }).click();
    await expect(page.getByText(/Stockout risk: \d+ critical/)).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText(/Built-in summary|Claude/).first()).toBeVisible();
  });

  test("an assistant proposal changes nothing until decided", async () => {
    await page.goto("/assistant");
    await page.getByLabel("Message").fill("Please acknowledge the open alerts");
    await page.getByRole("button", { name: "Send" }).click();
    const card = page.getByRole("log").getByRole("group", { name: "Proposed change" }).last();
    await expect(card).toContainText(/Acknowledge \d+ open alert/, { timeout: 60_000 });
    await card.getByRole("button", { name: "Dismiss proposal" }).click();
    await expect(card.getByRole("status")).toHaveText("Dismissed. Nothing was changed.");
  });

  test("a saved view is stored and reapplied; the inventory report downloads as PDF", async () => {
    const name = `E2E view ${Date.now()}`;
    await page.goto("/alerts");
    await page.getByLabel("Severity").selectOption("critical");
    await page.getByRole("button", { name: "Save these filters" }).click();
    await page.getByLabel("View name").fill(name);
    await page.getByRole("button", { name: "Save view" }).click();
    await expect(page.getByLabel("Saved views")).toHaveValue(/.+/);
    await page.getByLabel("Severity").selectOption("");
    await page.getByLabel("Saved views").selectOption({ label: name });
    await expect(page.getByLabel("Severity")).toHaveValue("critical");
    await page.getByRole("button", { name: "Delete view" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete view" }).click();
    await expect(page.getByRole("option", { name })).toHaveCount(0);

    await page.goto("/inventory");
    const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("link", { name: "Download PDF report" }).click()]);
    expect(download.suggestedFilename()).toBe("aurora-inventory-report.pdf");
  });

  test("the audit log exports as CSV and records the export", async () => {
    await page.goto("/admin/audit");
    await expect(page.getByRole("table", { name: "Audit events" })).toBeVisible();
    const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export CSV" }).click()]);
    expect(download.suggestedFilename()).toMatch(/^aurora-audit-.*\.csv$/);
    await page.reload();
    await expect(page.getByRole("table", { name: "Audit events" })).toContainText("audit.export");
  });

  test("an organisation export is built by the worker and downloads; deletion can be scheduled and cancelled", async () => {
    await page.goto("/admin/settings");
    await page.getByRole("button", { name: "Start export" }).click();
    const table = page.getByRole("table", { name: "Organisation exports" });
    await expect(table).toContainText("Ready", { timeout: 60_000 });
    const [zip] = await Promise.all([page.waitForEvent("download"), table.getByRole("button", { name: "Download ZIP" }).first().click()]);
    expect(zip.suggestedFilename()).toMatch(/\.zip$/);

    const hint = await page.getByText(/^Type “.+” to confirm$/).innerText();
    const orgName = hint.replace(/^Type “/, "").replace(/” to confirm$/, "");
    await page.getByLabel("Organisation name", { exact: true }).fill(orgName);
    await page.getByLabel("Your password", { exact: true }).fill(admin.password);
    await page.getByRole("button", { name: "Schedule deletion in 7 days" }).click();
    await expect(page.getByText(/This organisation will be deleted on/)).toBeVisible();
    await page.getByRole("button", { name: "Cancel deletion" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Keep organisation" }).click();
    await expect(page.getByText(/This organisation will be deleted on/)).toHaveCount(0);

    await page.goto("/account");
    const [mine] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Download my data" }).click()]);
    expect(mine.suggestedFilename()).toBe("aurora-my-data.json");
  });

  test("an API key works against the API until it is revoked", async ({ request }) => {
    const api = process.env.API_URL ?? "http://127.0.0.1:8100";
    await page.goto("/admin/developers");
    await expect(page.getByText("alert.opened")).toBeVisible();
    const keyName = `E2E key ${Date.now()}`; // unique, so reruns against the same database stay unambiguous
    await page.getByLabel("Key name", { exact: true }).fill(keyName);
    await page.getByLabel("Key role", { exact: true }).selectOption("viewer");
    await page.getByRole("button", { name: "Create key" }).click();
    const secret = await page.getByLabel(`New API key “${keyName}” value`).inputValue();
    expect(secret).toMatch(/^aur_[0-9a-f]{8}_/);
    const auth = { Authorization: `Bearer ${secret}` };
    expect((await request.get(`${api}/products?limit=1`, { headers: auth })).status()).toBe(200);
    expect((await request.get(`${api}/auth/me`, { headers: auth })).status()).toBe(403);
    await page.getByRole("button", { name: "Done" }).click();

    const row = page.getByRole("table", { name: "API keys" }).getByRole("row", { name: new RegExp(keyName) });
    await row.getByRole("button", { name: "Revoke" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Revoke key" }).click();
    await expect(row).toContainText("revoked");
    expect((await request.get(`${api}/products?limit=1`, { headers: auth })).status()).toBe(401);
  });

  test("theme, keyboard shortcuts and phone-width layout", async () => {
    await page.goto("/dashboard");
    await page.getByRole("button", { name: /Theme: Dark/ }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    await page.reload();
    // The boot script applies the saved theme before React loads.
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

    await page.locator("body").press("?");
    await expect(page.getByRole("dialog", { name: "Keyboard shortcuts" })).toBeVisible();
    await page.keyboard.press("Escape");
    await page.keyboard.press("g");
    await page.keyboard.press("s");
    await expect(page).toHaveURL(/\/suppliers$/);
    await page.keyboard.press("/");
    await expect(page.locator("[data-shortcut-search]")).toBeFocused();

    await page.setViewportSize({ width: 375, height: 800 });
    for (const path of ["/suppliers", "/admin/users", "/upload", "/inventory"]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await page.waitForLoadState("networkidle");
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${path} scrolls sideways at 375px`).toBeLessThanOrEqual(0);
    }
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.evaluate(() => localStorage.removeItem("aurora.theme"));
  });

  test("logout clears the session", async () => {
    await page.goto("/dashboard");
    await page.getByRole("button", { name: /sign out|log out/i }).click();
    await expect(page).toHaveURL(/\/login/);
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login/);
  });

  test.afterAll(async () => {
    await adminCtx?.close();
  });
});
