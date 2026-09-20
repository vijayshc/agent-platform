-- Staging: one row per holding snapshot.
select
    holding_id,
    account_id,
    product_id,
    as_of_date,
    market_value,
    currency
from {{ source('bank', 'holding') }}
