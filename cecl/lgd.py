"""LGD: loss given default, from Freddie Mac's actual loss fields (Phase 3; roadmap section 5.2).

**One row per default, with the same definition as PD** (ADR-002: first default, 90+ days late or a
loss event). Each default is followed to how the loan ended:
- a loss event (zero-balance code 02 third-party sale, 03 short sale, 09 REO disposition, 15 note
  sale) with Freddie's loss fields filled in: severity = actual loss / balance at default;
- paid off in full later, or current again at the end of the data: **no loss** (severity 0). These are
  most of the defaults that a loss-events-only table would leave out, which would overstate LGD;
- sold as a re-performing loan or bought back as a defect (codes 16, 96: Freddie reports no loss),
  still delinquent at the end of the data, or a loss not yet reported: **unresolved, left out** and
  counted. Leaving out codes 16 and 96 (mostly loans that had recovered) leans conservative.

Freddie's `actual_loss` in this release is positive for a loss (recoveries and sale proceeds are
stored negative, expenses positive: see the performance contract); a gain on sale counts as no loss.

**Two steps** (bank-standard for mortgages, where many defaults lose nothing because the borrower
recovers or the house covers the debt):
1. the chance of any loss; 2. the size of the loss when there is one. LGD = 1 x 2.

**Inputs**, all knowable at default so the CECL engine can supply them month by month:
- LTV at default: balance / (original value x state house-price index change since origination). This
  is what ties LGD to house prices: a 30% price fall in a stress scenario raises LTV and so LGD;
- the state house-price change over the 12 months before default (a falling market sells lower);
- mortgage insurance cover (MI pays part of the loss);
- judicial foreclosure state (longer and costlier resolution);
- original balance (fixed costs weigh more on small loans), credit score, investor property.
Both steps are gradient-boosted trees with monotone constraints in the business direction
(LTV up -> loss up; MI up -> loss down; prices rising -> loss down), so the model cannot learn a
backwards relationship from noise. The time to resolution and the price change after default are not
inputs (not known when the reserve is set); they are used only to explain results.

Educational model on Freddie Mac conforming mortgages; not an audited reserve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

LOSS_CODES = ("02", "03", "09", "15")
# States where foreclosure goes through the courts (the usual list used in mortgage research); longer
# timelines mean more interest, taxes and upkeep before the sale.
JUDICIAL = frozenset("CT DE FL HI IL IN IA KS KY LA ME NE NJ NM NY ND OH OK PA SC SD VT WI".split())
# Severity is bounded for fitting (not for reporting): a gain on sale counts as no loss (0), because a
# reserve does not let one loan's gain offset another's loss; and a few tiny balances with large fixed
# costs give severities of 100x the balance, which would dominate a squared-error fit, so the top is
# capped at 150%. The number capped is reported. Proposed 19 Nov 2026; Martins decides capping for LGD
# in Phase 3 (roadmap_v2_changes.md).
SEVERITY_CAP = (0.0, 1.5)
FEATURES = ("ltv_default", "hpi_change_12", "mi_pct", "judicial", "log_orig_upb", "credit_score", "investor")
# +1: higher input -> more loss; -1: less loss; 0: free
DIRECTIONS = {"ltv_default": 1, "hpi_change_12": -1, "mi_pct": -1, "judicial": 1, "log_orig_upb": -1,
              "credit_score": 0, "investor": 0}  # fmt: skip
TRAIN_LAST = "2016"  # defaults up to 2016 train; 2017 on are the check (the PD split, ADR-009 A)


def event_sql(perf: str, orig: str, panel: str, state_hpi: str) -> str:
    """DuckDB/Spark SQL: one row per first default (ADR-002, from the panel) with its inputs at default,
    how it ended (`outcome`) and its severity (null when unresolved). `state_hpi` is a relation with
    columns state, ym (YYYYMM), hpi."""
    codes = ", ".join(f"'{c}'" for c in LOSS_CODES)
    return f"""
with d as (
    select loan_id, period_ym as default_ym, cast(current_upb as double) as upb_at_default
    from {panel} where status = 'defaulted'
),
fin as (
    select loan_id, max(period) as last_period,
           arg_max(zero_balance_code, period) as code, arg_max(zero_balance_date, period) as disp_ym,
           arg_max(delinquency_status, period) as last_dq,
           cast(arg_max(actual_loss, period) as double) as loss,
           cast(arg_max(zero_balance_removal_upb, period) as double) as removal_upb
    from {perf} where loan_id in (select loan_id from d) group by loan_id
),
o as (
    select loan_id, property_state as state, cast(orig_upb as double) as orig_upb, cast(orig_ltv as double) as orig_ltv,
           cast(coalesce(mi_pct, 0) as double) as mi_pct, cast(credit_score as double) as credit_score,
           occupancy_status, first_payment_date
    from {orig}
),
r as (
    select d.loan_id, d.default_ym, fin.code, fin.disp_ym,
           coalesce(nullif(d.upb_at_default, 0), fin.removal_upb) as default_upb,
           case when fin.code in ({codes}) and fin.loss is not null then 'loss event'
                when fin.code = '01' then 'paid off'
                when fin.code is null and fin.last_dq = '00' then 'current again'
                when fin.code in ('16', '96') then 'unresolved: sold reperforming or defect'
                when fin.code in ({codes}) then 'unresolved: loss not yet reported'
                else 'unresolved: still delinquent' end as outcome,
           fin.loss
    from d join fin on fin.loan_id = d.loan_id
)
select r.loan_id, r.code, r.disp_ym, r.default_ym, r.outcome, o.state, r.default_upb, r.loss,
       case when r.outcome = 'loss event' then r.loss / r.default_upb
            when r.outcome in ('paid off', 'current again') then 0.0 end as severity,
       100.0 * r.default_upb / (o.orig_upb / (o.orig_ltv / 100.0) * hd.hpi / ho.hpi) as ltv_default,
       hd.hpi / h12.hpi - 1.0 as hpi_change_12,
       o.mi_pct, case when o.state in ({", ".join(f"'{s}'" for s in sorted(JUDICIAL))}) then 1.0 else 0.0 end as judicial,
       ln(o.orig_upb) as log_orig_upb, o.credit_score,
       case when o.occupancy_status = 'I' then 1.0 else 0.0 end as investor
from r
join o on o.loan_id = r.loan_id
left join {state_hpi} ho on ho.state = o.state
     and ho.ym = strftime(strptime(o.first_payment_date, '%Y%m') - interval 1 month, '%Y%m')
left join {state_hpi} hd on hd.state = o.state and hd.ym = r.default_ym
left join {state_hpi} h12 on h12.state = o.state
     and h12.ym = strftime(strptime(r.default_ym, '%Y%m') - interval 12 month, '%Y%m')
where r.default_upb > 0
"""


def matrix(frame: Any) -> np.ndarray:
    return np.column_stack([frame[f].to_numpy(dtype=float) for f in FEATURES])


@dataclass
class TwoStepLGD:
    """P(any loss) x E[severity | loss], both monotone gradient-boosted trees."""

    max_depth: int = 3
    max_iter: int = 300
    learning_rate: float = 0.05
    min_samples_leaf: int = 50
    step1: Any = field(default=None, repr=False)
    step2: Any = field(default=None, repr=False)

    def _cst(self) -> list[int]:
        return [DIRECTIONS[f] for f in FEATURES]

    def fit(self, frame: Any) -> TwoStepLGD:
        from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

        X = matrix(frame)
        sev = np.clip(frame["severity"].to_numpy(dtype=float), *SEVERITY_CAP)
        loss = sev > 0
        kw = {"max_depth": self.max_depth, "max_iter": self.max_iter, "learning_rate": self.learning_rate,
              "min_samples_leaf": self.min_samples_leaf, "monotonic_cst": self._cst(), "random_state": 7}  # fmt: skip
        self.step1 = HistGradientBoostingClassifier(**kw).fit(X, loss.astype(int))
        self.step2 = HistGradientBoostingRegressor(**kw).fit(X[loss], sev[loss])
        return self

    def p_loss(self, X: np.ndarray) -> np.ndarray:
        return np.asarray(self.step1.predict_proba(X)[:, 1])

    def severity(self, X: np.ndarray) -> np.ndarray:
        return np.clip(np.asarray(self.step2.predict(X)), 0.0, SEVERITY_CAP[1])

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Expected LGD (share of the balance at default lost)."""
        return self.p_loss(X) * self.severity(X)


def evaluate(model: TwoStepLGD, frame: Any, groups: dict[str, Any]) -> dict[str, Any]:
    """Predicted vs actual loss in money (balance-weighted, as a reserve is) overall and by group, plus
    the share of events with any loss, predicted vs actual."""
    X = matrix(frame)
    upb = frame["default_upb"].to_numpy(dtype=float)
    sev = np.clip(frame["severity"].to_numpy(dtype=float), *SEVERITY_CAP)
    pred = model.predict(X)
    out: dict[str, Any] = {
        "events": int(len(frame)),
        "actual_lgd": float((sev * upb).sum() / upb.sum()),
        "predicted_lgd": float((pred * upb).sum() / upb.sum()),
        "any_loss_actual": float((sev > 0).mean()),
        "any_loss_predicted": float(model.p_loss(X).mean()),
        "groups": {},
    }
    out["ratio"] = out["predicted_lgd"] / out["actual_lgd"]
    for name, g in groups.items():
        g = np.asarray(g)
        rows = []
        for k in sorted(set(g.tolist())):
            m = g == k
            a, p = (
                float((sev[m] * upb[m]).sum() / upb[m].sum()),
                float((pred[m] * upb[m]).sum() / upb[m].sum()),
            )
            rows.append(
                {
                    "group": str(k),
                    "events": int(m.sum()),
                    "actual": a,
                    "predicted": p,
                    "ratio": p / a if a else float("nan"),
                }
            )
        out["groups"][name] = rows
    return out


def downturn(
    model: TwoStepLGD, frame: Any, shocks: tuple[float, ...] = (0.0, -0.1, -0.2, -0.3)
) -> list[dict[str, float]]:
    """LGD of the same defaults if house prices were `shock` lower: LTV rises by 1 / (1 + shock) and the
    12-month price change falls by the shock (the roadmap's "downturn behaviour shown")."""
    rows = []
    upb = frame["default_upb"].to_numpy(dtype=float)
    for s in shocks:
        f = frame.copy()
        f["ltv_default"] = f["ltv_default"] / (1.0 + s)
        f["hpi_change_12"] = (1.0 + f["hpi_change_12"]) * (1.0 + s) - 1.0
        p = model.predict(matrix(f))
        rows.append({"hpi_shock": s, "lgd": float((p * upb).sum() / upb.sum())})
    return rows
