"""A requester's one email per event (F5 plan T4.8; spec D46, AC64).

The sweep and the pack job mark a row `email_due`; `fixes.notify_requesters` takes it, in a
transaction committed before the send, and sends a fixed template with a finding link
alone: no path, no file, no model words. A second run sends nothing; a requester who
cleared the box, has no verified address, or has left gets nothing. Each test's rows are
its own tenant's; the sender is a recording fake.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from alert_fixtures import AlertTenant, postgres_reachable
from live import require_stack
from nexovue_platform.email import Message
from nexovue_platform.email.templates import render
from propose_fix_fixtures import FOLDER, PATH, a_proposing_fix
from recording_site import RecordingSite
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from wording_fixtures import connect, finding

from nexovue_scheduler.fixes import notify

require_stack(
    reachable=postgres_reachable(),
    suite="apps/scheduler/tests/test_fix_notify.py",
    remedy="Postgres is not reachable — run `just up`",
)

BASE: str = "https://console.example"


class Recording:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(
        self, *, to: str, message: Message, data: dict[str, Any], idempotency_key: str | None = None
    ) -> None:
        self.sent.append({"to": to, "message": message, "data": data, "key": idempotency_key})


async def _due(
    install: AsyncEngine,
    site: RecordingSite,
    tenant: AlertTenant,
    at: datetime,
    *,
    state: str,
    notify_requester: bool = True,
) -> str:
    await connect(install, tenant, site, now=at)
    made = await a_proposing_fix(install, tenant, await finding(install, tenant, now=at), at=at)
    reason = None if state in ("proposed", "gathered") else "withheld_K6"
    async with install.begin() as connection:
        await connection.execute(
            text("DELETE FROM finding_fix_source_packs WHERE fix_id = :id"), {"id": made.fix_id}
        )
        await connection.execute(
            text(
                "UPDATE finding_fixes SET state = :state, reason = :reason, email_due = true, "
                "notify_requester = :notify WHERE id = :id"
            ),
            {"id": made.fix_id, "state": state, "reason": reason, "notify": notify_requester},
        )
    return made.fix_id


def _org(tenant: AlertTenant) -> str:
    """Each test's run is narrowed to its own organization, so it never takes another
    test's owed email."""
    return str(tenant.org_id)


async def _owed_here(install: AsyncEngine, tenant: AlertTenant) -> int:
    async with install.connect() as connection:
        return int(
            await connection.scalar(
                text("SELECT count(*) FROM finding_fixes WHERE org_id = :org AND email_due"),
                {"org": tenant.org_id},
            )
        )


async def test_ac64_a_written_suggestion_sends_one_fixed_email_with_a_link_alone(
    ai_install: AsyncEngine, provider_site: RecordingSite, ai_tenant: AlertTenant, now: datetime
) -> None:
    fix = await _due(ai_install, provider_site, ai_tenant, now, state="proposed")
    sender = Recording()

    await notify.notify(ai_install, sender=sender, app_base_url=BASE, only_org=_org(ai_tenant))
    await notify.notify(ai_install, sender=sender, app_base_url=BASE, only_org=_org(ai_tenant))

    mine = [each for each in sender.sent if each["key"] == f"fix-{fix}-proposed"]
    assert len(mine) == 1
    [email] = mine
    assert email["message"] == Message.FIX_SUGGESTION_READY
    assert set(email["data"]) == {"link"} and email["data"]["link"].startswith(f"{BASE}/findings/")
    subject, body = render(email["message"], email["data"])
    assert PATH not in body and FOLDER not in body and PATH not in subject
    assert await _owed_here(ai_install, ai_tenant) == 0


async def test_ac64_a_withheld_suggestion_sends_the_ended_template(
    ai_install: AsyncEngine, provider_site: RecordingSite, ai_tenant: AlertTenant, now: datetime
) -> None:
    fix = await _due(ai_install, provider_site, ai_tenant, now, state="refused")
    sender = Recording()

    await notify.notify(ai_install, sender=sender, app_base_url=BASE, only_org=_org(ai_tenant))

    [email] = [each for each in sender.sent if each["key"] == f"fix-{fix}-refused"]
    assert email["message"] == Message.FIX_SUGGESTION_ENDED


async def test_d46_a_requester_who_cleared_the_box_gets_nothing_and_nothing_stays_owed(
    ai_install: AsyncEngine, provider_site: RecordingSite, ai_tenant: AlertTenant, now: datetime
) -> None:
    fix = await _due(
        ai_install, provider_site, ai_tenant, now, state="proposed", notify_requester=False
    )
    sender = Recording()

    await notify.notify(ai_install, sender=sender, app_base_url=BASE, only_org=_org(ai_tenant))

    assert not [each for each in sender.sent if fix in str(each["key"])]
    assert await _owed_here(ai_install, ai_tenant) == 0


async def test_with_no_mail_configured_rows_wait(
    ai_install: AsyncEngine, provider_site: RecordingSite, ai_tenant: AlertTenant, now: datetime
) -> None:
    await _due(ai_install, provider_site, ai_tenant, now, state="proposed")

    assert await notify.notify(
        ai_install, sender=None, app_base_url=BASE, only_org=_org(ai_tenant)
    ) == notify.Notified(0, 0)
    assert await _owed_here(ai_install, ai_tenant) == 1
