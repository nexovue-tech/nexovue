"""An organization's AI settings: connection, test, preview, consent, switch, opt-outs, usage
and runs (spec FR23 to FR32; design *API*).

**AI is off until the whole flow is done** (BR4): a passing test for the saved values, a
monthly limit, and an owner's consent to the saved address, format, region, key and facts
schema. The database's own check holds the switch to a limit and a consent for this very
address; the rest is here.

**A saved key can never be sent somewhere new** (FR23, AC34): changing the address or format
erases it in the same transaction unless the same request brings a new one, and every key
is sealed for one address (`key_owner`), so a key copied to another row or address will not
open. Changing anything an owner agreed to ends that consent and turns AI off; changing the
model keeps it, turns AI off until a fresh test, and clears the price (FR26, AC33). Each act
is audited with hosts and field names, never values (AC72), and owners are emailed after the
change commits, so a failed send cannot undo it (AC102).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final

from nexovue_platform.ai.check_prompt import CHECK_REQUEST
from nexovue_platform.ai.connection import (
    AI_CONNECTION_PURPOSE,
    MODEL_PATTERN,
    ConnectionRefused,
    check_connection,
    check_fingerprint,
    check_key,
    consent_fingerprint,
    is_private_address,
    key_owner,
    provider_host,
)
from nexovue_platform.ai.contract import canonical_json
from nexovue_platform.ai.explain_prompt import (
    INSTRUCTIONS,
    PROMPT_VERSION,
    SAMPLE_ALIASES,
    SAMPLE_MODEL_INPUT,
    SENDABLE_FIELDS,
    explain_request,
)
from nexovue_platform.ai.formats import wire_format
from nexovue_platform.ai.jobs import (
    AI_CHECK_RESULT_KIND,
    CHECK_AI_CONNECTION_TASK,
    CHECK_REASONS,
    check_request,
)
from nexovue_platform.ai.real_calls import formats_with_a_real_call
from nexovue_platform.ai_records import RUN_OUTCOMES, RUN_PURPOSES
from nexovue_platform.checks.sealing import ConfiguredKeys, SealedBox, keyring_from
from nexovue_platform.deployment import HOSTED
from nexovue_platform.email import EmailError, EmailSender, Message
from nexovue_platform.evidence import KINDS
from nexovue_platform.evidence.model_facts import SCHEMA_VERSION
from nexovue_platform.jobs import ENQUEUE, WAKE
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from nexovue_api import insight_account
from nexovue_api.ai import queries
from nexovue_api.ai.schemas import (
    AISettings,
    AliasRow,
    CheckAccepted,
    CheckResult,
    ConnectionView,
    Consent,
    HeaderRow,
    Missing,
    Preview,
    ProjectOptOut,
    RunRow,
    SaveConnection,
    SentView,
    SwitchState,
    UnavailableReason,
    Usage,
)
from nexovue_api.audit import service as audit
from nexovue_api.data.postgres.models import AIConnection, AIRun
from nexovue_api.errors.exceptions import (
    ConflictError,
    DependencyUnavailableError,
    InvalidRequestError,
    NotFoundError,
)

logger = get_logger(__name__)

# What each unavailable reason says (design, *AI settings*, Status states).
UNAVAILABLE_WORDS: Final[dict[str, str]] = {
    "hosted": "AI wording is not offered on the hosted service yet.",
    "account_missing": "AI is not available: the database account could not be created.",
    "no_key": "AI is not available: AI_SECRET_KEY is not set on this server.",
}
CONTAINER_NOT_RUNNING: Final = "The AI container is not running on this server."
NO_CONNECTION: Final = "Save a connection first."
CONSENT_MOVED: Final = (
    "The connection changed after you read it. Read what would be sent again, then agree."
)
MISSING_WORDS: Final[dict[str, str]] = {
    "connection": "a saved connection",
    "container": "the AI container running",
    "test": "a passing test of the saved values",
    "limit": "a monthly token limit",
    "consent": "an owner's agreement to the saved values",
}
MATCHES: Final = "Matches the hash of what was sent."
CANNOT_REPRODUCE: Final = "Cannot be reproduced: nexovue changed since this run."
EXPIRED: Final = "The facts this run sent have expired with their finding."
# The key's stand-in where a preview shows the header that carries it.
KEY_SHOWN_AS: Final = "(the saved key)"
_FACT_PURPOSES: Final = frozenset({"explain", "chat", "fix"})
_PER_MILLION: Final = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class Availability:
    """What this install offers, from the settings the API and the AI role share."""

    deployment: str
    keys: ConfiguredKeys
    insight_replicas: int
    app_base_url: str

    @property
    def container_running(self) -> bool:
        return self.insight_replicas > 0


@dataclass(frozen=True, slots=True)
class Actor:
    org_id: uuid.UUID
    user_id: uuid.UUID
    organization_name: str


# ----------------------------------------------------------------- reading


async def _unavailable(
    session: AsyncSession, availability: Availability
) -> UnavailableReason | None:
    if availability.deployment == HOSTED:
        return "hosted"
    if not await insight_account.account_exists(session):
        return "account_missing"
    if not availability.keys.current:
        return "no_key"
    return None


def _check_fingerprint(row: AIConnection) -> str:
    return check_fingerprint(
        wire_format=row.wire_format,
        base_url=row.base_url,
        model=row.model,
        region=row.region_label,
        key_id=None if row.key_id is None else str(row.key_id),
    )


def _consent_fingerprint(row: AIConnection) -> str:
    return consent_fingerprint(
        wire_format=row.wire_format,
        base_url=row.base_url,
        region=row.region_label,
        key_id=None if row.key_id is None else str(row.key_id),
    )


def _consent_current(row: AIConnection) -> bool:
    return (
        row.consented_at is not None
        and row.consented_base_url == row.base_url
        and row.consented_fingerprint == _consent_fingerprint(row)
        and row.consented_schema == SCHEMA_VERSION
    )


def _test_current(row: AIConnection) -> bool:
    return bool(row.checked_ok) and row.checked_fingerprint == _check_fingerprint(row)


def _missing(row: AIConnection | None, availability: Availability) -> list[Missing]:
    missing: list[Missing] = []
    if row is None:
        missing.append("connection")
    if not availability.container_running:
        missing.append("container")
    if row is not None:
        if not _test_current(row):
            missing.append("test")
        if row.monthly_token_limit is None:
            missing.append("limit")
        if not _consent_current(row):
            missing.append("consent")
    return missing


def _is_on(row: AIConnection | None, unavailable: UnavailableReason | None) -> bool:
    return row is not None and row.enabled and unavailable is None and _consent_current(row)


async def settings_view(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    may_manage: bool,
    availability: Availability,
) -> AISettings:
    """What `/settings/ai` reads. Without `MANAGE_AI`, on or off and nothing else (AC34)."""
    row = await queries.connection_of(session, org_id=org_id)
    unavailable = await _unavailable(session, availability)
    on = _is_on(row, unavailable)
    if not may_manage:
        return AISettings(on=on, full=False)
    # Asked only where the account exists: the catalog function errors on an unknown role.
    exposed = unavailable in (None, "no_key") and await insight_account.beat_lock_exposed(session)
    return AISettings(
        on=on,
        full=True,
        available=unavailable is None,
        unavailable_reason=unavailable,
        container_running=availability.container_running,
        beat_lock_exposed=exposed,
        off_reason=None if row is None or row.enabled else _off_reason(row.off_reason),
        missing=_missing(row, availability),
        connection=None if row is None else await _connection_view(session, row),
        formats_offered=list(formats_with_a_real_call()),
        consent_fingerprint=None if row is None else _consent_fingerprint(row),
        schema_version=SCHEMA_VERSION,
    )


def _off_reason(value: str | None) -> str | None:
    return value if value in ("person", "consent_ended") else None


async def _connection_view(session: AsyncSession, row: AIConnection) -> ConnectionView:
    consent: Consent | None = None
    if row.consented_at is not None:
        person = (
            None
            if row.consented_by is None
            else await queries.person(session, org_id=row.org_id, user_id=row.consented_by)
        )
        consent = Consent(
            by_user_id=row.consented_by,
            by_name=None if person is None else person.name,
            by_left=person is None or person.left,
            at=row.consented_at,
            current=_consent_current(row),
        )
    test_current = _test_current(row)
    return ConnectionView(
        wire_format=row.wire_format,
        base_url=row.base_url,
        provider_host=provider_host(row.base_url),
        address_kind="private" if is_private_address(row.base_url) else "public",
        model=row.model,
        region_label=row.region_label,
        has_key=row.sealed_key is not None,
        call_seconds=row.call_seconds,
        monthly_token_limit=row.monthly_token_limit,
        price_input=row.price_input,
        price_output=row.price_output,
        checked_at=row.checked_at,
        checked_ok=row.checked_ok,
        test_current=test_current,
        model_changed=(
            _consent_current(row) and row.checked_fingerprint is not None and not test_current
        ),
        consent=consent,
    )


# ---------------------------------------------------------------- changing


async def _refuse_where_unavailable(session: AsyncSession, availability: Availability) -> None:
    """Every write refuses where AI cannot be offered (AC40)."""
    reason = await _unavailable(session, availability)
    if reason is not None:
        raise ConflictError(UNAVAILABLE_WORDS[reason], reason=reason)


def _box(keys: ConfiguredKeys) -> SealedBox:
    if not keys.current:
        raise DependencyUnavailableError(UNAVAILABLE_WORDS["no_key"])
    return SealedBox(keyring_from(keys.current, keys.previous), purpose=AI_CONNECTION_PURPOSE)


def _checked(request: SaveConnection) -> None:
    try:
        check_connection(
            wire_format=request.wire_format,
            base_url=request.base_url,
            model=request.model,
            region=request.region_label,
        )
        if request.key is not None:
            check_key(request.key)
    except ConnectionRefused as refused:
        raise InvalidRequestError(str(refused), reason=refused.reason) from refused


def _end_consent(row: AIConnection) -> None:
    row.consented_by = None
    row.consented_at = None
    row.consented_base_url = None
    row.consented_fingerprint = None
    row.consented_schema = None


def _host_or_none(base_url: str | None) -> str | None:
    return None if base_url is None else provider_host(base_url)


async def save_connection(
    session: AsyncSession,
    *,
    actor: Actor,
    request: SaveConnection,
    availability: Availability,
    email: EmailSender | None,
) -> AISettings:
    await _refuse_where_unavailable(session, availability)
    _checked(request)
    row = await queries.connection_of(session, org_id=actor.org_id, lock=True)
    if row is None:
        row = await _create(session, actor=actor, request=request, keys=availability.keys)
        told: _Told | None = None
    else:
        told = _change(session, actor=actor, row=row, request=request, keys=availability.keys)
    await session.commit()
    if told is not None:
        await _tell_owners(session, actor=actor, told=told, availability=availability, email=email)
    return await settings_view(
        session, org_id=actor.org_id, may_manage=True, availability=availability
    )


async def _create(
    session: AsyncSession, *, actor: Actor, request: SaveConnection, keys: ConfiguredKeys
) -> AIConnection:
    row = AIConnection(
        org_id=actor.org_id,
        wire_format=request.wire_format,
        base_url=request.base_url,
        model=request.model,
        region_label=request.region_label or None,
        call_seconds=request.call_seconds,
        monthly_token_limit=request.monthly_token_limit,
        price_input=request.price_input,
        price_output=request.price_output,
        enabled=False,
    )
    session.add(row)
    await session.flush()
    if request.key is not None:
        _seal(row, request.key, keys)
    audit.record_ai_event(
        session,
        org_id=actor.org_id,
        actor_user_id=actor.user_id,
        action=audit.AI_CONNECTION_CHANGED,
        target_id=row.id,
        detail={"created": True, "old_host": None, "new_host": provider_host(row.base_url)},
    )
    return row


def _seal(row: AIConnection, key: str, keys: ConfiguredKeys) -> None:
    """A new key, sealed for this row's address, with a new id, so consent and the test's
    fingerprint both move with it."""
    row.key_id = uuid.uuid4()
    row.sealed_key = _box(keys).seal(
        key, owner=key_owner(str(row.org_id), str(row.id), row.base_url)
    )


@dataclass(frozen=True, slots=True)
class _Told:
    """What owners are emailed about, after the change commits (AC102)."""

    what: str
    old_host: str
    new_host: str
    until: str


def _change(
    session: AsyncSession,
    *,
    actor: Actor,
    row: AIConnection,
    request: SaveConnection,
    keys: ConfiguredKeys,
) -> _Told | None:
    old_host = provider_host(row.base_url)
    address_moved = request.base_url != row.base_url or request.wire_format != row.wire_format
    key_given = request.key is not None
    model_moved = request.model != row.model
    changed = [
        name
        for name, before, after in (
            ("wire_format", row.wire_format, request.wire_format),
            ("base_url", row.base_url, request.base_url),
            ("model", row.model, request.model),
            ("region_label", row.region_label, request.region_label or None),
            ("call_seconds", row.call_seconds, request.call_seconds),
            ("price_input", row.price_input, request.price_input),
            ("price_output", row.price_output, request.price_output),
        )
        if before != after
    ]
    limit_moved = request.monthly_token_limit != row.monthly_token_limit
    had_consent = row.consented_at is not None
    was_on = row.enabled

    row.wire_format = request.wire_format
    row.base_url = request.base_url
    row.model = request.model
    row.region_label = request.region_label or None
    row.call_seconds = request.call_seconds
    row.monthly_token_limit = request.monthly_token_limit
    row.price_input, row.price_output = request.price_input, request.price_output
    if address_moved and not key_given:
        row.sealed_key, row.key_id = None, None
    if key_given:
        _seal(row, request.key or "", keys)
    consent_ends = had_consent and not _consent_current(row)
    if consent_ends:
        _end_consent(row)
        row.enabled, row.off_reason = False, "consent_ended"
    if model_moved:
        # The price was for the old model: cleared until it is entered again (FR29).
        row.price_input, row.price_output = None, None
        if row.enabled:
            row.enabled, row.off_reason = False, "person"

    new_host = provider_host(row.base_url)
    detail_hosts = {"old_host": old_host, "new_host": new_host}
    if changed:
        _record(
            session, actor, row, audit.AI_CONNECTION_CHANGED, {"fields": changed, **detail_hosts}
        )
    if key_given:
        _record(session, actor, row, audit.AI_KEY_REPLACED, detail_hosts)
    if limit_moved:
        _record(session, actor, row, audit.AI_LIMIT_CHANGED, {"fields": ["monthly_token_limit"]})
    if consent_ends:
        _record(session, actor, row, audit.AI_CONSENT_ENDED, detail_hosts)
    if was_on and not row.enabled:
        _record(session, actor, row, audit.AI_TURNED_OFF, {"by": "change"})
    if consent_ends:
        return _Told(
            what="An admin changed the AI connection, which ended the owners' agreement.",
            old_host=old_host,
            new_host=new_host,
            until="an owner agrees again",
        )
    if model_moved:
        return _Told(
            what=f"An admin changed the AI model to {row.model}.",
            old_host=old_host,
            new_host=new_host,
            until="a test of the new model passes and someone turns it on",
        )
    return None


def _record(
    session: AsyncSession,
    actor: Actor,
    row: AIConnection,
    action: str,
    detail: Mapping[str, object],
) -> None:
    audit.record_ai_event(
        session,
        org_id=actor.org_id,
        actor_user_id=actor.user_id,
        action=action,
        target_id=row.id,
        detail=dict(detail),
    )


async def _tell_owners(
    session: AsyncSession,
    *,
    actor: Actor,
    told: _Told,
    availability: Availability,
    email: EmailSender | None,
) -> None:
    """Each owner, after the change committed: a refused send is logged, never undone."""
    if email is None:
        logger.warning("an AI connection change was not emailed: no mail provider configured")
        return
    link = f"{availability.app_base_url.rstrip('/')}/settings/ai"
    for address in await queries.owner_addresses(session, org_id=actor.org_id):
        try:
            await email.send(
                to=address,
                message=Message.AI_CONNECTION_CHANGED,
                data={
                    "organization": actor.organization_name,
                    "what": told.what,
                    "old_host": told.old_host,
                    "new_host": told.new_host,
                    "until": told.until,
                    "link": link,
                },
            )
        except EmailError:
            logger.error("an owner was not emailed about an AI connection change")


async def delete_connection(
    session: AsyncSession,
    *,
    actor: Actor,
    availability: Availability,
    email: EmailSender | None,
) -> None:
    """Erase the key, end consent and turn off, by removing the row (AC34)."""
    await _refuse_where_unavailable(session, availability)
    row = await queries.connection_of(session, org_id=actor.org_id, lock=True)
    if row is None:
        raise NotFoundError(NO_CONNECTION)
    old_host = provider_host(row.base_url)
    had_consent = row.consented_at is not None
    _record(
        session, actor, row, audit.AI_CONNECTION_DELETED, {"old_host": old_host, "new_host": None}
    )
    if row.enabled:
        _record(session, actor, row, audit.AI_TURNED_OFF, {"by": "delete"})
    await queries.delete_connection(session, org_id=actor.org_id)
    await session.commit()
    if had_consent:
        await _tell_owners(
            session,
            actor=actor,
            told=_Told(
                what="An admin removed the AI connection, which ended the owners' agreement.",
                old_host=old_host,
                new_host="none",
                until="a connection is saved and an owner agrees again",
            ),
            availability=availability,
            email=email,
        )


async def start_check(
    session: AsyncSession, *, actor: Actor, availability: Availability
) -> CheckAccepted:
    """A test, as a job on the AI container: the API never calls a provider (FR24, AC35)."""
    await _refuse_where_unavailable(session, availability)
    row = await queries.connection_of(session, org_id=actor.org_id)
    if row is None:
        raise ConflictError(NO_CONNECTION)
    if not availability.container_running:
        raise ConflictError(CONTAINER_NOT_RUNNING)
    request_id = uuid.uuid4()
    job = check_request(
        org_id=str(actor.org_id), request_id=str(request_id), requested_by=str(actor.user_id)
    )
    # In this transaction, with the audit row; the workers are woken when it commits.
    await session.execute(text(ENQUEUE), job.parameters())
    await session.execute(text(WAKE), job.wake_parameters())
    host = provider_host(row.base_url)
    _record(session, actor, row, audit.AI_CONNECTION_TESTED, {"old_host": host, "new_host": host})
    return CheckAccepted(request_id=request_id)


async def check_result(
    session: AsyncSession, *, org_id: uuid.UUID, request_id: uuid.UUID
) -> CheckResult:
    """The test's result, or pending while its job waits. Another organization's id, or an
    id never issued, is not found (AC35)."""
    payload = await queries.check_payload(
        session, org_id=org_id, request_id=request_id, kind=AI_CHECK_RESULT_KIND
    )
    if payload is not None:
        ok, reason = payload.get("ok"), payload.get("reason")
        return CheckResult(
            pending=False,
            ok=ok if isinstance(ok, bool) else None,
            reason=reason if reason in CHECK_REASONS else None,
            input_tokens=_count(payload.get("input_tokens")),
            output_tokens=_count(payload.get("output_tokens")),
        )
    if await queries.check_queued(
        session, org_id=org_id, request_id=request_id, task=CHECK_AI_CONNECTION_TASK
    ):
        return CheckResult(pending=True)
    raise NotFoundError("No such test.")


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


async def preview(session: AsyncSession, *, org_id: uuid.UUID) -> Preview:
    """The exact request for the newest finding's stored facts, as `WireFormat.build` makes
    it for the saved connection, the key shown as a stand-in (FR25, AC36)."""
    row = await queries.connection_of(session, org_id=org_id)
    if row is None:
        raise ConflictError(NO_CONNECTION)
    newest = await queries.newest_input(session, org_id=org_id)
    model_input = SAMPLE_MODEL_INPUT if newest is None else (newest.model_input or {})
    names = SAMPLE_ALIASES if newest is None else dict((newest.aliases or {}).get("names", {}))
    call = wire_format(row.wire_format).build(
        explain_request(model_input),
        model=row.model,
        base_url=row.base_url,
        key=KEY_SHOWN_AS if row.sealed_key is not None else None,
    )
    body = call.body.decode()
    used = sorted(alias for alias in names if alias in _strings_in(model_input))
    return Preview(
        sample=newest is None,
        finding_id=None if newest is None else newest.finding_id,
        project_id=None if newest is None else newest.project_id,
        read_at=None if newest is None else newest.read_at,
        summary=_summary(model_input, len(used), row, sample=newest is None),
        method=call.method,
        url=call.url,
        headers=[HeaderRow(name=name, value=value) for name, value in call.headers],
        instructions=INSTRUCTIONS,
        body=body,
        request_sha256=call.sha256(),
        prompt_version=PROMPT_VERSION,
        aliases=[AliasRow(alias=alias, stands_for=str(names[alias])) for alias in used],
        sendable_fields=list(SENDABLE_FIELDS),
        fact_kinds=list(KINDS),
    )


def _strings_in(value: object) -> set[str]:
    """Every string a stored input holds: the aliases it sends are among them."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return set().union(*(_strings_in(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_strings_in(item) for item in value))
    return set()


def _summary(
    model_input: dict[str, object], aliases: int, row: AIConnection, *, sample: bool
) -> list[str]:
    facts = model_input.get("facts")
    count = len(facts) if isinstance(facts, list) else 0
    lines = [
        f"This sends {count} facts about one finding: numbers, minutes from the finding's "
        f"start, fixed labels, and {aliases} stand-in names such as service_1.",
        "It sends no service, endpoint or project names, no log or error text, and no "
        "attributes from your telemetry.",
        f"It goes to {provider_host(row.base_url)}, in the {row.wire_format} format, for the "
        f"model {row.model}. That provider's terms decide what it keeps.",
    ]
    if sample:
        lines.insert(0, "No finding has facts yet, so this is a built-in sample.")
    return lines


async def consent(
    session: AsyncSession,
    *,
    actor: Actor,
    fingerprint: str,
    schema_version: int,
    availability: Availability,
    now: datetime,
) -> AISettings:
    """An owner agrees to what they read: refused if it moved since (FR26, AC33)."""
    await _refuse_where_unavailable(session, availability)
    row = await queries.connection_of(session, org_id=actor.org_id, lock=True)
    if row is None:
        raise ConflictError(NO_CONNECTION)
    if fingerprint != _consent_fingerprint(row) or schema_version != SCHEMA_VERSION:
        raise ConflictError(CONSENT_MOVED)
    old_host = _host_or_none(row.consented_base_url)
    row.consented_by = actor.user_id
    row.consented_at = now
    row.consented_base_url = row.base_url
    row.consented_fingerprint = fingerprint
    row.consented_schema = SCHEMA_VERSION
    _record(
        session,
        actor,
        row,
        audit.AI_DISCLOSURE_AGREED,
        {"old_host": old_host, "new_host": provider_host(row.base_url), "schema": SCHEMA_VERSION},
    )
    await session.commit()
    return await settings_view(
        session, org_id=actor.org_id, may_manage=True, availability=availability
    )


async def switch(
    session: AsyncSession, *, actor: Actor, on: bool, availability: Availability
) -> SwitchState:
    """On only when nothing is missing, naming what is (FR27, AC31); off always."""
    await _refuse_where_unavailable(session, availability)
    row = await queries.connection_of(session, org_id=actor.org_id, lock=True)
    if on:
        missing = _missing(row, availability)
        if row is None or missing:
            words = ", ".join(MISSING_WORDS[item] for item in missing)
            raise ConflictError(f"AI cannot be turned on yet. It needs {words}.", missing=missing)
        if not row.enabled:
            row.enabled, row.off_reason = True, None
            _record(session, actor, row, audit.AI_TURNED_ON, {"by": "person"})
    elif row is not None and row.enabled:
        row.enabled, row.off_reason = False, "person"
        _record(session, actor, row, audit.AI_TURNED_OFF, {"by": "person"})
    return SwitchState(on=row is not None and row.enabled)


async def project_opt_outs(session: AsyncSession, *, org_id: uuid.UUID) -> list[ProjectOptOut]:
    return [
        ProjectOptOut(project_id=project_id, name=name, opted_out=opted)
        for project_id, name, opted in await queries.projects_with_opt_outs(session, org_id=org_id)
    ]


async def set_opt_out(
    session: AsyncSession,
    *,
    actor: Actor,
    project_id: uuid.UUID,
    opted_out: bool,
    availability: Availability,
    now: datetime,
) -> ProjectOptOut:
    """Keep a project out of AI wording, or let it back (FR28). The project is proved to be
    this organization's first; an opt-out stops queued wording in the same transaction."""
    await _refuse_where_unavailable(session, availability)
    project = await queries.live_project(session, org_id=actor.org_id, project_id=project_id)
    if project is None:
        raise NotFoundError("No such project.")
    if opted_out:
        changed = await queries.opt_out(
            session, org_id=actor.org_id, project_id=project_id, person_id=actor.user_id, now=now
        )
    else:
        changed = await queries.opt_in(session, org_id=actor.org_id, project_id=project_id)
    if changed:
        audit.record_ai_event(
            session,
            org_id=actor.org_id,
            actor_user_id=actor.user_id,
            action=audit.AI_PROJECT_OPTED_OUT if opted_out else audit.AI_PROJECT_OPTED_IN,
            target_id=project_id,
            target_kind=audit.PROJECT_TARGET,
        )
    return ProjectOptOut(project_id=project_id, name=project.name, opted_out=opted_out)


def _first_of_next_month(month: date) -> date:
    return date(month.year + month.month // 12, month.month % 12 + 1, 1)


async def usage(session: AsyncSession, *, org_id: uuid.UUID, now: datetime) -> Usage:
    """This month's tokens, the limit, the reset date and, from typed prices, estimates."""
    month = date(now.year, now.month, 1)
    resets_on = _first_of_next_month(month)
    row = await queries.usage_month(session, org_id=org_id, month=month)
    connection = await queries.connection_of(session, org_id=org_id)
    since = datetime(month.year, month.month, 1, tzinfo=UTC)
    until = datetime(resets_on.year, resets_on.month, 1, tzinfo=UTC)
    input_tokens, output_tokens = await queries.run_tokens(
        session, org_id=org_id, since=since, until=until
    )
    limit = None if connection is None else connection.monthly_token_limit
    estimated = worst = None
    if connection is not None and (
        connection.price_input is not None or connection.price_output is not None
    ):
        price_in = connection.price_input or Decimal(0)
        price_out = connection.price_output or Decimal(0)
        estimated = (input_tokens * price_in + output_tokens * price_out) / _PER_MILLION
        if limit is not None:
            worst = limit * max(price_in, price_out) / _PER_MILLION
    return Usage(
        month=month,
        resets_on=resets_on,
        used_tokens=0 if row is None else row.used_tokens,
        reserved_tokens=0 if row is None else row.reserved_tokens,
        monthly_token_limit=limit,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost=estimated,
        worst_case_cost=worst,
    )


async def run_list(session: AsyncSession, *, org_id: uuid.UUID) -> list[RunRow]:
    """Runs, newest first. Every closed word this account wrote is mapped again on read,
    since the AI account wrote them (design, *Validation*)."""
    consents = await queries.consented_hosts(session, org_id=org_id)
    return [_run_row(run, consents) for run in await queries.runs(session, org_id=org_id)]


def _run_row(run: AIRun, consents: Sequence[tuple[str, datetime]]) -> RunRow:
    sendable = set(SENDABLE_FIELDS)
    if run.purpose not in _FACT_PURPOSES:
        host_consent = "not_applicable"
    elif any(host == run.provider_host and at <= run.started_at for host, at in consents):
        host_consent = "consented"
    else:
        host_consent = "not_consented"
    finished = run.finished_at
    return RunRow(
        id=run.id,
        started_at=run.started_at,
        finished_at=finished,
        seconds=None if finished is None else (finished - run.started_at).total_seconds(),
        purpose=run.purpose if run.purpose in RUN_PURPOSES else "unknown",
        outcome=run.outcome if run.outcome in RUN_OUTCOMES else "unknown",
        finding_id=run.subject_id,
        provider_host=run.provider_host,
        model=run.model if MODEL_PATTERN.fullmatch(run.model or "") else None,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        tokens_estimated=run.tokens_estimated,
        fields_sent=[name for name in run.fields_sent if name in sendable],
        by_person=run.requested_by is not None,
        host_consent=host_consent,
    )


async def sent(session: AsyncSession, *, org_id: uuid.UUID, run_id: uuid.UUID) -> SentView:
    """What a run sent, rebuilt from what is stored, with whether it matches the hash of the
    real bytes (FR29, AC36). `ai_runs` holds no content (FD9), so this is all there is."""
    run = await queries.run(session, org_id=org_id, run_id=run_id)
    if run is None:
        raise NotFoundError("No such run.")
    if run.purpose == "check":
        request = CHECK_REQUEST
        facts = None
    else:
        stored = (
            None
            if run.explanation_id is None
            else await queries.explanation(
                session, org_id=org_id, explanation_id=run.explanation_id
            )
        )
        if stored is None or stored.model_input is None:
            return SentView(
                match="expired", statement=EXPIRED, instructions=None, body=None, facts=None
            )
        request = explain_request(stored.model_input)
        facts = canonical_json(stored.model_input).decode()
    # The body never holds the key or the address, so any base URL rebuilds the same bytes.
    call = wire_format(run.wire_format).build(
        request, model=run.model, base_url="https://provider.invalid", key=None
    )
    if call.sha256() == run.request_sha256:
        return SentView(
            match="matches",
            statement=MATCHES,
            instructions=request.instructions,
            body=call.body.decode(),
            facts=facts,
        )
    return SentView(
        match="cannot_reproduce",
        statement=CANNOT_REPRODUCE,
        instructions=None,
        body=None,
        facts=facts,
    )
