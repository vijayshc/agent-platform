-- Product dimension with the taxonomy and the cross-cutting program classification
-- flattened onto it.
--
--   product_family    = the root of the product's taxonomy branch
--   product_subfamily = the product's own node in that branch
--   product_program   = a cross-cutting business classification that does not follow
--                       the taxonomy (reference data from the product_program seed)
--
-- All three are ordinary columns, so a family, a sub-family, "family minus
-- sub-family", or a cross-cutting program is a dimension filter. Deeper taxonomies
-- or further classifications extend here, not in any tool or agent.
{{ config(materialized='table') }}

with recursive branch as (
    select
        product_id,
        name,
        parent_product_id,
        asset_class,
        name as product_family
    from {{ ref('stg_product') }}
    where parent_product_id is null

    union all

    select
        child.product_id,
        child.name,
        child.parent_product_id,
        child.asset_class,
        branch.product_family
    from {{ ref('stg_product') }} as child
    join branch on child.parent_product_id = branch.product_id
)

select
    branch.product_id,
    branch.name as product_name,
    branch.asset_class,
    branch.product_family,
    branch.name as product_subfamily,
    program.product_program
from branch
left join {{ ref('product_program') }} as program
    on program.product_id = branch.product_id
