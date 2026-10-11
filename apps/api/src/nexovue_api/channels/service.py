"""Notification channels: where an organization's alerts go (F21, FR1 to FR3, FR23, FR25).

**A secret goes in and never comes out.** Slack, Teams and Discord webhook URLs, a
PagerDuty routing key and a webhook's signing secret are sealed the moment they
arrive, bound to the organization and the channel (ADR-010, amended), and no
response, log line or audit row carries one afterwards (BR6, AC2). A webhook's
signing secret is the one exception, once: nexovue generates it, and the customer
must see it to verify what we send.

**Validation here is the fast answer, not the boundary.** A destination written as
a private address is refused at save; one that only *resolves* somewhere private is
refused by the scheduler at connect time (BR7), which is the only place the
address cannot change again — the same split `checks/targets.py` records.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

from nexovue_platform.alerts.channels import (
    READ_BY_PEOPLE,
    URL_SECRET_KINDS,
    config_model,
    destination_host,
    new_webhook_secret,
    takes_secret,
    validate_secret,
)
from nexovue_platform.alerts.quiet_hours import QuietHours
from nexovue_platform.checks.sealing import (
    NOTIFICATION_CHANNEL_PURPOSE,
    ConfiguredKeys,
    SealedBox,
    channel_secret_owner,
    keyring_from,
)
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api.audit import service as audit
from nexovue_api.channels import queries
from nexovue_api.channels.schemas import (
    ChannelListResponse,
    ChannelSummary,
    CreateChannelRequest,
    CreatedChannel,
    DeliveryAttempt,
    DeliveryLogResponse,
    RuleUsingChannel,
    SendTestAccepted,
    SendTestResult,
    SigningSecret,
    UpdateChannelRequest,
)
from nexovue_api.channels.send_test import ChannelTester
from nexovue_api.checks.targets import validate_target
from nexovue_api.data.postgres.models import AlertRule, NotificationChannel
from nexovue_api.errors.exceptions import (
    ConflictError,
    DependencyUnavailableError,
    InvalidRequestError,
    NotFoundError,
)

MAX_CHANNELS_PER_ORGANIZATION: Final = 50
DELIVERY_LOG_DAYS: Final = 30

CANNOT_SAVE: Final = "This channel cannot be saved. Each problem is named with its field."
NOT_FOUND: Final = "No such channel."
_KEY_MISSING: Final = (
    "Channels with a secret cannot be saved on this deployment: CHECK_SECRET_KEY is not set."
)
_EMAIL_MISSING: Final = (
    "Email channels need a mail provider, and this deployment has none: set EMAIL_PROVIDER and "
    "the other EMAIL_* variables."
)
OWNERS_UNDELETABLE: Final = (
    "The organization owners channel cannot be deleted — every default rule sends to it. Mute "
    "it instead."
)
OWNERS_FIXED: Final = "The organization owners channel has no settings to change; mute it instead."


def channel_box(keys: ConfiguredKeys) -> SealedBox:
    if not keys.current:
        raise DependencyUnavailableError(_KEY_MISSING)
    try:
        return SealedBox(
            keyring_from(keys.current, keys.previous), purpose=NOTIFICATION_CHANNEL_PURPOSE
        )
    except ValueError as unusable:
        raise DependencyUnavailableError(_KEY_MISSING) from unusable


async def list_channels(session: AsyncSession, *, org_id: uuid.UUID) -> ChannelListResponse:
    channels = await queries.list_channels(session, org_id=org_id)
    rules = await queries.rules_by_channel(session, org_id=org_id)
    return ChannelListResponse(
        channels=[_summary(channel, rules.get(channel.id, [])) for channel in channels]
    )


async def get(session: AsyncSession, *, org_id: uuid.UUID, channel_id: uuid.UUID) -> ChannelSummary:
    channel = await _require(session, org_id=org_id, channel_id=channel_id)
    rules = await queries.rules_by_channel(session, org_id=org_id)
    return _summary(channel, rules.get(channel.id, []))


async def create(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    request: CreateChannelRequest,
    secret_keys: ConfiguredKeys,
    email_configured: bool,
) -> CreatedChannel:
    if request.kind == "email" and not email_configured:
        raise DependencyUnavailableError(_EMAIL_MISSING)
    existing = await queries.list_channels(session, org_id=org_id)
    if len(existing) >= MAX_CHANNELS_PER_ORGANIZATION:
        raise ConflictError(
            f"An organization may have at most {MAX_CHANNELS_PER_ORGANIZATION} channels."
        )

    config = _validated_config(request.kind, request.config)
    secret = _validated_secret(request.kind, request.secret, required=True)
    signing_secret = new_webhook_secret() if request.kind == "webhook" else None
    box = channel_box(secret_keys) if (secret or signing_secret) else None

    channel = NotificationChannel(
        org_id=org_id,
        kind=request.kind,
        name=request.name.strip(),
        config=_with_host(request.kind, config, secret),
    )
    queries.add(session, channel)
    await session.flush()
    if box is not None:
        channel.sealed_secret = box.seal(
            secret or signing_secret or "", owner=channel_secret_owner(str(org_id), str(channel.id))
        )
    await session.flush()
    await session.refresh(channel)

    audit.record_notification_channel_created(session, channel=channel, actor_user_id=actor_user_id)
    return CreatedChannel(**_summary(channel, []).model_dump(), signing_secret=signing_secret)


async def update(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    channel_id: uuid.UUID,
    request: UpdateChannelRequest,
    secret_keys: ConfiguredKeys,
) -> ChannelSummary:
    channel = await _require(session, org_id=org_id, channel_id=channel_id)
    if channel.kind == "owners" and (request.config is not None or request.secret is not None):
        raise InvalidRequestError(OWNERS_FIXED)

    if request.name is not None:
        channel.name = request.name.strip()
    secret = _validated_secret(channel.kind, request.secret, required=False)
    if request.config is not None:
        kept = {"host": channel.config["host"]} if "host" in channel.config else {}
        channel.config = _validated_config(channel.kind, request.config) | kept
    if secret is not None:
        box = channel_box(secret_keys)
        channel.sealed_secret = box.seal(
            secret, owner=channel_secret_owner(str(org_id), str(channel.id))
        )
        channel.config = _with_host(channel.kind, dict(channel.config), secret)
    channel.updated_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(channel)

    audit.record_notification_channel_updated(session, channel=channel, actor_user_id=actor_user_id)
    return await get(session, org_id=org_id, channel_id=channel_id)


async def rotate_signing_secret(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    channel_id: uuid.UUID,
    secret_keys: ConfiguredKeys,
) -> SigningSecret:
    """A new signing secret for a webhook, shown this once. The old one stops working
    at once: a receiver still verifying with it will refuse what we send next."""
    channel = await _require(session, org_id=org_id, channel_id=channel_id)
    if channel.kind != "webhook":
        raise InvalidRequestError("Only a webhook channel has a signing secret.")
    signing_secret = new_webhook_secret()
    channel.sealed_secret = channel_box(secret_keys).seal(
        signing_secret, owner=channel_secret_owner(str(org_id), str(channel.id))
    )
    channel.updated_at = datetime.now(UTC)
    await session.flush()
    audit.record_notification_channel_updated(session, channel=channel, actor_user_id=actor_user_id)
    return SigningSecret(signing_secret=signing_secret)


async def mute(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    channel_id: uuid.UUID,
    until: datetime | None,
) -> ChannelSummary:
    """Muted until `until`, which ends by itself; None unmutes (FR14)."""
    channel = await _require(session, org_id=org_id, channel_id=channel_id)
    if until is not None and until <= datetime.now(UTC):
        raise InvalidRequestError(
            "A mute must end in the future.",
            errors=[{"field": "until", "reason": "must be in the future"}],
        )
    channel.muted_until = until
    channel.muted_by = actor_user_id if until is not None else None
    await session.flush()
    audit.record_notification_channel_muted(session, channel=channel, actor_user_id=actor_user_id)
    return await get(session, org_id=org_id, channel_id=channel_id)


async def set_quiet_hours(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    channel_id: uuid.UUID,
    quiet_hours: dict[str, Any] | None,
) -> ChannelSummary:
    """When warnings on this channel wait, to arrive together as a digest when the
    quiet ends. Criticals never wait (BR4)."""
    channel = await _require(session, org_id=org_id, channel_id=channel_id)
    if channel.kind not in READ_BY_PEOPLE:
        raise InvalidRequestError(
            "Quiet hours are for channels people read; PagerDuty and webhooks keep their own."
        )
    if quiet_hours is None:
        channel.quiet_hours = None
    else:
        try:
            parsed = QuietHours.model_validate(quiet_hours)
        except ValidationError as invalid:
            raise InvalidRequestError(
                CANNOT_SAVE,
                errors=[
                    {
                        "field": ".".join(("quiet_hours", *(str(part) for part in problem["loc"]))),
                        "reason": problem["msg"].removeprefix("Value error, "),
                    }
                    for problem in invalid.errors()
                ],
            ) from invalid
        channel.quiet_hours = parsed.model_dump(mode="json")
    channel.updated_at = datetime.now(UTC)
    await session.flush()
    audit.record_notification_channel_updated(session, channel=channel, actor_user_id=actor_user_id)
    return await get(session, org_id=org_id, channel_id=channel_id)


async def delete(
    session: AsyncSession, *, org_id: uuid.UUID, actor_user_id: uuid.UUID, channel_id: uuid.UUID
) -> None:
    """Rules that sent here lose it; one left with no channel shows as *not notifying
    anyone* rather than being deleted with it (FR25)."""
    channel = await _require(session, org_id=org_id, channel_id=channel_id)
    if channel.kind == "owners":
        raise ConflictError(OWNERS_UNDELETABLE)
    audit.record_notification_channel_deleted(session, channel=channel, actor_user_id=actor_user_id)
    await queries.delete_channel(session, org_id=org_id, channel_id=channel_id)
    await session.flush()


async def delivery_log(
    session: AsyncSession, *, org_id: uuid.UUID, channel_id: uuid.UUID
) -> DeliveryLogResponse:
    await _require(session, org_id=org_id, channel_id=channel_id)
    since = datetime.now(UTC) - timedelta(days=DELIVERY_LOG_DAYS)
    entries = await queries.delivery_log(session, org_id=org_id, channel_id=channel_id, since=since)
    return DeliveryLogResponse(
        attempts=[
            DeliveryAttempt(
                id=attempt.id,
                attempted_at=attempt.attempted_at,
                outcome=attempt.outcome,
                http_status=attempt.http_status,
                reason=attempt.reason,
                notifications=about,
                kind=attempt.kind,
            )
            for attempt, about in entries
        ]
    )


async def _require(
    session: AsyncSession, *, org_id: uuid.UUID, channel_id: uuid.UUID
) -> NotificationChannel:
    channel = await queries.find_channel(session, org_id=org_id, channel_id=channel_id)
    if channel is None:
        raise NotFoundError(NOT_FOUND)
    return channel


def _validated_config(kind: str, raw: dict[str, object]) -> dict[str, object]:
    try:
        config = config_model(kind).model_validate(raw).model_dump(mode="json")
    except ValidationError as invalid:
        raise InvalidRequestError(
            CANNOT_SAVE,
            errors=[
                {
                    "field": ".".join(("config", *(str(part) for part in problem["loc"]))),
                    "reason": problem["msg"].removeprefix("Value error, "),
                }
                for problem in invalid.errors()
            ],
        ) from invalid
    if kind == "webhook":
        _refuse_our_own(str(config["url"]), field="config.url")
    return config


def _validated_secret(kind: str, raw: str | None, *, required: bool) -> str | None:
    if not takes_secret(kind):
        if raw is not None:
            raise InvalidRequestError(
                CANNOT_SAVE, errors=[{"field": "secret", "reason": f"a {kind} channel takes none"}]
            )
        return None
    if raw is None:
        if required:
            label = "routing key" if kind == "pagerduty" else "webhook URL"
            raise InvalidRequestError(
                CANNOT_SAVE, errors=[{"field": "secret", "reason": f"a {label} is required"}]
            )
        return None
    try:
        secret = validate_secret(kind, raw)
    except ValueError as invalid:
        raise InvalidRequestError(
            CANNOT_SAVE, errors=[{"field": "secret", "reason": str(invalid)}]
        ) from invalid
    if kind in URL_SECRET_KINDS:
        _refuse_our_own(secret, field="secret")
    return secret


def _refuse_our_own(url: str, *, field: str) -> None:
    """A literal private address, or one of our own hostnames, is refused while the
    customer is looking at the form (see the module docstring)."""
    try:
        validate_target(url)
    except InvalidRequestError as refused:
        raise InvalidRequestError(
            CANNOT_SAVE, errors=[{"field": field, "reason": refused.detail}]
        ) from refused


def _with_host(kind: str, config: dict[str, object], secret: str | None) -> dict[str, object]:
    """For the kinds whose URL is the secret, the host alone is kept where it may be
    shown, so the console can say where a channel posts without opening anything."""
    if kind in URL_SECRET_KINDS and secret is not None:
        return config | {"host": urlsplit(secret).hostname}
    return config


def _summary(channel: NotificationChannel, rules: list[AlertRule]) -> ChannelSummary:
    return ChannelSummary(
        **queries.summary_fields(channel),
        destination=channel.config.get("host")
        if channel.kind in URL_SECRET_KINDS
        else destination_host(channel.kind, channel.config, None),
        rules=[
            RuleUsingChannel(id=rule.id, name=rule.name, project_id=rule.project_id)
            for rule in rules
        ],
    )


TEST_POLL_SECONDS: Final = 0.5
MAX_TEST_WAIT_SECONDS: Final = 10
_TESTS_UNAVAILABLE: Final = (
    "Send test is not available right now: the delivery queue cannot be reached."
)


async def dispatch_test(
    session: AsyncSession,
    tester: ChannelTester,
    *,
    org_id: uuid.UUID,
    channel_id: uuid.UUID,
) -> SendTestAccepted:
    await _require(session, org_id=org_id, channel_id=channel_id)
    request_id = str(uuid.uuid4())
    try:
        await tester.dispatch(org_id=str(org_id), channel_id=str(channel_id), request_id=request_id)
    except Exception as unreachable:
        raise DependencyUnavailableError(_TESTS_UNAVAILABLE) from unreachable
    return SendTestAccepted(request_id=request_id)


async def test_result(
    tester: ChannelTester, *, org_id: uuid.UUID, request_id: str, wait_seconds: float
) -> SendTestResult:
    """The outcome, waiting up to `wait_seconds` for it — the pattern Run now uses."""
    deadline = time.monotonic() + min(wait_seconds, MAX_TEST_WAIT_SECONDS)
    while True:
        found = await tester.collect(org_id=str(org_id), request_id=request_id)
        if found is not None:
            return found
        if time.monotonic() >= deadline:
            return SendTestResult(pending=True)
        await asyncio.sleep(TEST_POLL_SECONDS)
