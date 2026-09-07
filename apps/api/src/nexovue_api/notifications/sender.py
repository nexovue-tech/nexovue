"""The port every transactional email leaves through.

No provider is chosen yet, and that is the point of this file existing before
one is. Verification, password reset and invoices all need mail; each of those
is a message whose non-arrival is a lost account rather than a lower open rate,
so the provider will be judged on deliverability and may well be replaced. This
interface is what makes replacing it an afternoon.

**The interface speaks our domain, never a provider's.** ADR 0015 fixed the
object store as the generic S3 API rather than any one vendor's, and
`data/objectstore/store.py` carries the rule that follows from it — *"nothing
here may use a feature outside that API"*. The same rule applies here: a
`message_stream`, a `template_alias`, a `sending_domain_id` are Postmark's
vocabulary, Resend's, SES's. If one of those words reaches this module, the
abstraction has already failed and nobody will notice until the second provider.

**What a caller may know:** who the mail is for, which message it is, and what
fills it. Nothing about how it is sent.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


class EmailError(Exception):
    """Sending failed, and the caller has to decide what that means.

    Deliberately not swallowed. A verification link that silently never arrives
    is worse than a visible error — the plan says so and it is the whole reason
    this is an exception rather than a returned bool. A caller that cannot
    recover should surface the failure to the person waiting for the mail.
    """


class Message(Enum):
    """Every message this product sends. A closed set, on purpose.

    Templates live with the provider or in this repository depending on the
    provider, so the *name* is the contract and the body is an implementation
    detail. A new message is a new member here first.
    """

    VERIFY_EMAIL = "verify_email"
    RESET_PASSWORD = "reset_password"
    INVOICE = "invoice"


@dataclass(frozen=True, slots=True)
class Delivery:
    """What the provider said about one accepted message.

    `provider_message_id` is opaque and exists for one reason: when a customer
    says a mail never arrived, it is what turns a support conversation into a
    lookup. It is not parsed and nothing branches on its shape.
    """

    provider_message_id: str


class EmailSender(Protocol):
    """The only way mail leaves this application.

    One method, because one method is all three messages need. A provider that
    wants batching, scheduling or campaigns is offering something this product
    does not send.
    """

    async def send(
        self,
        *,
        to: str,
        message: Message,
        data: Mapping[str, Any],
    ) -> Delivery:
        """Send one message to one address.

        `data` fills the message and is the caller's own vocabulary — a
        verification URL, an invoice total. An implementation maps it to whatever
        its provider calls a template variable, and that mapping is the only
        place a provider's naming is allowed to exist.

        Raises `EmailError` if the provider refused or could not be reached.
        """
        ...
