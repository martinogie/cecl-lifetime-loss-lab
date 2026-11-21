import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const B = "/cecl";
const PAGES = [
  ["overview", `${B}/`],
  ["explorer", `${B}/explorer/`],
  ["scenarios", `${B}/scenarios/`],
  ["disclosures", `${B}/disclosures/`],
  ["methods", `${B}/methods/`],
  ["pipeline", `${B}/pipeline/`],
] as const;
const WIDTHS = [1440, 375];

async function settle(page: Page) {
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[data-asof]")).not.toHaveAttribute("aria-busy");
  await expect(page.locator("[data-asof]")).toContainText("Reserve date 30 Jun 2026");
}

for (const [name, path] of PAGES) {
  for (const width of WIDTHS) {
    test(`${name} at ${width}px: renders, no errors, no sideways scroll, accessible`, async ({ page }) => {
      const errors: string[] = [];
      const external: string[] = [];
      page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
      page.on("pageerror", (e) => errors.push(e.message));
      page.on("request", (r) => { if (!r.url().startsWith("http://localhost:4321")) external.push(r.url()); });
      await page.setViewportSize({ width, height: 900 });
      await page.goto(path);
      await settle(page);
      await expect(page.locator("h1:visible").first()).toBeVisible();
      expect(errors, "console errors").toEqual([]);
      expect(external, "requests to other sites (no tracking, no CDNs)").toEqual([]);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, "horizontal overflow in px").toBeLessThanOrEqual(0);
      expect(await page.context().cookies(), "no cookies").toEqual([]);
      const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
      const serious = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(serious.map((v) => `${v.id}: ${v.nodes.length}`), "serious accessibility violations").toEqual([]);
      // every page states the model status and the educational framing (Martins's rule: on every output)
      await expect(page.locator("text=Educational model").first()).toBeAttached();
      await expect(page.locator(".site-footer")).toContainText("© 2026 Martins Ogiekpolor. All rights reserved.");
      await expect(page.locator("[data-home]")).toHaveAttribute("href", "https://martinsogiekpolor.com/");
      await expect(page.locator("[data-home]")).toContainText("Home");
      await expect(page.locator("body")).not.toContainText(/AI-assisted|No tracking, no cookies/);
      await page.screenshot({ path: `../docs/images/site/${name}-${width}.png`, fullPage: true });
    });
  }
}

test("overview: key figures and the three quarter-ends come from the data", async ({ page }) => {
  await page.goto(`${B}/`);
  await settle(page);
  await expect(page.locator("[data-figures]")).toContainText("$297.5m");
  await expect(page.locator("[data-dates] tr")).toHaveCount(3);
  await expect(page.locator("[data-status]")).toContainText("failed its holdout");
});

test("loss explorer: switching the cut and the economy redraws the table", async ({ page }) => {
  await page.goto(`${B}/explorer/`);
  await settle(page);
  await page.getByRole("button", { name: "Credit score" }).click();
  await expect(page.getByRole("button", { name: "Credit score" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("[data-rows] th").first()).toHaveText("620-679");
  await page.getByRole("button", { name: "Severely adverse" }).click();
  await expect(page.getByRole("button", { name: "Severely adverse" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("[data-rows] tr")).toHaveCount(4);
});

test("scenario lab: scenario toggle, shocks and sensitivity", async ({ page }) => {
  await page.goto(`${B}/scenarios/`);
  await settle(page);
  await expect(page.locator("[data-fed-rows] tr")).toHaveCount(12);
  await page.getByRole("button", { name: "Baseline", exact: true }).click();
  await expect(page.locator("[data-fed-summary]")).toContainText("Baseline:");
  await expect(page.locator("[data-shock-bars] .hbar-row")).toHaveCount(11);
  await expect(page.locator("[data-tornado] .tornado-row").first()).toContainText("Scenario weights");
});

test("disclosures: the rollforward reconciles to the cent in both quarters", async ({ page }) => {
  await page.goto(`${B}/disclosures/`);
  await settle(page);
  await expect(page.locator("[data-rf-note]")).toContainText("$297,497,504.04");
  await page.getByRole("button", { name: "Q1 2026" }).click();
  await expect(page.locator("[data-rf-note]")).toContainText("$290,087,623.96");
  await expect(page.locator("[data-rf-note]")).toContainText("Reconciles: yes");
});

test("indexable pages (the router marks only the preview hosts noindex) and a real 404 page", async ({ page }) => {
  await page.goto(`${B}/`);
  await expect(page.locator('meta[name="robots"]')).toHaveCount(0);
  const res = await page.goto(`${B}/no-such-page/`);
  expect(res?.status()).toBe(404);
  await expect(page.locator("h1")).toHaveText("This page does not exist");
});

test("data files: aggregates only, no identifiers or loan ids", async () => {
  const dir = "public/data";
  for (const f of readdirSync(dir)) {
    const text = readFileSync(join(dir, f), "utf-8");
    expect(text, `${f}: loan ids`).not.toMatch(/\b[FA]\d{2}Q[1-4]\d{7}\b/);
    expect(text, `${f}: emails`).not.toMatch(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}/);
    expect(text, `${f}: IP addresses`).not.toMatch(/\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/);
    expect(text, `${f}: workspace hosts`).not.toMatch(/azuredatabricks|cloud\.databricks|ocid1\./);
    const data = JSON.parse(text);
    expect(data.dataAsOf.reserveDate, `${f}: reserve date`).toBe("2026-06-30");
    expect(data.status.framing, `${f}: framing`).toContain("Educational model");
  }
});
