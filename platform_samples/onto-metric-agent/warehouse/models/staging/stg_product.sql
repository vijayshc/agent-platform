-- Staging: one row per product, with its parent for the taxonomy walk.
select
    product_id,
    name,
    parent_product_id,
    asset_class
from {{ source('bank', 'product') }}
