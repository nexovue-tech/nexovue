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
- Shellcheck (pinned image, `lint-deploy`'s file list and `fence.sh`) clean.

## Next action

Founder reviews the diff; after it is on the branch, push and read the `insight-fence` run. F3's
status (T3.11 "Not yet run") stays true until that run passes, and is not changed here.

## Open decisions

None.

## Blocked

Nothing.

## Risks

Everything after the install has never run (see `bug.md`, *Status*).

## Validation

See `bug.md`. Not run: the whole harness, any image build.

## Specification impact

None.

## Documentation impact

`deploy/nexovue` header lists `NEXOVUE_PROJECT`.

## Last updated

2026-11-21
