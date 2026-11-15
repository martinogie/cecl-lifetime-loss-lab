-- Fails (returns rows) if any loan has two records for the same month. The panel needs exactly one
-- record per loan per month (cecl/panel.py raises on duplicates).
select loan_id, period_index, count(*) as records
from {{ ref('int_freddie__loan_months') }}
group by loan_id, period_index
having count(*) > 1
