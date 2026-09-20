-- Staging: one row per account.
select
    account_id,
    customer_id,
    status,
    book,
    open_date
from {{ source('bank', 'account') }}
