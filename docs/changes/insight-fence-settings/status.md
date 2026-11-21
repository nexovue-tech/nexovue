# Status: Insight-fence settings

## Lifecycle status

Implemented, awaiting the first full CI run.

## Current task

None.

## Completed

- Reproduced the S3 error against the harness's settings with the installer's own `compose` command.
- `fence.sh` installs through `nexovue install` with answers in the environment, so every secret and
  default is generated; `deploy/nexovue` takes `NEXOVUE_PROJECT` from the environment.
- Two later defects fixed from reading: volume removal after `stop`, and the snapshot name.
- Shellcheck (pinned image, `lint-deploy`'s file list, `fence.sh`, `grants-check.sh`) clean.
- CI run 44936883096 (install, AC42, backup/restore/verify all passed) failed at "AC46: setup granted
  again": `function "insight_claim_jobs(text,integer,integer)" does not exist`. Cause: the grants check
  hard-wrote F3's signature; migration 0073 replaced the function with `(text, text[], integer, integer)`.
  Fix: `tools/insight-fence/grants-check.sh` reads the account and every function from
  `insight_account.py` (`INSIGHT_ACCOUNT`, `INSIGHT_FUNCTIONS`), so all six (F4's two chat functions
  too, which the old check never looked at) must exist, be runnable by the account and closed to PUBLIC;
  `fence.sh` calls it. Proved on a Postgres 18 + pgvector container migrated to head by the real
  `nexovue_api.setup`: the old query fails with the same error; the new script passes, fails (rc 1)
  after the grants are dropped as a restore drops them, fails when a function is opened to PUBLIC,
  and passes again after `setup grants`. The harness's account and grants suites (48 tests) pass there.
- Other drifts looked for: the three suites' table, column and function lists match head (they pass);
  the network suite's service names and ports match the compose files. None found beyond the above.

## Next action

Founder reviews the diff; after it is on the branch, push and read the `insight-fence` run. F3's
status (T3.11 "Not yet run") stays true until that run passes, and is not changed here.

## Open decisions

None.

## Blocked

Nothing.

## Risks

Everything after AC46's restore has never run on GitHub (see `bug.md`, *Status*); the network suite was not run here (needs the installed kit).

## Validation

See `bug.md` and *Completed*. Not run here: the whole harness (preflight wants 40 GiB), any image build, the `insight_fence` network suite.

## Specification impact

None.

## Documentation impact

`deploy/nexovue` header lists `NEXOVUE_PROJECT`.

## Last updated

2026-11-21
