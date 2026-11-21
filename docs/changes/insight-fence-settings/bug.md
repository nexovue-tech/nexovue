# Bug: The insight-fence harness hand-wrote the install's settings and missed the object store's

## Reported behavior

CI run 44899530725, job `insight-fence`, its first run. `just insight-fence` built both images and the
kit, passed "checking this machine", then at "pulling nexovue 0.0.0-fence.…" stopped:

```
error while interpolating x-object-store-env.S3_BUCKET: required variable S3_BUCKET is missing a value: set S3_BUCKET
```

## Reproduction

The harness's settings file, run through the command the installer runs at "pulling":

```
docker compose --project-name nexovue-fence --env-file <the harness's settings> -f deploy/compose/nexovue.yaml config --quiet
error while interpolating x-object-store-env.S3_ACCESS_KEY_ID: required variable S3_ACCESS_KEY_ID is missing a value
error while interpolating x-object-store-env.S3_BUCKET: required variable S3_BUCKET is missing a value
error while interpolating x-object-store-env.S3_SECRET_ACCESS_KEY: required variable S3_SECRET_ACCESS_KEY is missing a value
```

Compose reports every missing name, and CI showed one line of them. Not the object store alone: the
settings also lacked the generated secrets (`POSTGRES_*`, `CLICKHOUSE_*`, the sealing and masking keys).

## Root cause

`fence.sh` called `nexovue install --settings FILE` with a file it wrote itself. `--settings` is the
path for "a machine replacing another" (`deploy/nexovue`, `cmd_install`): it installs the file whole and
skips `write_secrets`, so nothing the file omits is ever generated. The harness's comment said
"write_secrets fills in every secret", which was false for that path. A real install takes its answers
from the environment or prompts and writes every secret and default itself, so a list in the harness
could only drift from the compose file.

Two later steps would also have failed, found by reading `fence.sh` against `deploy/nexovue`:

- `nexovue stop` is `compose stop`, which keeps the containers; Docker refuses `docker volume rm` on a
  volume a stopped container still names. Shown with a throwaway project: `stop`, then `volume rm`
  fails with "volume is in use"; after `down`, it succeeds.
- The snapshot name came from `nexovue backups | grep -E '^20.*Z$'`, but that command indents each
  snapshot by two spaces, so the pattern never matched.

## Specification impact

None. `deploy/nexovue` now honours `NEXOVUE_PROJECT` from the environment on a fresh install, like
`NEXOVUE_LAYOUT`; before, only `--settings` or hand-editing could name a second project. Header comment
updated.

## Regression test

The harness is the test: it now cannot lack a variable the installer generates. No separate unit test:
the installer needs Docker and 12 GiB to run whole. The proof below runs the real `cmd_install` with
only the machine checks, `start` and the backup timer stubbed.

## Fix plan and validation

`fence.sh` installs through the installer's own path (answers in the environment, no `--settings`),
tears containers down before removing the volume, and finds the snapshot from the backups directory.

Before (original installer, harness's `--settings` file, real `compose pull`): the S3 errors above.
After (`fence.sh`'s own export lines, real `cmd_install`): settings hold all 32 keys, `config --quiet`
exits 0 with the file alone, and `compose pull` proceeds to pulling the third-party images (stopped by
Docker Hub's 429 on this machine, not by Compose).

## Status

Implemented, not yet run whole. Still unproved until GitHub runs it: the image build and kit steps
beyond what CI already passed, `up --wait` of every service, the live suites against the install, the
backup and restore, the AI account after the restore. `kit.sh` has the same kind of hand-written list
(`images.env`, for `--images`); it works for its job today and is left alone.
