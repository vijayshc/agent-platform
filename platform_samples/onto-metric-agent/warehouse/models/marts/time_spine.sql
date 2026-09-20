-- MetricFlow time spine: one row per day, so time dimensions aggregate and
-- constrain at day granularity.
{{ config(materialized='table') }}

select cast(range as date) as date_day
from range(date '2025-01-01', date '2027-01-01', interval 1 day)
