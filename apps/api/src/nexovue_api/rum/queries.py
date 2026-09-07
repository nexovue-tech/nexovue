"""The ClickHouse reads behind the RUM screen.

Every statement here names `{tenant_id:String}`; the repository refuses one that
does not, and the value is bound there from the session rather than passed in.

`web_vitals_1m` carries one time column, `minute`, which is also its partition
key — unlike `spans` and `logs`, a read of this table needs no second bound on
a receive clock to prune.
"""

from __future__ import annotations

from typing import Final

# The parameter list the rollup's `AggregateFunction(quantiles(...))` was
# declared with in migration 000031. `quantilesMerge` only accepts the same one,
# so it is spelled here once and the service reads the p75 out by position.
ROLLUP_QUANTILES: Final = (0.5, 0.75, 0.95)

_QUANTILE_ARGUMENTS: Final = ", ".join(str(quantile) for quantile in ROLLUP_QUANTILES)

# One row per vital: how many readings the window holds and the distribution
# they merge to. A Core Web Vital is defined at the 75th percentile, so the
# quantile state is what is stored and the finished number is computed here.
VITAL_SUMMARY_SQL: Final = f"""
SELECT
    vital_name,
    sum(reading_count) AS readings,
    quantilesMerge({_QUANTILE_ARGUMENTS})(value_quantiles) AS quantiles
FROM web_vitals_1m
WHERE tenant_id = {{tenant_id:String}}
  AND project_id = {{project_id:String}}
  AND minute >= {{from_time:DateTime}}
  AND minute < {{to_time:DateTime}}
GROUP BY vital_name
ORDER BY vital_name
"""

# The two breakdowns below differ by one column and are still written out in
# full, for the reason `infrastructure/queries.py` gives at length:
# `tests/test_tenant_chokepoint_structure.py` reads string literals, so a
# statement whose `FROM` and whose tenant predicate live in different constants
# is no longer provably scoped from the source alone. The repetition is what
# keeps the guarantee machine-readable.
#
# Both group twice in one pass. The inner aggregation merges each
# (group, vital) distribution; the outer one ranks groups by how many readings
# arrived, so the busiest pages are the ones that survive the limit. The vitals
# come back as parallel arrays rather than as one row each because the limit
# applies to a *group*, and a row-per-vital shape cannot be limited by group.
#
# `country` is deliberately not a dimension. The column exists in the rollup but
# nothing writes it — there is no GeoIP in the system — so a geo breakdown today
# would be a single group named "".
#
# A page is `route` where the customer configured one and `url_path` where they
# did not, and the choice is made in SQL because that is where the rows are:
# `/products/1` and `/products/2` have to become one distribution, and merging
# two quantile states is something only the store can do. `is_route_template`
# comes back beside the key because the two mean different things to a reader —
# a template is "this page", a path is "this URL" — and `route != ''` already
# says which without a column of its own.

PAGE_BREAKDOWN_SQL: Final = f"""
SELECT
    group_key,
    max(is_route_template) AS is_route_template,
    groupArray(vital_name) AS vital_names,
    groupArray(readings) AS vital_readings,
    groupArray(quantiles) AS vital_quantiles
FROM (
    SELECT
        if(route != '', route, url_path) AS group_key,
        max(route != '') AS is_route_template,
        vital_name,
        sum(reading_count) AS readings,
        quantilesMerge({_QUANTILE_ARGUMENTS})(value_quantiles) AS quantiles
    FROM web_vitals_1m
    WHERE tenant_id = {{tenant_id:String}}
      AND project_id = {{project_id:String}}
      AND minute >= {{from_time:DateTime}}
      AND minute < {{to_time:DateTime}}
    GROUP BY group_key, vital_name
)
GROUP BY group_key
ORDER BY sum(readings) DESC, group_key
LIMIT {{group_limit:UInt32}}
"""

# The flag is a constant here rather than absent, so one reader handles both
# statements: a device class is a device class, never a route template.
DEVICE_BREAKDOWN_SQL: Final = f"""
SELECT
    group_key,
    max(is_route_template) AS is_route_template,
    groupArray(vital_name) AS vital_names,
    groupArray(readings) AS vital_readings,
    groupArray(quantiles) AS vital_quantiles
FROM (
    SELECT
        device_class AS group_key,
        false AS is_route_template,
        vital_name,
        sum(reading_count) AS readings,
        quantilesMerge({_QUANTILE_ARGUMENTS})(value_quantiles) AS quantiles
    FROM web_vitals_1m
    WHERE tenant_id = {{tenant_id:String}}
      AND project_id = {{project_id:String}}
      AND minute >= {{from_time:DateTime}}
      AND minute < {{to_time:DateTime}}
    GROUP BY group_key, vital_name
)
GROUP BY group_key
ORDER BY sum(readings) DESC, group_key
LIMIT {{group_limit:UInt32}}
"""
