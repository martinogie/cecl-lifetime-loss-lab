-- Fails (returns rows) if a typed origination value is outside the range Freddie documents, or a
-- loan matures before its first payment. Contracts catch this in raw files; this re-checks after
-- typing, so a casting mistake in staging is caught too.
select loan_id
from {{ ref('stg_freddie__origination') }}
where credit_score not between 300 and 850
   or orig_ltv not between 1 and 998
   or orig_dti not between 0 and 65
   or orig_upb <= 0
   or maturity_ym < first_payment_ym
