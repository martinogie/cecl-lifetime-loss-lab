-- One row per loan per month: Freddie's monthly performance fields, typed. Codes stay as Freddie
-- wrote them (the panel in cecl/panel.py applies ADR-002 to them); "Not Available" values become
-- nulls; YYYYMM months also get a plain month number, so months can be subtracted.
with source as (
    select * from {{ freddie_relation('performance') }}
)

select
    loan_id,
    case
        when substr(loan_id, 2, 2) = '99' then 1999
        else 2000 + cast(substr(loan_id, 2, 2) as integer)
    end as vintage_year,
    cast(period as string) as period_ym,
    cast(substr(cast(period as string), 1, 4) as integer) * 12
        + cast(substr(cast(period as string), 5, 2) as integer) as period_index,
    cast(current_upb as decimal(18, 2)) as current_upb,
    delinquency_status,
    cast(loan_age as integer) as loan_age,
    cast(remaining_months as integer) as remaining_months_reported,
    modification_flag,
    zero_balance_code,
    cast(zero_balance_date as string) as zero_balance_ym,
    cast(defect_settlement_date as string) as defect_settlement_ym,
    cast(current_interest_rate as decimal(7, 3)) as current_interest_rate,
    payment_deferral_flag,
    borrower_assistance_status,
    delinquency_due_to_disaster,
    nullif(cast(eltv as integer), 999) as eltv,
    -- Loss fields (populated at disposition only; used by LGD in Phase 3)
    cast(net_sale_proceeds as decimal(18, 2)) as net_sale_proceeds,
    cast(mi_recoveries as decimal(18, 2)) as mi_recoveries,
    cast(non_mi_recoveries as decimal(18, 2)) as non_mi_recoveries,
    cast(total_expenses as decimal(18, 2)) as total_expenses,
    cast(actual_loss as decimal(18, 2)) as actual_loss,
    cast(zero_balance_removal_upb as decimal(18, 2)) as zero_balance_removal_upb,
    cast(delinquent_accrued_interest as decimal(18, 2)) as delinquent_accrued_interest
from source
