"""The connection test, "Test this connection" (spec FR24, FR27; plan T4.3), run by
`scheduler-insight` alone.

The job carries ids only: the organization and the request the person waits on. The task
reads the organization's connection itself, checks it again against FR23 (the row could
have been written by anyone holding the install account), opens the key for **this**
address, and sends the constant check request, which holds no telemetry (AC35).

A test is a real, metered call (A51): it writes an `ai_runs` row with purpose `check`
before the call, counts its tokens to the month with no reservation, so it runs while no
limit is set (AC53), and records pass or fail on the connection only if the values it tested
are still the saved ones. The result for the person goes through migration 0070's function,
the only way this account may write `job_results`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nexovue_platform.ai.check_prompt import CHECK_PROMPT_VERSION, CHECK_REQUEST
from nexovue_platform.ai.connection import (
    AI_CONNECTION_PURPOSE,
    ConnectionRefused,
    check_connection,
    check_fingerprint,
    check_key,
    key_owner,
    provider_host,
)
from nexovue_platform.ai.contract import AIResponse, Outcome, estimate_tokens
from nexovue_platform.ai.formats import wire_format
from nexovue_platform.ai.jobs import (
    AI_CHECK_RESULT_TTL_SECONDS,
    CHECK_AI_CONNECTION_TASK,
    CHECK_CALL_SECONDS,
    CheckReason,
)
from nexovue_platform.checks.sealing import ConfiguredKeys, SealedBox, SealError, keyring_from
from nexovue_platform.telemetry.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncEngine

from nexovue_scheduler.insight import store, transport
from nexovue_scheduler.jobs import results
from nexovue_scheduler.jobs.registry import task
from nexovue_scheduler.probes.addresses import PrivateEndpoints
from nexovue_scheduler.process_engine import with_engine
from nexovue_scheduler.settings import get_settings

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Tested:
    """One test's result, as the person reads it: never a provider's words."""

    ok: bool
    reason: CheckReason | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    model_reported: str | None = None

    def payload(self, at: datetime) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "model_reported": self.model_reported,
            "checked_at": at.isoformat(),
        }


async def check(
    engine: AsyncEngine,
    *,
    org_id: str,
    request_id: str,
    requested_by: str | None,
    keys: ConfiguredKeys,
    endpoints: PrivateEndpoints,
    clock: Callable[[], datetime],
) -> Tested:
    """Test the organization's connection, and leave the result for whoever asked."""
    connection = await store.load_connection(engine, org_id)
    if connection is None:
        tested = Tested(ok=False, reason="no_connection")
    else:
        tested = await _test(
            engine,
            org_id=org_id,
            connection=connection,
            requested_by=requested_by,
            keys=keys,
            endpoints=endpoints,
            clock=clock,
        )
    logger.info(
        "an AI connection was tested", org_id=org_id, passed=tested.ok, reason=tested.reason
    )
    await results.store_ai_check(
        engine,
        org_id=org_id,
        request_id=request_id,
        payload=tested.payload(clock()),
        ttl_seconds=AI_CHECK_RESULT_TTL_SECONDS,
    )
    return tested


async def _test(
    engine: AsyncEngine,
    *,
    org_id: str,
    connection: store.StoredConnection,
    requested_by: str | None,
    keys: ConfiguredKeys,
    endpoints: PrivateEndpoints,
    clock: Callable[[], datetime],
) -> Tested:
    fingerprint = check_fingerprint(
        wire_format=connection.wire_format,
        base_url=connection.base_url,
        model=connection.model,
        region=connection.region_label,
        key_id=connection.key_id,
    )
    refused = _refusal(org_id, connection, keys)
    if isinstance(refused, Tested):
        await store.finish_check(
            engine,
            org_id=org_id,
            connection=connection,
            fingerprint=fingerprint,
            passed=False,
            at=clock(),
            run=None,
        )
        return refused
    key = refused
    fmt = wire_format(connection.wire_format)
    call = fmt.build(CHECK_REQUEST, model=connection.model, base_url=connection.base_url, key=key)
    run_id = await store.start_run(
        engine,
        store.RunStart(
            org_id=org_id,
            requested_by=requested_by,
            purpose="check",
            wire_format=connection.wire_format,
            model=connection.model,
            region_label=connection.region_label,
            provider_host=provider_host(connection.base_url),
            prompt_version=CHECK_PROMPT_VERSION,
            request_sha256=call.sha256(),
        ),
    )
    answer = transport.send(
        call,
        timeout_seconds=min(connection.call_seconds, CHECK_CALL_SECONDS),
        private_endpoints=endpoints,
    )
    tested, end = _ending(answer, fmt.parse, call.body, finished_at=clock())
    await store.finish_check(
        engine,
        org_id=org_id,
        connection=connection,
        fingerprint=fingerprint,
        passed=tested.ok,
        at=end.finished_at,
        run=(run_id, end),
    )
    return tested


def _refusal(
    org_id: str, connection: store.StoredConnection, keys: ConfiguredKeys
) -> Tested | str | None:
    """The opened key (or None, for a server with none), or why no call may be made."""
    try:
        check_connection(
            wire_format=connection.wire_format,
            base_url=connection.base_url,
            model=connection.model,
            region=connection.region_label,
        )
    except ConnectionRefused:
        return Tested(ok=False, reason="not_allowed")
    if connection.sealed_key is None:
        return None
    try:
        box = SealedBox(keyring_from(keys.current, keys.previous), purpose=AI_CONNECTION_PURPOSE)
        key = box.open(
            connection.sealed_key,
            owner=key_owner(org_id, connection.id, connection.base_url),
        )
    except (SealError, ValueError):
        return Tested(ok=False, reason="key_unreadable")
    try:
        check_key(key)
    except ConnectionRefused:
        return Tested(ok=False, reason="not_allowed")
    return key


def _ending(
    answer: transport.Answer,
    parse: Any,
    request_body: bytes,
    *,
    finished_at: datetime,
) -> tuple[Tested, store.RunEnd]:
    """What a call came to: the person's result, and the run row's ending."""
    if answer.failure is not None and answer.status is None:
        # A call that may have reached the provider is counted, overstated: the safe side.
        reached = answer.reached
        return Tested(ok=False, reason=_reason_of_failure(answer.failure)), store.RunEnd(
            outcome="unavailable",
            input_tokens=estimate_tokens(len(request_body)) if reached else None,
            output_tokens=0 if reached else None,
            tokens_estimated=reached,
            finished_at=finished_at,
        )
    if answer.failure == "too_large":
        response = AIResponse("invalid")
    else:
        response = parse(answer.status or 0, answer.body)
    counted = response.with_counts(request_body=request_body)
    passed = response.outcome == "ok"
    return Tested(
        ok=passed,
        reason=None if passed else _reason_of_outcome(response.outcome),
        input_tokens=counted.input_tokens,
        output_tokens=counted.output_tokens,
        model_reported=response.model_reported,
    ), store.RunEnd(
        outcome="done" if passed else response.outcome,
        input_tokens=counted.input_tokens,
        output_tokens=counted.output_tokens,
        tokens_estimated=counted.tokens_estimated,
        finished_at=finished_at,
    )


def _reason_of_failure(failure: transport.TransportFailure) -> CheckReason:
    return "invalid" if failure == "too_large" else failure


def _reason_of_outcome(outcome: Outcome) -> CheckReason:
    return "invalid" if outcome == "ok" else outcome


@task(CHECK_AI_CONNECTION_TASK)
def check_task(*, org_id: str, request_id: str, requested_by: str | None = None) -> None:
    settings = get_settings()
    with_engine(
        lambda engine: check(
            engine,
            org_id=org_id,
            request_id=request_id,
            requested_by=requested_by,
            keys=settings.ai_secret_keys,
            endpoints=settings.ai_endpoints,
            clock=lambda: datetime.now(UTC),
        )
    )
