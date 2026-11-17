-- One row per loan: Freddie origination fields, typed, with Freddie's "Not Available" codes
-- (9999 for credit score, 999 for ratios) turned into nulls so they can't be averaged by mistake.
with source as (
    select * from {{ freddie_relation('origination') }}
)

select
    loan_id,
    -- Loan ids are PYYQnXXXXXXX: YY is the origination year. The dataset starts in 1999, so 99 is
    -- 1999 and every other value is 20YY.
    case
        when substr(loan_id, 2, 2) = '99' then 1999
        else 2000 + cast(substr(loan_id, 2, 2) as integer)
    end as vintage_year,
    cast(substr(loan_id, 5, 1) as integer) as vintage_quarter,
    cast(first_payment_date as string) as first_payment_ym,
    cast(maturity_date as string) as maturity_ym,
    nullif(cast(credit_score as integer), 9999) as credit_score,
    nullif(cast(orig_ltv as integer), 999) as orig_ltv,
    nullif(cast(orig_cltv as integer), 999) as orig_cltv,
    nullif(cast(orig_dti as integer), 999) as orig_dti,
    nullif(cast(mi_pct as integer), 999) as mi_pct,
    cast(orig_upb as decimal(18, 2)) as orig_upb,
    cast(orig_interest_rate as decimal(7, 3)) as orig_interest_rate,
    cast(orig_loan_term as integer) as orig_loan_term,
    -- L-014 (decided 14 Nov 2026): loans with terms over 360 months stay in the book, flagged, and
    -- every summary and backtest is reported with and without them.
    cast(orig_loan_term as integer) > 360 as term_over_360,
    property_state,
    occupancy_status,
    loan_purpose,
    -- HARP / relief refinance (Freddie's relief refinance indicator). Every such loan has its
    -- debt-to-income missing (L-024), so the flag is a model feature of its own: missing DTI must
    -- not stand in for "relief refinance".
    coalesce(relief_refinance_indicator = 'Y', false) as relief_refinance
from source
