-- Staging: one row per legal entity, with its parent for hierarchy walks.
select
    legal_entity_id,
    name,
    parent_id
from {{ source('bank', 'legal_entity') }}
