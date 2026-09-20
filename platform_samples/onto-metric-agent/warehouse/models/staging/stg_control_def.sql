-- Staging: one row per control definition.
select
    control_id,
    name
from {{ source('bank', 'control_def') }}
