# Website plan (Phase 7, 20 November 2026)

CECL Lifetime Loss Lab's public site, the second project in the finance sector of the portfolio. It
follows the Bank Health site and the portfolio-site README ("How to add a project") step by step. Nothing
is published, deployed or pushed to `main` without Martins's approval.

## Where it lives

```
finance.martinsogiekpolor.com  ──►  Worker "sector-router-finance"  (portfolio-site, live since 7 Nov 2026)
   /cecl/...                         → Pages project "cecl"  (this repo, site/, built with base /cecl)
   /cecl/data/*.json                 → the same Pages project (data files ship in the build)
```

## The four steps (portfolio-site README)

| Step | What | Status |
|---|---|---|
| 1 | Build the site with base `/cecl`; pin the design (design-v1.0.1, Finance accent) with `pin-design.mjs`; `check-design.mjs` in CI | **done** (`site/`, `.github/workflows/site.yml`) |
| 2 | Create the Pages project `cecl` and deploy the build output to its root | **needs Martins's approval** (Cloudflare dashboard or a deploy workflow with `CLOUDFLARE_API_TOKEN`; Martins adds the secret) |
| 3 | One line in `workers/sector-router/src/finance.js` (`cecl`), `CECL_ORIGIN` in `wrangler.finance.toml`, router tests; deploy the Worker | **code done** on portfolio-site branch `feature/cecl-project`; **the deploy needs Martins's approval** (Actions → Deploy) |
| 4 | The card in `site/src/data/portfolio.json` (wording from this repo's README), so it appears on the home page and the finance lobby; deploy the portfolio site | **code done** on the same branch; **the deploy needs Martins's approval** |

## Pages (roadmap Phase 7)

| Page | Data | What it shows |
|---|---|---|
| `/` Overview | `overview.json` | The allowance at 30 June 2026, the three scenarios, three quarter-ends, the model status, "Compared with real banks" (FFIEC peer range and Freddie Mac's ratio) |
| `/explorer/` Loss explorer | `explorer.json` | Reserve by vintage, credit score, loan-to-value and state, under each economy (groups under 50 loans folded into "Other") |
| `/scenarios/` Scenario lab | `scenarios.json` | The Fed's scenarios quarter by quarter, the custom shocks, the sensitivity ranking, the DFAST benchmark |
| `/disclosures/` Disclosures | `disclosures.json` | Rollforwards to the cent (Q1, Q2 2026), bridges, credit quality and write-offs by origination year |
| `/methods/` Methods | `methods.json`, `overview.json` | The models, PD x LGD x EAD, the exams (recorded and corrected), the Phase 6 backtests, limitations |
| `/pipeline/` Pipeline | `pipeline.json` | From files to the allowance, the controls, the tools |

Every page: the model status (v2 challenger, holdout failed; L-039; Option B), "Educational model, not an
audited reserve" in the header strip, 375 px with no sideways scroll, WCAG AA, a text alternative for every
chart, no tracking, no cookies, no requests to other sites. Footer: the copyright line (the portfolio's
current rule: no AI-assisted or no-tracking lines in the footer).

## Data

`site/scripts/export_site_data.py` writes the six files from the committed aggregates (Phases 4-6) plus the
reserve by loan group (sums only). Schemas: `site/data/schemas/files.schema.json`; CI checks every file and
that no file holds a loan id, email, IP address or workspace host.

**Refresh (proposal for Martins):** the data changes once a quarter, after the close. Simplest: re-export and
redeploy the `cecl` Pages project (one build a quarter, far inside the free tier). Alternative, as Bank
Health: a private R2 bucket with a `current.json` pointer, read by the router (needs an R2 token and a
router change). Publishing in either form runs only from `CECL_ENV=prod` and after every check of the
close passes.
