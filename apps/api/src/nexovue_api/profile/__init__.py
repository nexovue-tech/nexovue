"""The signed-in person's own account: their name, their password, their sessions.

**Every route here acts on the caller and only on the caller.** The user comes
from `CurrentIdentity`, never from a path segment or a body field. A route that
accepted a target user id would be a privilege-escalation surface the day F26
brings a second person into an organization — and it would be one that no test
notices, because it works perfectly for the person testing it.

**One flow starts here and ends elsewhere.** `POST /profile/email` asks to move
the account to a new address and mails that address a link; following the link
is `POST /auth/email-change/confirm`, which is unauthenticated because it is
opened from a mail client. Putting that half here would have meant a route in
this package that no session guards, which is the one thing the rule above
cannot bend for.

There is no `queries.py`. `auth.dependencies.get_current_identity` has already
loaded the caller's `User` row through the request's session to resolve the
tenant, so the row is in hand: changing a field on it and letting the request
dependency commit *is* the write. A lookup here would re-read a row we hold.
"""
