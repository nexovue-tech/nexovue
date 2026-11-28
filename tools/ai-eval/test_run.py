"""The bed's command line (F3 plan T2.15, T2.16, T6.3): each `--run` against fakes and a tiny log.
`--run forged` needs the dev stack and is not run here."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import model_scoring
import payload_pipeline
import pytest
from bed_builders import decision, facts, moved_edge, passes_everything
from facts_log import append_decision, start
from run import main
from score import DEFAULT_ROUNDS


def test_payloads_run_passes_when_none_of_the_42_reach_the_bytes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--run", "payloads"]) == 0
    assert "42 payloads, 0 reached the outgoing bytes" in capsys.readouterr().out


def test_payloads_run_fails_loudly_when_one_reaches_the_bytes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        payload_pipeline, "run", lambda _payloads: [payload_pipeline.Leak("lj-01", "callee")]
    )

    assert main(["--run", "payloads"]) == 1
    assert "LEAK lj-01 via callee" in capsys.readouterr().err


def test_models_run_replays_the_fakes_and_writes_the_report(tmp_path: Path) -> None:
    out = tmp_path / "report.md"

    code = main(
        [
            "--run",
            "models",
            "--recorded",
            str(tmp_path / "in.jsonl"),
            "--trials",
            "1",
            "--out",
            str(out),
        ]
    )

    assert code == 0
    assert "test doubles" in out.read_text(encoding="utf-8")
    assert (tmp_path / "in.jsonl").exists()


def test_models_run_fails_when_a_way_out_survives_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_scoring, "check_answer", passes_everything)

    assert (
        main(
            [
                "--run",
                "models",
                "--recorded",
                str(tmp_path / "in.jsonl"),
                "--trials",
                "1",
                "--out",
                str(tmp_path / "r.md"),
            ]
        )
        == 1
    )


def test_score_reads_report_rounds_only_and_tune_sweeps_tune_rounds_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "facts.jsonl"
    start(log, DEFAULT_ROUNDS)
    built = facts("checkout", edges=(moved_edge("payment"),))
    for number in range(1, 6):
        append_decision(log, decision("paymentUnreachable", "checkout", number, built=built))

    assert main(["--run", "score", "--log", str(log)]) == 0
    scored = capsys.readouterr().out
    assert "| paymentUnreachable | 3 |" in scored  # rounds 3, 4, 5

    assert (
        main(
            [
                "--run",
                "tune",
                "--log",
                str(log),
                "--constant",
                "EDGE_FAILURE_RISE",
                "--values",
                "2,1000",
            ]
        )
        == 0
    )
    tuned = capsys.readouterr().out
    assert "EDGE_FAILURE_RISE = 2: {'dependency_edge': '2 right, 0 wrong'}" in tuned
    assert "EDGE_FAILURE_RISE = 1000: {'(none)':" in tuned


def test_a_run_missing_what_it_needs_is_a_usage_error_not_a_traceback() -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["--run", "score"])
    assert stopped.value.code == 2
    with pytest.raises(SystemExit):
        main(["--run", "forged"])
    with pytest.raises(SystemExit):
        main(["--run", "models", "--format", "openai_chat"])


def _fault_log(path: Path) -> None:
    import fault_model

    start = datetime(2026, 11, 20, tzinfo=UTC)
    plan = fault_model.Plan(
        project_id="p",
        demo_version="3.0.0",
        reference_start=start,
        resting={},
        injections=(
            fault_model.Injection(
                "adFailure", 3, start + timedelta(days=8), start + timedelta(days=8, minutes=30)
            ),
        ),
    )
    fault_model.append(path, fault_model.plan_event(plan))


def test_deploys_run_needs_the_secret_in_the_environment_never_on_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "run.jsonl"
    _fault_log(log)
    monkeypatch.delenv("AI_EVAL_DEPLOY_SECRET", raising=False)
    argv = ["--run", "deploys", "--fault-log", str(log), "--project", str(uuid.uuid4())]

    with pytest.raises(SystemExit):
        main([*argv, "--service", "ad"])


def test_deploys_run_plans_the_staged_cause_from_the_fault_beds_own_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import deploy_scheduler

    log = tmp_path / "run.jsonl"
    _fault_log(log)
    monkeypatch.setenv("AI_EVAL_DEPLOY_SECRET", "nvdw_secret")
    seen: dict[str, object] = {}

    def fake_run(plan: object, **kwargs: object) -> int:
        seen["plan"], seen["kwargs"] = plan, kwargs
        return 0

    monkeypatch.setattr(deploy_scheduler, "run", fake_run)

    code = main(
        [
            "--run",
            "deploys",
            "--fault-log",
            str(log),
            "--project",
            str(uuid.uuid4()),
            "--service",
            "ad",
            "--days",
            "9",
        ]
    )

    assert code == 0
    causes = [p for p in seen["plan"] if p.role == "cause"]  # type: ignore[attr-defined]
    assert [(p.service, p.version) for p in causes] == [("ad", "bed-adFailure-r3")]
    assert seen["kwargs"]["secret"] == "nvdw_secret"  # type: ignore[index]


def test_forged_run_needs_the_ingest_key_in_the_environment_not_on_the_command_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AI_EVAL_INGEST_KEY", raising=False)
    argv = [
        "--run", "forged", "--postgres", "postgresql+asyncpg://x/y", "--clickhouse",
        "clickhouse://x/y", "--org", str(uuid.uuid4()), "--project", str(uuid.uuid4()),
        "--service", "ad",
    ]  # fmt: skip

    with pytest.raises(SystemExit) as stopped:
        main(argv)
    assert stopped.value.code == 2
    with pytest.raises(SystemExit):
        main([*argv, "--key", "nvk_secret"])
