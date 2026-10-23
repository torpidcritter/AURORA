import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { creds, login } from "./helpers";

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
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Saved." })).toBeVisible();
    await expect(page.getByRole("definition").filter({ hasText: /^9 days$/ })).toBeVisible();
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

  test("assistant answers from real data (built-in mode without an API key)", async () => {
    await page.goto("/assistant");
    await page.getByRole("button", { name: "Which products are at risk of stocking out?" }).click();
    await expect(page.getByText(/Stockout risk: \d+ critical/)).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText(/Built-in summary|Claude/).first()).toBeVisible();
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
