"""One real message, through the real provider, to prove the chain.

**Not run by default, because every run is spent.** Two messages leave here per
run — one per template — and this suite used to sit inside `just check`. Forty
gate runs in a single working session sent eighty real messages and consumed
most of a month's provider allowance. The suite was right about what it proves
and wrong about when to prove it: a mail chain is verified before a release, not
on every change to a chart.

`NEXOVUE_BILLABLE_LIVE_TESTS=1` asks for it; `just test-billable` sets that. Ask
for it and it behaves exactly as it did — failing loudly rather than skipping,
because a skip you did not ask for is the thing this file exists to avoid.


The offline suite drives `httpx.MockTransport`, which proves the request is
shaped correctly and nothing else. It cannot tell you the API key is valid, the
sending domain is verified, the DKIM record resolves, or that a message actually
arrives — and each of those has failed for somebody. This one sends.

`require_stack` rather than a skip, for the reason it exists: a skipped live
suite is indistinguishable from a passing one, and a mail provider that has
started refusing is exactly what a green-but-empty run would hide.

**It sends to `EMAIL_REPLY_TO`**, which is our own inbox. A test that mailed a
stranger would be a defect of its own.
"""

from __future__ import annotations

import os
from typing import Final

import pytest
from live import require_opt_in, require_stack
from nexovue_platform.email import Message, build_email_sender
from nexovue_platform.email.settings import EMAIL_VARIABLES, EmailSettings

_PROVIDER: Final = os.environ.get("EMAIL_PROVIDER", "resend")
_API_KEY: Final = os.environ.get("EMAIL_API_KEY", "")
_SENDING_DOMAIN: Final = os.environ.get("EMAIL_SENDING_DOMAIN", "")
_FROM: Final = os.environ.get("EMAIL_FROM_ADDRESS", "")
_REPLY_TO: Final = os.environ.get("EMAIL_REPLY_TO", "")

# Asked for, before anything else — including before the configuration check,
# so an unconfigured machine is told "not run by default" rather than told off
# for missing credentials it never needed.
require_opt_in(
    suite="apps/api/tests/test_notifications_live.py",
    cost="it sends two real messages through the mail provider, against a metered quota",
)

require_stack(
    reachable=all((_API_KEY, _SENDING_DOMAIN, _FROM, _REPLY_TO)),
    suite="apps/api/tests/test_notifications_live.py",
    remedy=f"email is not configured — set {', '.join(EMAIL_VARIABLES)} in .env",
)

SETTINGS: Final = EmailSettings(
    provider=_PROVIDER,  # type: ignore[arg-type]  # validated by Settings in the app
    api_key=_API_KEY,
    sending_domain=_SENDING_DOMAIN,
    from_address=_FROM,
    reply_to=_REPLY_TO,
)


@pytest.mark.parametrize("message", [Message.VERIFY_EMAIL, Message.RESET_PASSWORD])
async def test_the_provider_accepts_a_real_message(message: Message) -> None:
    """Both templates, because a field missing from one is not caught by the other.

    The assertion is the returned identifier, not the absence of an exception: a
    provider that accepted the request and queued nothing would still return
    without raising, and the id is what a support conversation later looks up.
    """
    sender = build_email_sender(SETTINGS)

    delivery = await sender.send(
        to=_REPLY_TO,
        message=message,
        data={
            "link": "https://app.nexovue.com/example-link-from-a-test",
            "expires_in": "24 hours",
        },
    )

    assert delivery.provider_message_id, (
        "the provider accepted the message and returned no identifier, which is "
        "indistinguishable from it queuing nothing"
    )
