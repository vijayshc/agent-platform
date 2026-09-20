-- Staging: one row per customer.
select
    customer_id,
    name,
    segment,
    legal_entity_id,
    cif_no
from {{ source('bank', 'customer') }}
