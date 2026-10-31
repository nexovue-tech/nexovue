"""Delivering to real sockets: what each receiver is sent, and what its answer means
(AC1, AC3, AC4, FR19). The receiver is the recording site on this machine.

This suite fails rather than skips when the stack is down; see tests/helpers/live.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alert_fixtures import AlertTenant, add_channel, add_rule, future, postgres_reachable, rows
from live import require_stack
from nexovue_platform.alerts.channels import new_webhook_secret
from nexovue_platform.checks.sealing import (
    NOTIFICATION_CHANNEL_PURPOSE,
    Keyring,
    SealedBox,
    channel_secret_owner,
)
from recording_site import RecordedRequest, RecordingSite, Reply
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.alerts import payloads
from nexovue_scheduler.alerts.delivery import deliver_due
from nexovue_scheduler.alerts.dispatch import Outbound, Outgoing, send
from nexovue_scheduler.alerts.messages import AlertMessage
from nexovue_scheduler.alerts.outbox import Channel
from nexovue_scheduler.alerts.senders.http import NOT_PUBLIC
from nexovue_scheduler.probes import state_store

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_alert_senders_live.py",
    remedy="Postgres is not reachable — run `just up`",
)

BOX = SealedBox(Keyring(os.urandom(32)), purpose=NOTIFICATION_CHANNEL_PURPOSE)
ORG = uuid.uuid4()
MESSAGE = AlertMessage(
    kind="opened",
    severity="critical",
    summary="[critical] Checkout API is down",
    details=("Checkout API is down.",),
    link="https://app.nexovue.test/synthetics/c1",
    rule_name="Any check down",
    manage_link=None,
)
OUTGOING = Outgoing(
    message=MESSAGE,
    message_id="batch-1",
    dedup_key="incident-1",
    subject="Checkout API",
    opened_at=datetime(2026, 9, 19, 14, 3, tzinfo=UTC),
    resolved_at=None,
)


def _strings(value: object) -> list[str]:
    """Every string inside a decoded JSON body, wherever the channel put it."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def _outbound(*, private_allowed: bool = True) -> Outbound:
    return Outbound(
        email_sender=None,
        secret_box=BOX,
        app_base_url="https://app.nexovue.test",
        private_allowed=private_allowed,
    )


def _channel(kind: str, secret: str, config: dict[str, object] | None = None) -> Channel:
    channel_id = uuid.uuid4()
    return Channel(
        id=channel_id,
        org_id=ORG,
        kind=kind,
        name=kind,
        config=config or {},
        sealed_secret=BOX.seal(secret, owner=channel_secret_owner(str(ORG), str(channel_id))),
    )


@pytest.mark.parametrize("kind", ["slack", "teams", "discord"])
async def test_a_chat_webhook_receives_its_format_at_its_secret_url(
    kind: str, engine: AsyncEngine, recording_site: RecordingSite
) -> None:
    url = recording_site.url(f"/{kind}/T000/B000/secret-part")
    result = await send(engine, _outbound(), _channel(kind, url), OUTGOING, now=datetime.now(UTC))

    assert result.outcome == "delivered"
    [request] = recording_site.requests
    assert request.method == "POST"
    assert request.path == f"/{kind}/T000/B000/secret-part"
    assert request.headers["content-type"] == "application/json"
    # Each chat escapes its own markup, so the summary arrives in that channel's form.
    shown = {"slack": payloads.slack_text, "teams": payloads.teams_text}.get(
        kind, payloads.discord_text
    )(MESSAGE.summary)
    assert any(shown in value for value in _strings(json.loads(request.body)))


async def test_a_webhook_is_signed_so_that_an_independent_check_verifies_it(
    engine: AsyncEngine, recording_site: RecordingSite
) -> None:
    """AC3, verified the way a receiver would — HMAC of `id.timestamp.body` — rather
    than by calling our own signer."""
    secret = new_webhook_secret()
    channel = _channel("webhook", secret, {"url": recording_site.url("/hooks/nexovue")})
    await send(engine, _outbound(), channel, OUTGOING, now=datetime.now(UTC))

    [request] = recording_site.requests
    message_id = request.headers["webhook-id"]
    timestamp = request.headers["webhook-timestamp"]
    expected = base64.b64encode(
        hmac.new(
            base64.b64decode(secret.removeprefix("whsec_")),
            f"{message_id}.{timestamp}.".encode() + request.body,
            hashlib.sha256,
        ).digest()
    ).decode()
    assert message_id == "batch-1"
    assert request.headers["webhook-signature"] == f"v1,{expected}"
    assert json.loads(request.body)["type"] == "incident.opened"
    assert secret not in request.body.decode()


@pytest.mark.parametrize(
    ("reply", "outcome", "reason"),
    [
        (Reply(status=503, body=b"busy"), "retry", "the receiver answered 503: busy"),
        (Reply(status=404, body=b"no_service"), "failed", "the receiver answered 404: no_service"),
        (
            Reply(status=302, body=b"", headers={"Location": "https://elsewhere.test/"}),
            "failed",
            "the receiver answered 302, a redirect, which is not followed",
        ),
    ],
)
async def test_each_answer_means_what_fr19_says(
    reply: Reply,
    outcome: str,
    reason: str,
    engine: AsyncEngine,
    recording_site: RecordingSite,
) -> None:
    recording_site.replies["/slack/x"] = reply
    result = await send(
        engine,
        _outbound(),
        _channel("slack", recording_site.url("/slack/x")),
        OUTGOING,
        now=datetime.now(UTC),
    )
    assert (result.outcome, result.reason) == (outcome, reason)


async def test_too_many_requests_is_retried_when_the_receiver_says(
    engine: AsyncEngine, recording_site: RecordingSite
) -> None:
    recording_site.replies["/slack/x"] = Reply(status=429, headers={"Retry-After": "120"})
    result = await send(
        engine,
        _outbound(),
        _channel("slack", recording_site.url("/slack/x")),
        OUTGOING,
        now=datetime.now(UTC),
    )
    assert (result.outcome, result.retry_after_seconds) == ("retry", 120)


async def test_a_receiver_echoing_the_url_does_not_put_the_credential_in_the_log(
    engine: AsyncEngine, recording_site: RecordingSite
) -> None:
    """AC2: a Slack URL's path is its credential, and the reason is shown to people."""
    url = recording_site.url("/slack/T000/B000/very-secret-token")
    recording_site.replies["/slack/T000/B000/very-secret-token"] = Reply(
        status=400, body=f"bad webhook {url}".encode()
    )
    result = await send(
        engine, _outbound(), _channel("slack", url), OUTGOING, now=datetime.now(UTC)
    )
    assert result.outcome == "failed"
    assert "very-secret-token" not in (result.reason or "")


async def test_a_destination_that_is_not_public_is_refused_without_its_address(
    engine: AsyncEngine, recording_site: RecordingSite
) -> None:
    """AC4, BR7. The recording site is on a private address, which only a development
    stack may reach; with that off, nothing is sent and the reason names no address."""
    result = await send(
        engine,
        _outbound(private_allowed=False),
        _channel("slack", recording_site.url("/slack/x")),
        OUTGOING,
        now=datetime.now(UTC),
    )
    assert (result.outcome, result.reason) == ("failed", NOT_PUBLIC)
    assert recording_site.requests == []
    assert recording_site.host not in (result.reason or "")


async def test_a_retried_webhook_carries_the_same_webhook_id(
    engine: AsyncEngine, tenant: AlertTenant, recording_site: RecordingSite
) -> None:
    """AC3, through the outbox: 503 then 200 is two requests, one id."""
    answers = iter([Reply(status=503), Reply(status=200)])

    def reply(_: RecordedRequest) -> Reply:
        return next(answers)

    recording_site.replies["/hooks/nexovue"] = reply
    secret = new_webhook_secret()
    async with engine.begin() as connection:
        channel_id = await add_channel(
            connection,
            tenant.org_id,
            kind="webhook",
            name="Hooks",
            config=json.dumps({"url": recording_site.url("/hooks/nexovue")}),
        )
        await connection.execute(
            text("UPDATE notification_channels SET sealed_secret = :sealed WHERE id = :id"),
            {
                "sealed": BOX.seal(
                    secret, owner=channel_secret_owner(str(tenant.org_id), str(channel_id))
                ),
                "id": channel_id,
            },
        )
        await add_rule(
            connection,
            tenant.org_id,
            tenant.project_id,
            name="Hooks",
            condition={"kind": "check_state", "states": {"down": "critical"}},
            channel_ids=[channel_id],
        )
    at = future()
    await state_store.record(
        engine,
        state_store.RecordedOutcome(
            check_id=str(tenant.check_id),
            org_id=str(tenant.org_id),
            project_id=str(tenant.project_id),
            outcome="down",
            run_id="run-1",
            location="local",
            observed_at=at,
            reason=None,
        ),
    )

    await deliver_due(engine, _outbound(), now=lambda: at, org_id=tenant.org_id)
    await deliver_due(
        engine, _outbound(), now=lambda: at + timedelta(minutes=20), org_id=tenant.org_id
    )

    hooks = [request for request in recording_site.requests if request.path == "/hooks/nexovue"]
    assert len(hooks) == 2
    assert hooks[0].headers["webhook-id"] == hooks[1].headers["webhook-id"]
    attempts = await rows(
        engine,
        "SELECT outcome, http_status FROM notification_attempts WHERE channel_id = :id "
        "ORDER BY attempted_at",
        id=channel_id,
    )
    assert [(row.outcome, row.http_status) for row in attempts] == [
        ("retrying", 503),
        ("delivered", 200),
    ]
