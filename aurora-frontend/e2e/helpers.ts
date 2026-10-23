import { expect, type Page } from "@playwright/test";

export function creds(prefix = "E2E_ADMIN") {
  const email = process.env[`${prefix}_EMAIL`];
  const password = process.env[`${prefix}_PASSWORD`];
  if (!email || !password) throw new Error(`Set ${prefix}_EMAIL and ${prefix}_PASSWORD for a local test account`);
  return { email, password };
}

export async function login(page: Page, email: string, password: string, next = "/dashboard") {
  await page.goto(`/login?next=${encodeURIComponent(next)}`);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page).toHaveURL(new RegExp(`${next.replace(/[/]/g, "\\/")}$`));
}
