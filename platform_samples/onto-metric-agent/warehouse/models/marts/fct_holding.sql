-- Holding-grain fact: the measure plus every conformed dimension the semantic
-- layer exposes. One row per holding, so no dimension fans the measure out.
{{ config(materialized='table') }}

select
    holding.holding_id,
    holding.account_id,
    account.customer_id,
    holding.product_id,
    holding.as_of_date,
    holding.market_value,
    holding.currency,
    customer.segment,
    account.book,
    customer.legal_entity_id,
    customer.legal_entity_l1,
    customer.legal_entity_l2,
    product.product_family,
    product.product_subfamily,
    product.product_program,
    product.asset_class
from {{ ref('stg_holding') }} as holding
join {{ ref('stg_account') }} as account
    on account.account_id = holding.account_id
join {{ ref('dim_customer') }} as customer
    on customer.customer_id = account.customer_id
join {{ ref('dim_product') }} as product
    on product.product_id = holding.product_id
