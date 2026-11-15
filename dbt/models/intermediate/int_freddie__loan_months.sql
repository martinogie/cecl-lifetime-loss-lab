-- The monthly records the panel reads: each loan-month with the origination details the rules and
-- summaries need. Remaining months is worked out from the maturity date when Freddie left it empty
-- (L-013: 363 of 76 million rows in the Release 48 sample).
with months as (
    select * from {{ ref('stg_freddie__performance') }}
),

loans as (
    select
        loan_id,
        maturity_ym,
        term_over_360,
        cast(substr(maturity_ym, 1, 4) as integer) * 12
            + cast(substr(maturity_ym, 5, 2) as integer) as maturity_index
    from {{ ref('stg_freddie__origination') }}
)

select
    months.*,
    coalesce(months.remaining_months_reported, loans.maturity_index - months.period_index)
        as remaining_months,
    months.remaining_months_reported is null as remaining_months_derived,
    loans.term_over_360
from months
inner join loans on months.loan_id = loans.loan_id
