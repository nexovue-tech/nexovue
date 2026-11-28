"""AC49's target-aware score over a recorded run built by hand (F19, plan T6.3): a burn on
`payment` caught as it opens, one already open that escalates, one that does not, a finding
beside a burn with one email, and the reference week's burns against the demo's own baseline."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fault_model import (
    REFERENCE,
    BurnSeen,
    FindingSeen,
    Injection,
    MessageSeen,
    Observation,
    Plan,
    ServiceWindow,
    append,
    burns_event,
    events,
    findings_event,
    load,
    observation_event,
    plan_event,
    reference_services_event,
)
from fault_score import render, score
from fault_score_burns import render_burns, score_burns
from test_fault_score import finding

REFERENCE_START = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
SLOT = timedelta(minutes=90)
ACTIVE = timedelta(minutes=30)
# 100 requests, 1 failing a minute in the reference windows: the demo's own baseline of 1%.
BASELINE = (ServiceWindow("payment", 500, 5, 50.0),)
DURING = (ServiceWindow("payment", 500, 60, 50.0),)


def email(
    kind: str, at: datetime | None, state: str = "delivered", reason: str | None = None
) -> MessageSeen:
    return MessageSeen(kind, state, "email", reason, at)


def burn(
    name: str,
    opened: datetime,
    *,
    resolved: datetime | None = None,
    messages: tuple[MessageSeen, ...] = (),
    findings: tuple[str, ...] = (),
    service: str = "payment",
) -> BurnSeen:
    return BurnSeen(
        name,
        opened,
        resolved,
        "warning",
        f"{service} availability, 99.9% over 28 days",
        findings,
        messages,
    )


def run_log(tmp_path: Path, burns: list[BurnSeen], seen: list[FindingSeen]) -> Path:
    injections = [
        Injection("paymentFailure", n, start_of(n), start_of(n) + ACTIVE) for n in range(1, 6)
    ]
    log = tmp_path / "run.jsonl"
    append(
        log,
        plan_event(
            Plan("p", "3.0.0", REFERENCE_START, {"paymentFailure": "off"}, tuple(injections))
        ),
    )
    for injection in injections:
        append(
            log, observation_event(Observation("paymentFailure", injection.round, DURING, BASELINE))
        )
    append(log, reference_services_event(["payment"]))
    append(log, findings_event(REFERENCE_START + REFERENCE * 2, seen))
    append(log, burns_event(REFERENCE_START + REFERENCE * 2, burns))
    return log


def start_of(round_number: int) -> datetime:
    return REFERENCE_START + REFERENCE + SLOT * (round_number - 1)


def test_four_burns_opened_in_five_injections_pass_and_the_fifth_is_said_to_be_missed(
    tmp_path: Path,
) -> None:
    burns = [burn(f"b{n}", start_of(n) + timedelta(minutes=9)) for n in (1, 2, 3, 4)]
    card = score_burns(load(events(run_log(tmp_path, burns, []))))
    assert [i.how for i in card.injections] == ["opened"] * 4 + [None]
    assert card.verdict == "pass"
    assert card.injections[0].time_to_catch == timedelta(minutes=9)


def test_an_already_open_burn_that_escalates_is_caught_and_one_that_does_not_is_not(
    tmp_path: Path,
) -> None:
    long_open = burn(
        "long",
        start_of(1) - timedelta(hours=3),
        messages=(
            email("opened", start_of(1) - timedelta(hours=3)),
            email("escalated", start_of(1) + timedelta(minutes=12)),
        ),
    )
    quiet_open = burn("quiet", start_of(2) - timedelta(hours=3) + SLOT * 0, resolved=None)
    burns = [long_open, quiet_open] + [
        burn(f"b{n}", start_of(n) + timedelta(minutes=5)) for n in (3, 4, 5)
    ]
    card = score_burns(load(events(run_log(tmp_path, burns, []))))
    hows = [i.how for i in card.injections]
    assert hows[0] == "escalated"
    assert hows[1] is None
    assert card.already_open == 2
    assert card.already_open_escalated == 1
    assert card.verdict == "pass"


def test_a_finding_and_a_burn_in_one_injection_are_linked_with_the_emails_counted(
    tmp_path: Path,
) -> None:
    opened = finding(start_of(1) + timedelta(minutes=8), "payment")
    linked = burn(
        "b1",
        start_of(1) + timedelta(minutes=9),
        findings=(opened.id,),
        messages=(
            email("opened", None, "dropped", "Held back: the same news reached this channel"),
        ),
    )
    other = [burn(f"b{n}", start_of(n) + timedelta(minutes=9)) for n in (2, 3, 4, 5)]
    card = score_burns(load(events(run_log(tmp_path, [linked, *other], [opened]))))
    first = card.injections[0]
    assert first.finding_opened and first.linked
    assert (first.emails_delivered, first.emails_withheld) == (0, 1)
    assert card.with_both == 1 and card.linked == 1


def test_the_reference_week_reports_every_burn_beside_the_baseline_and_never_a_fixed_zero(
    tmp_path: Path,
) -> None:
    quiet = score_burns(load(events(run_log(tmp_path, [], []))))
    assert quiet.reference_burns == ()
    assert quiet.baseline_rate == 5 / 500
    noisy = tmp_path / "noisy"
    noisy.mkdir()
    during_reference = burn(
        "r1",
        REFERENCE_START + timedelta(days=2),
        resolved=REFERENCE_START + timedelta(days=2, hours=1),
    )
    card = score_burns(load(events(run_log(noisy, [during_reference], []))))
    assert [b.id for b in card.reference_burns] == ["r1"]
    text = render_burns(card)
    assert "demo" in text.lower()
    assert "1 burn" in text and "1.0%" in text


def test_burns_on_another_service_are_not_this_targets(tmp_path: Path) -> None:
    others = [
        burn(f"c{n}", start_of(n) + timedelta(minutes=9), service="cart") for n in range(1, 6)
    ]
    card = score_burns(load(events(run_log(tmp_path, others, []))))
    assert card.verdict.startswith("fail")


def test_the_ac36_table_still_renders_without_a_target_and_burns_render_apart(
    tmp_path: Path,
) -> None:
    run = load(events(run_log(tmp_path, [], [])))
    assert "## AC36" in render(score(run))


def test_a_message_to_the_owners_channel_is_an_email_too(tmp_path: Path) -> None:
    held = MessageSeen("opened", "dropped", "owners", "held back", None)
    sent = MessageSeen(
        "escalated", "delivered", "owners", None, start_of(2) + timedelta(minutes=10)
    )
    burns = [
        burn("b1", start_of(1) + timedelta(minutes=9), messages=(held,)),
        burn("b2", start_of(2) + timedelta(minutes=9), messages=(sent,)),
    ]
    card = score_burns(load(events(run_log(tmp_path, burns, []))))
    assert (card.injections[0].emails_delivered, card.injections[0].emails_withheld) == (0, 1)
    assert (card.injections[1].emails_delivered, card.injections[1].emails_withheld) == (1, 0)
