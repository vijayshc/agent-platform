-- Staging: one row per control evaluation.
select
    breach_id,
    account_id,
    control_id,
    event_date,
    result
from {{ source('bank', 'control_breach') }}
