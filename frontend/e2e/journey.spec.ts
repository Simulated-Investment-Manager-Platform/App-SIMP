import { expect, test } from "@playwright/test";

/** Spec journey: submit run -> watch progress -> open TCA -> drill to a fill. */
test("submit a run, watch it finish, open TCA and drill to a fill", async ({ page }) => {
  await page.goto("/runs/new");
  await page.getByRole("button", { name: /Phase 1 random order generator/ }).click();
  for (let i = 0; i < 6; i++) await page.getByRole("button", { name: /^Next/ }).click();
  await expect(page.getByRole("heading", { name: "Data-availability check" })).toBeVisible();
  await expect(page.getByText(/valid, hash/)).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("submit-run").click();

  await page.waitForURL(/\/runs\/[0-9A-Z]{26}$/);
  const runId = page.url().split("/").pop()!;
  await expect(page.getByText("Simulate", { exact: true })).toBeVisible();
  await expect(page.getByText("DONE").first()).toBeVisible({ timeout: 200_000 });
  await expect(page.getByText(/passed · lineage 100%/)).toBeVisible();

  await page.locator("main").getByRole("link", { name: "TCA" }).click();
  await expect(page).toHaveURL(new RegExp(`run=${runId}`));
  await expect(page.getByText("Cost by size against liquidity")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Weighted cost", { exact: true })).toBeVisible();

  await page.goto(`/blotter?run=${runId}`);
  const row = page.getByTestId("pm-row").first();
  await expect(row).toBeVisible({ timeout: 60_000 });
  await row.getByRole("button", { name: "Expand trader orders" }).click();
  const trader = page.getByTestId("trader-row").first();
  await expect(trader).toBeVisible();
  await trader.getByRole("button", { name: "Expand child orders" }).click();
  await expect(page.getByTestId("fill-row").first()).toBeVisible({ timeout: 30_000 });

  await row.click();
  await expect(page.getByText("Implementation shortfall (bps)")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("heading", { name: "Timeline" })).toBeVisible();
});
