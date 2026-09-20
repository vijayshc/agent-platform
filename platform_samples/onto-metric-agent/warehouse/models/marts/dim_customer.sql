-- Customer dimension with the legal-entity tree flattened to levels.
--
--   legal_entity_l1 = the root entity of the customer's branch
--   legal_entity_l2 = the customer's own entity
--
-- Filtering a level selects that entity and everything beneath it, so
-- "including subsidiaries" is one equality. Deeper org trees add l3, l4, ...
{{ config(materialized='table') }}

with recursive entity_path as (
    select
        legal_entity_id,
        parent_id,
        [name] as path
    from {{ ref('stg_legal_entity') }}

    union all

    select
        child.legal_entity_id,
        parent.parent_id,
        [parent.name] || child.path
    from entity_path as child
    join {{ ref('stg_legal_entity') }} as parent
        on parent.legal_entity_id = child.parent_id
),

resolved as (
    select legal_entity_id, path
    from entity_path
    qualify row_number() over (partition by legal_entity_id order by len(path) desc) = 1
)

select
    customer.customer_id,
    customer.name as customer_name,
    customer.segment,
    customer.cif_no,
    customer.legal_entity_id,
    resolved.path[1] as legal_entity_l1,
    resolved.path[2] as legal_entity_l2
from {{ ref('stg_customer') }} as customer
join resolved on resolved.legal_entity_id = customer.legal_entity_id
