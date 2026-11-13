-- One row per loan: Freddie origination fields, typed, with Freddie's "Not Available" codes
-- (9999 for credit score, 999 for ratios) turned into nulls so they can't be averaged by mistake.
with source as (
    select * from {{ freddie_origination_relation() }}
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
    cast(orig_upb as double) as orig_upb,
    cast(orig_interest_rate as double) as orig_interest_rate,
    cast(orig_loan_term as integer) as orig_loan_term,
    property_state,
    occupancy_status,
    loan_purpose
from source
