# Out-of-time test: pre-registered result statement

- **Status:** approved by Martins (18 November 2026)

Written and committed **before** the out-of-time test runs (18 November 2026), so the way the result is
reported is fixed in advance, whichever way it goes. After the test, exactly one of the two statements
below is used, with the placeholders `{...}` filled from the test's MLflow run; the wording is not
changed after the result is known. Used in the model card, the validation report and the website
(Phase 7 Models & Methods page).

The rule it reports (ADR-009 Amendment 2, accepted before the test): the test **passes** when every
judged default cell (by year and by vintage, cells with at least 200 actual defaults) of the full
multi-state projection from 1 July 2022 to June 2026 is inside 0.80-1.25. Payoff cells are reported;
a payoff cell outside the bar is recorded under limitations log L-028 and does not fail the test.

---

## If it passes

> **Out-of-time test (July 2022 - June 2026), run on {test_date}: passed.** The locked models (logistic transition models
> for loans current, 30 and 60 days late; chosen on 2017-2019 under a rule written before the test)
> were scored once on four years they had never seen. Projected defaults were {projected_defaults}
> against {actual_defaults} actual ({default_ratio}); all {judged_default_cells} judged default cells
> were inside the 0.80-1.25 bar. Projected payoffs were {projected_payoffs} against {actual_payoffs}
> ({payoff_ratio}); {failing_payoff_cells} of {judged_payoff_cells} payoff cells were outside the bar
> and are recorded as a known limitation (L-028). Loans 60 days late at the start: {sixty_ratio}.
> What we expected beforehand, and how it compares: {expectation_comparison}. A pass makes the models
> eligible for promotion to champion after Martins's written review. Scenario: the economy as it
> actually happened from July 2022 to June 2026 (actual unemployment, house prices and mortgage rates),
> not a forecast. Known limitations that still apply: payoffs under-projected after the first year
> (L-028), recorded calibration cells (L-029), late defaults for loans 30 or 60 days late at the start
> (L-030) and loan-age and housing-turnover drift (L-031). Educational model on Freddie Mac conforming
> mortgages; not an audited bank reserve and not accounting advice. Test run `{test_run}`, code
> `{commit}`.

## If it fails

> **Out-of-time test (July 2022 - June 2026), run on {test_date}: failed.** The locked models (logistic transition models
> for loans current, 30 and 60 days late; chosen on 2017-2019 under a rule written before the test)
> were scored once on four years they had never seen. {failing_default_cells} of
> {judged_default_cells} judged default cells were outside the 0.80-1.25 bar: {failing_cell_list}.
> Projected defaults were {projected_defaults} against {actual_defaults} actual ({default_ratio});
> payoffs {projected_payoffs} against {actual_payoffs} ({payoff_ratio}); {failing_payoff_cells} of
> {judged_payoff_cells} payoff cells were outside the bar (L-028). Loans 60 days late at the start:
> {sixty_ratio}. What we expected beforehand,
> and how it compares: {expectation_comparison}. The models were **not** tuned, swapped or re-chosen
> on the test: they stay `@challenger`, are not promoted to champion, and the failure is recorded in
> the limitations log ({limitation_id}). Any fix is a new model version, validated on a new period
> and approved by Martins; the reserve built in Phase 4 states this result and its effect next to
> every figure. Scenario: the economy as it actually happened from July 2022 to June 2026 (actual
> unemployment, house prices and mortgage rates), not a forecast. Known limitations that still apply:
> payoffs under-projected after the first year (L-028), recorded calibration cells (L-029), late
> defaults for loans 30 or 60 days late at the start (L-030) and loan-age and housing-turnover drift
> (L-031). Educational model on Freddie Mac conforming mortgages; not an audited bank reserve and not
> accounting advice. Test run `{test_run}`, code `{commit}`.

---

## Rules for using it

- Both statements give the same numbers in the same order; neither leaves out the payoff result or the
  60-days-late group. The fail version is stated as plainly as the pass version: "failed" in the first
  line, the failing cells listed by name and ratio, no qualifier such as "narrowly" or "almost".
- Numbers are copied from the test run's `out_of_time_projection.json`, rounded as shown in the
  validation report; ratios to two decimals.
- On the website the statement appears in full next to the model results, with the date of the test
  and a link to the validation report; it is not shortened to "passed" or "failed" alone.
- Educational model on Freddie Mac conforming mortgages; not an audited reserve and not accounting
  advice.
