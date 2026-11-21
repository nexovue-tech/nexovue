#!/usr/bin/env bash
# The AI account's grants, read straight after `setup` and before any suite touches the account
# (AC46: a restore drops them, `start` runs `setup`, so they must be back without another step).
#
#   grants-check.sh POSTGRES_CONTAINER
#
# The functions and the account are read from the code that grants them, never written out
# here: a signature in this file would go stale when a migration widens a function (0073 did,
# and this check failed on the old name). Needs `uv` and the workspace, like the suites.
set -euo pipefail

container="${1:?usage: grants-check.sh POSTGRES_CONTAINER}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

account=$(cd "$root" && uv run --package nexovue-api python -c \
    'from nexovue_api.insight_account import INSIGHT_ACCOUNT; print(INSIGHT_ACCOUNT)')
mapfile -t functions < <(cd "$root" && uv run --package nexovue-api python -c \
    'from nexovue_api.insight_account import INSIGHT_FUNCTIONS; print(*INSIGHT_FUNCTIONS, sep="\n")')
[ -n "$account" ] && [ "${#functions[@]}" -gt 0 ] || { echo "no account or functions read from insight_account.py"; exit 1; }

# One row per function: it exists, the account may run it, and PUBLIC may not (no ACL at all
# means PUBLIC may, so the default ACL stands in for a missing one).
query=$(cat <<'SQL'
SELECT f.oid IS NOT NULL
   AND has_function_privilege(:'account', f.oid, 'EXECUTE')
   AND NOT EXISTS (SELECT 1 FROM pg_proc AS p,
                   LATERAL aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) AS a
                   WHERE p.oid = f.oid AND a.grantee = 0)
FROM (SELECT to_regprocedure(:'signature') AS oid) AS f
SQL
)

failed=0
for signature in "${functions[@]}"; do
    granted=$(printf '%s\n' "$query" | docker exec -i "$container" psql -U nexovue -d nexovue -tA \
        -v ON_ERROR_STOP=1 -v account="$account" -v signature="$signature")
    if [ "$granted" = t ]; then
        echo "granted as setup leaves it: $signature"
    else
        echo "NOT as setup leaves it (missing, not runnable by $account, or open to PUBLIC): $signature"
        failed=1
    fi
done
[ "$failed" = 0 ]
