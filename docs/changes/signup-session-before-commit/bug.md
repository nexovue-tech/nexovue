# Bug: signup hands out a session before the tenant is durable

## Reported behavior

`just check` failed intermittently on
`apps/api/tests/test_tenant_isolation_live.py::test_a_session_for_an_organization_that_has_been_deleted_is_refused`.
The assertion that failed was the test's *first* one: `/auth/me` answered 401 immediately after a
signup that had returned 201. The test passed when run alone and when run with its own file; it
failed only inside the full suite, and not on every run.

## Reproduction

`scratchpad/repro.py`: spend the per-IP auth window with failed logins, sign up, then call
`/auth/me` at once. Reproduced on the third round. The response body was decisive:

```
"detail": "The session is no longer valid. Sign in again."
```

## Root cause

`get_db_session` (`apps/api/src/nexovue_api/deps.py`) is a yield dependency: it commits in teardown,
after the response has been sent. `sign_up` called `start_session` before that, and
`SessionStore.create` writes to Redis immediately, outside the transaction.

So between the 201 and the commit there was a window where the session existed and the rows it named
did not. Any request arriving in that window resolved the session in Redis, failed `load_identity`
in Postgres, and answered 401. Load widens the window, which is why the full suite tripped it and a
single file did not.

Ruled out with evidence, not reasoning: Redis eviction (`evicted_keys: 0`, 1.9 MB of 384 MB),
session TTL (7 days), and the `sessions_valid_from` epoch (NULL at signup; the column has no server
default).

## Customer impact

Not test-only. A real signup whose console loaded fast enough would be signed out on the first
request after account creation. Worse, the API key and session cookie were handed over before the
transaction was durable: had the commit failed, the customer would have held working-looking
credentials for an organization that does not exist.

## Fix

`sign_up` commits the tenant before minting the session. `expire_on_commit=False` on the session
factory, so the ORM objects the response is built from survive the commit.

## Regression coverage

`apps/api/tests/test_signup.py::test_the_tenant_is_committed_before_its_session_is_minted` records
the order of provision, commit, and session creation through the real route and asserts the commit
precedes the session. Verified to fail without the fix (`['provision', 'session']`) and pass with it.

The live test now asserts with `response.text`, so the next failure names which of the endpoint's
three 401s occurred instead of hiding it behind a status code.
