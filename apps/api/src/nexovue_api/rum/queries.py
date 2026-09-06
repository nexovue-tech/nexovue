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
