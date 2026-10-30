# Status: personal-data

## Lifecycle status

Built and committed (305269c)

## Current task

None.

## Completed

- `spec.md`, approved 2026-10-11.
- Built 2026-10-11 as T21 of `docs/changes/remaining-recommendations/plan.md`:
  `GET /profile/data.json`, `GET`/`POST /profile/deletion` (`profile/personal_data.py`,
  `profile/queries.py`); one scrub for leaving and for an admin's removal (`members/service.py::scrub`);
  the *Your data* section on Settings → Account; the *Your account was deleted* sign-in notice; the audit
  log's wording for both.

## Next action

The founder says "commit".

## Open decisions

None.

## Blocked

Nothing.

## Risks

- **The export holds addresses the person typed.** Invitations they sent, and their own audit entries
  (an invite's `detail.email`), name the people they invited. Kept as the record of their own acts; the
  spec's "never another person's data" is read as never another person's *account* data. The founder's
  call if that should change.
- **The scrub replaces exact values only.** An address or name an operator typed into free text (a plan
  grant's reason, say) is not found. Every place the API writes a person into an audit entry is a value
  of its own, and those are all replaced.
- **Telemetry is not searched.** A person's address inside the organization's own logs or spans is the
  organization's data (out of scope); masking is what keeps addresses out of it.

## Validation

- On dev over HTTP: the sole owner refused (409) and told so by `GET /profile/deletion`; the file
  downloaded as an attachment; a member's wrong password refused (422), the right one deleting them
  (204, cookie cleared), after which `/auth/me` and sign-in both answer 401; the audit log holding their
  id and neither their name nor address. The section seen in the console as the sole owner.

- `apps/api/tests/test_personal_data_live.py` (7), against Postgres, seen red before the code existed;
  the scrub's two tests seen red again with the audit rewrite disabled.
- `test_members_live.py` and `test_audit_read_live.py`: the removal tests changed to the new rule (name "Former member", no address
  in the entry).
- `test_authorization_coverage.py` (deletion exempt with its reason), `test_api_conventions.py` (`.json`
  is a file extension).
- `test_profile.py`: the three routes refuse without a session and refuse an API token; no field names
  a user.
- Web: `AccountScreen.test.ts` (5 new), `audit-words.test.ts` (2, seen red), `search.test.ts`.

Found on the way and fixed with the founder's approval: the two-factor section's password labels were
swapped ("to turn this off" on the form that turns it on); `AccountScreen.test.ts` holds both, seen red.

## Specification impact

`team-and-api-access/spec.md` BR6 and FR11 amended: history names a removed person by id.

## Documentation impact

`current-system-reference.md` (profile routes, removal).

## Last updated

2026-10-11
