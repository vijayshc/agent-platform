-- Control-event fact: one row per control evaluation.
{{ config(materialized='table') }}

select
    breach.breach_id,
    breach.account_id,
    account.customer_id,
    breach.control_id,
    control.name as control_name,
    breach.event_date,
    breach.result,
    customer.segment
from {{ ref('stg_control_breach') }} as breach
join {{ ref('stg_account') }} as account
    on account.account_id = breach.account_id
join {{ ref('dim_customer') }} as customer
    on customer.customer_id = account.customer_id
join {{ ref('stg_control_def') }} as control
    on control.control_id = breach.control_id
