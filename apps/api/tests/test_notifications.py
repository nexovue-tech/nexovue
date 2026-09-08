"""The Resend sender, driven without a network.

`httpx.MockTransport` answers the request in-process, so these assert what is
actually put on the wire — the From address, the Reply-To, the rendered body —
rather than that a mock was called. A test that only checks "send was called"
would pass with an empty body and a wrong sender.

The one live test is separate and skipped without credentials; see
`test_notifications_live.py`.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import nexovue_api
from nexovue_api.notifications import build_email_sender
from nexovue_api.notifications.resend import SEND_ENDPOINT, ResendEmailSender
from nexovue_api.notifications.sender import EmailError, EmailSender, Message
from nexovue_api.settings import EmailSettings

SETTINGS = EmailSettings(
    provider="resend",
    api_key="re_test_key",
    sending_domain="mail.nexovue.com",
    from_address="noreply@mail.nexovue.com",
    reply_to="nexovue@nexovue.com",
)

VERIFY_DATA = {"link": "https://app.nexovue.com/verify?t=abc", "expires_in": "24 hours"}


def _sender(handler: Any) -> ResendEmailSender:
    return ResendEmailSender(
        SETTINGS, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


def _accepts(captured: list[httpx.Request]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"id": "b7c1f0e2-0000-4000-8000-000000000001"})

    return handler


async def test_a_sent_message_carries_the_configured_addresses() -> None:
    """The two addresses are different on purpose and both must survive.

    `from` has to be at the verified sending domain or the provider refuses it;
    `reply_to` is a real inbox that deliberately is not. Sending with them
    swapped would be accepted by nobody and read as broken by everybody.
    """
    captured: list[httpx.Request] = []
    delivery = await _sender(_accepts(captured)).send(
        to="someone@example.test", message=Message.VERIFY_EMAIL, data=VERIFY_DATA
    )

    assert delivery.provider_message_id == "b7c1f0e2-0000-4000-8000-000000000001"
    assert len(captured) == 1
    body = json.loads(captured[0].content)
    assert body["from"] == "noreply@mail.nexovue.com"
    assert body["reply_to"] == "nexovue@nexovue.com"
    assert body["to"] == ["someone@example.test"]
    assert str(captured[0].url) == SEND_ENDPOINT


async def test_the_body_is_rendered_rather_than_sent_with_placeholders() -> None:
    """The failure this catches is a mail that reads `{link}` to a customer."""
    captured: list[httpx.Request] = []
    await _sender(_accepts(captured)).send(
        to="someone@example.test", message=Message.VERIFY_EMAIL, data=VERIFY_DATA
    )

    body = json.loads(captured[0].content)["text"]
    assert VERIFY_DATA["link"] in body
    assert "24 hours" in body
    assert "{" not in body, "a placeholder survived into the body"


async def test_the_api_key_travels_as_a_bearer_token_and_not_in_the_body() -> None:
    """A key in a JSON body would be logged by anything that logs a request."""
    captured: list[httpx.Request] = []
    await _sender(_accepts(captured)).send(
        to="someone@example.test", message=Message.VERIFY_EMAIL, data=VERIFY_DATA
    )

    assert captured[0].headers["authorization"] == "Bearer re_test_key"
    assert "re_test_key" not in captured[0].content.decode()


async def test_a_missing_template_field_refuses_instead_of_sending() -> None:
    """Refusing beats degrading: a half-rendered password reset is worse than
    none, because a customer acts on it."""
    captured: list[httpx.Request] = []
    with pytest.raises(EmailError, match="expires_in"):
        await _sender(_accepts(captured)).send(
            to="someone@example.test",
            message=Message.RESET_PASSWORD,
            data={"link": "https://app.nexovue.com/reset?t=abc"},
        )

    assert not captured, "nothing may be sent when a field is missing"


async def test_a_refusal_carries_the_providers_own_reason() -> None:
    """ "Domain is not verified" and "invalid API key" are the two failures that
    actually happen, and a bare status code names neither."""

    def refuses(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "The mail.nexovue.com domain is not verified."})

    with pytest.raises(EmailError, match="not verified"):
        await _sender(refuses).send(
            to="someone@example.test", message=Message.VERIFY_EMAIL, data=VERIFY_DATA
        )


async def test_an_unreachable_provider_is_an_error_and_not_a_silent_success() -> None:
    def unreachable(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with pytest.raises(EmailError, match="could not reach"):
        await _sender(unreachable).send(
            to="someone@example.test", message=Message.VERIFY_EMAIL, data=VERIFY_DATA
        )


def test_the_implementation_matches_the_port_signature() -> None:
    """The port and its implementation live in different files and nothing else
    connects them, so a drifted signature is found at a call site otherwise.

    Signatures rather than `isinstance`: the Protocol is not `@runtime_checkable`
    and making it so would compare method *names* only, which passes for an
    implementation whose arguments have changed. mypy checks the annotated
    assignment below too; this is the same check for anyone running without it.
    """
    sender: EmailSender = ResendEmailSender(SETTINGS)
    assert sender is not None

    port = inspect.signature(EmailSender.send)
    implementation = inspect.signature(ResendEmailSender.send)
    assert implementation.parameters.keys() == port.parameters.keys()
    for name, expected in port.parameters.items():
        assert implementation.parameters[name].kind == expected.kind, (
            f"`{name}` is {implementation.parameters[name].kind} on the implementation and "
            f"{expected.kind} on the port — a keyword-only argument that became positional is "
            f"a call site that breaks without the type checker noticing."
        )


# ---------------------------------------------------------- the composition root


def test_the_provider_is_chosen_from_configuration_and_not_by_a_caller() -> None:
    """The third part of the pattern: a port and an adapter alone still leave a
    caller naming a vendor at construction.

    Switching provider is a change to `EMAIL_PROVIDER` and nothing else, which
    is only true while this is the single place that reads it.
    """
    sender = build_email_sender(SETTINGS)
    assert isinstance(sender, ResendEmailSender)


def test_nothing_outside_the_composition_root_imports_a_provider() -> None:
    """One file may name a vendor's module; the rest ask for the port.

    Without this the pattern erodes quietly — a second `from ... import
    ResendEmailSender` compiles, passes, and makes the next switch a search
    through the tree rather than one edit.
    """
    api_source = Path(nexovue_api.__file__).parent
    allowed = {"notifications/__init__.py", "notifications/resend.py"}

    offenders = [
        str(module.relative_to(api_source))
        for module in api_source.rglob("*.py")
        if str(module.relative_to(api_source)) not in allowed
        and "ResendEmailSender" in module.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"{offenders} name a provider directly. Ask `build_email_sender` for an `EmailSender` "
        f"instead; only the composition root chooses."
    )
