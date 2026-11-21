# Website (Phase 7)

The public site for the CECL Lifetime Loss Lab, served at `finance.martinsogiekpolor.com/cecl` (not live
yet). Six pages: Overview (with "Compared with real banks"), Loss explorer, Scenario lab, Disclosures,
Methods, Pipeline. Plan and launch steps: [PLAN.md](PLAN.md).

- **Astro**, static output, built with base `/cecl`, the portfolio design system pinned in `src/design/`
  (design-v1.0.1, Finance accent; CI runs `check-design.mjs`). Same toolchain as Bank Health's site:
  Node 26.13.0, npm 11.20.0 (`npm ci` only).
- Pages fetch their numbers at runtime from `/cecl/data/*.json`, written by `scripts/export_site_data.py`
  from the project's aggregate results (no loan-level data; groups under 50 loans folded into "Other").
  Schemas: `data/schemas/files.schema.json`.
- No tracking, no cookies, no requests to other sites. Every page states the model status and that this is
  an educational model, not an audited reserve.
- **Publishing** runs only from `CECL_ENV=prod`, after every check of the quarterly close passes, and
  nothing is deployed without Martins's approval.

## Run it

From the repository root: `CECL_ENV=local python site/scripts/export_site_data.py` (refresh the data). Then
from `site/`: `npm ci`, `npm run build`, `npx playwright test` (17 tests: every page at 1440 and 375 px,
console errors, outside requests, cookies, axe, sideways scroll, the page features, no identifiers in the data).
