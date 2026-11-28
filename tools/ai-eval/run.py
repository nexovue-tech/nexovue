"""The bed's command line: `just ai-eval --run <name>` (plan T2.15, T2.16, T6.3).

    --run score      a facts log's report rounds, as the rules-only report's tables
    --run tune       one constant swept over values on the tune rounds only
    --run payloads   all 42 payloads through the pipeline with no model (AC76): none may leak
    --run models     recorded inputs replayed over providers: injection, wording and cost
    --run forged     forged spans through the real intake (needs the dev stack; AC76)
    --run deploys    the deploy scheduler: signed deploys through the real webhook for 7+ days

`models` uses the fakes unless `--format` names a real option. **A real model is the founder's
run, after Phase 2 (foundation item 31):** its key is read from `AI_EVAL_KEY`, never printed, and
nothing here has been run against one. The fakes test the harness, not any model.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import bed_report
import facts_log
import forged as forged_module
import model_runs
import model_scoring
import payload_pipeline
import replay
import score
from fixtures.cases import INPUTS
from fixtures.payloads import LOGJACK_STANDINS

KEY_VARIABLE: Final = "AI_EVAL_KEY"
DEFAULT_TRIALS: Final = 10
RUNS: Final = ("score", "tune", "payloads", "models", "forged", "deploys")
SECRET_VARIABLE: Final = "AI_EVAL_DEPLOY_SECRET"
INGEST_KEY_VARIABLE: Final = "AI_EVAL_INGEST_KEY"


def run_score(arguments: argparse.Namespace) -> int:
    plan, decisions = facts_log.read(arguments.log)
    scored = score.score_all(score.in_rounds(decisions, plan, "report"))
    text = score.render_tables(scored)
    if arguments.out:
        arguments.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


def run_tune(arguments: argparse.Namespace) -> int:
    plan, decisions = facts_log.read(arguments.log)
    values = [_number(each) for each in arguments.values.split(",")]
    for value, counts in replay.sweep(decisions, plan, arguments.constant, values).items():
        stated = {
            rule or "(none)": f"{c.right} right, {c.wrong} wrong" for rule, c in counts.items()
        }
        print(f"{arguments.constant} = {value}: {stated}")
    return 0


def _number(text: str) -> Any:
    return int(text) if text.lstrip("-").isdigit() else float(text)


def run_payloads() -> int:
    leaks = payload_pipeline.run(list(LOGJACK_STANDINS))
    print(f"{len(LOGJACK_STANDINS)} payloads, {len(leaks)} reached the outgoing bytes")
    for leak in leaks:
        print(f"  LEAK {leak.payload_id} via {leak.field}", file=sys.stderr)
    return 1 if leaks else 0


def _providers(arguments: argparse.Namespace) -> dict[str, model_runs.Provider]:
    if arguments.format_name is None:
        return dict(model_runs.FAKES)
    return {
        arguments.model: model_runs.http_provider(
            format_name=arguments.format_name,
            base_url=arguments.base_url,
            model=arguments.model,
            key=os.environ.get(KEY_VARIABLE) or None,
            private=arguments.private,
        )
    }


def run_models(arguments: argparse.Namespace) -> int:
    recorded_path: Path = arguments.recorded
    if not recorded_path.exists():
        model_runs.record_inputs(recorded_path, INPUTS)
    recorded = model_runs.load_inputs(recorded_path)
    records = model_runs.replay(_providers(arguments), recorded, trials=arguments.trials)
    scored = model_scoring.score_all(records, INPUTS)
    text = bed_report.render_bed_report(
        scored, model_scoring.cost_by_provider(records), machine=arguments.machine
    )
    if arguments.out:
        arguments.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    return (
        1 if any(row.way_out for row in model_scoring.injection_by_provider(scored).values()) else 0
    )


def _ingest_key() -> str:
    key = os.environ.get(INGEST_KEY_VARIABLE)
    if not key:
        parser().error(f"--run forged needs {INGEST_KEY_VARIABLE} in the environment")
    return key


def run_forged(arguments: argparse.Namespace) -> int:
    """Forged exports through the real receiver. Needs the stack's receiver, workers and
    detection running; the observation is the evidence job's own read (`live_reader`). The project's
    ingest key comes from `AI_EVAL_INGEST_KEY`, never the command line."""
    key = _ingest_key()
    from live_reader import finding_observer, live_reader
    from sqlalchemy.ext.asyncio import create_async_engine

    from nexovue_scheduler.evidence.store import connect_store

    engine = create_async_engine(arguments.postgres)
    reader = live_reader(
        engine,
        connect_store(arguments.clickhouse),
        org_id=arguments.org,
        project_id=arguments.project,
    )
    observe = finding_observer(
        engine, reader, org_id=arguments.org, project_id=arguments.project, wait=arguments.wait
    )
    specs = forged_module.specs_for(
        arguments.service,
        start=datetime.now(UTC) + timedelta(minutes=1),
        repeats=arguments.repeats,
        spacing=timedelta(minutes=arguments.spacing_minutes),
    )
    trials = asyncio.run(
        forged_module.run_trials(
            specs, post=forged_module.poster(arguments.receiver, key), observe=observe
        )
    )
    print(forged_module.render(forged_module.tally(trials)))
    return 0


def run_deploys(arguments: argparse.Namespace) -> int:
    """Post the plan's signed deploys when their time comes. The project's deploy secret is read
    from `AI_EVAL_DEPLOY_SECRET`, never the command line, and never printed."""
    import deploy_scheduler
    import fault_model
    import httpx

    secret = os.environ.get(SECRET_VARIABLE)
    if not secret:
        parser().error(f"--run deploys needs {SECRET_VARIABLE} in the environment")
    run_log = fault_model.load(fault_model.events(arguments.fault_log))
    windows = [
        deploy_scheduler.Window(each.fault, each.round, each.start)
        for each in run_log.plan.injections
    ]
    plan = deploy_scheduler.build_plan(
        services=arguments.service,
        windows=windows,
        start=run_log.plan.reference_start,
        days=arguments.days,
    )
    client = httpx.Client(timeout=10.0)

    def post(url: str, body: bytes, headers: Mapping[str, str]) -> int:
        return client.post(url, content=body, headers=dict(headers)).status_code

    posted = deploy_scheduler.run(
        plan,
        base_url=arguments.api,
        project_id=str(arguments.project),
        secret=secret,
        post=post,
        ledger=arguments.ledger,
    )
    print(f"posted {posted} deploys")
    return 0


def parser() -> argparse.ArgumentParser:
    built = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    built.add_argument("--run", required=True, choices=RUNS)
    built.add_argument("--log", type=Path, help="the facts log (score, tune)")
    built.add_argument("--out", type=Path)
    built.add_argument("--constant", help="a tunable constant's name (tune)")
    built.add_argument("--values", help="comma-separated values (tune)")
    built.add_argument("--recorded", type=Path, default=Path("recorded-inputs.jsonl"))
    built.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    built.add_argument("--machine", default="not stated")
    built.add_argument("--format", dest="format_name", help="a real wire format (founder's run)")
    built.add_argument("--base-url")
    built.add_argument("--model")
    built.add_argument("--private", default="")
    built.add_argument("--receiver", default="http://localhost:14318")
    built.add_argument("--postgres", help="async Postgres DSN (forged)")
    built.add_argument("--clickhouse", help="reader DSN (forged)")
    built.add_argument("--org", type=uuid.UUID)
    built.add_argument("--project", type=uuid.UUID)
    built.add_argument("--service", action="append", default=[], help="a real service (forged)")
    built.add_argument("--repeats", type=int, default=3)
    built.add_argument("--spacing-minutes", type=int, default=30)
    built.add_argument("--fault-log", type=Path, help="the fault bed's run log (deploys)")
    built.add_argument("--api", default="http://localhost:18000", help="the API (deploys)")
    built.add_argument("--days", type=int, default=7)
    built.add_argument("--ledger", type=Path, help="posted deliveries, so a stop resumes (deploys)")
    built.add_argument("--wait", type=int, default=420, help="seconds to wait for a finding")
    return built


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    needs: dict[str, tuple[str, ...]] = {
        "score": ("log",),
        "tune": ("log", "constant", "values"),
        "models": (),
        "forged": ("postgres", "clickhouse", "org", "project", "service"),
        "deploys": ("fault_log", "project", "service"),
    }
    missing = [name for name in needs.get(arguments.run, ()) if not getattr(arguments, name)]
    if missing:
        parser().error(f"--run {arguments.run} needs --{', --'.join(missing)}")
    if arguments.format_name and not (arguments.base_url and arguments.model):
        parser().error("--format needs --base-url and --model")
    if arguments.run == "score":
        return run_score(arguments)
    if arguments.run == "tune":
        return run_tune(arguments)
    if arguments.run == "payloads":
        return run_payloads()
    if arguments.run == "models":
        return run_models(arguments)
    if arguments.run == "deploys":
        return run_deploys(arguments)
    return run_forged(arguments)


if __name__ == "__main__":
    sys.exit(main())
