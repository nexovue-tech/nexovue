"""The chat bed's command: `just chat-bed <command>` (F4 plan T4.1 to T4.4).

    run      one option's trials: chat, injection, or both, saved one per line
    render   the one report, from saved trials, written outside the repository
    answers  rewrite `answers.json` from the seed

`run --fake NAME` uses a fake provider (harness only). Without `--fake` it is a **real
option, the founder's run after Phase 2 (foundation item 31)**: `--format`, `--base-url` and
`--model` name it, the key is read from `AI_EVAL_KEY` and never printed or stored, and a
model on the founder's own machine is reached through `--private` (F3's
`AI_PRIVATE_ENDPOINTS` form). Nothing here has been run against a real model.

Saved trials are appended as each ends, so a run of days can stop and start again: a trial
already saved for that option is skipped. Records and the report must both lie outside the
repository (founder decision 5).

Run against the bed's own Postgres and ClickHouse, migrated by `setup`, with no scheduler
or worker running (`stores.py`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Final

from injection import run as injection_run
from injection.payloads import PAYLOADS, PAYLOADS_BY_ID, PLACES, TRIALS, Payload, Place
from nexovue_platform.ai.chat import ChatProvider
from nexovue_platform.checks.sealing import ConfiguredKeys, generate_key

from chat import answers, harness, stores
from chat.answers import Question
from chat.harness import TrialRecord
from chat.providers import FAKES as CHAT_FAKES
from chat.providers import ForConnection
from chat.providers import fake as chat_fake
from chat.report import REPOSITORY, InsideRepositoryError, render, write_report

KEY_VARIABLE: Final = "AI_EVAL_KEY"
RUNS: Final = ("chat", "injection")
# Which injection fake stands in for a chat fake of the same purpose.
_INJECTION_FAKE: Final = {
    "fake-answer-key": "fake-ignoring",
    "fake-abstaining": "fake-ignoring",
    "fake-hostile": "fake-hostile",
    "fake-obeying": "fake-obeying",
    "fake-ignoring": "fake-ignoring",
}


def outside_repository(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == REPOSITORY or REPOSITORY in resolved.parents:
        raise InsideRepositoryError(f"{resolved} is inside the repository; keep it outside.")
    return resolved


def load_records(paths: Sequence[Path]) -> list[TrialRecord]:
    found: list[TrialRecord] = []
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                found.append(TrialRecord.loaded(json.loads(line)))
    return found


def save(path: Path, records: Sequence[TrialRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        for record in records:
            out.write(json.dumps(record.saved(), default=str) + "\n")


def done(records: Sequence[TrialRecord], option: str) -> set[tuple[str, str, int]]:
    return {
        (record.run, record.case, record.trial)
        for record in records
        if record.option == option and record.turn == 1
    }


def real_connection(arguments: argparse.Namespace) -> stores.Connection:
    return stores.Connection(
        wire_format=arguments.format_name,
        base_url=arguments.base_url,
        model=arguments.model,
        call_seconds=arguments.call_seconds,
    )


def real_provider(arguments: argparse.Namespace) -> ForConnection:
    """The product's own transport and the connection's chat format, built from the
    connection the loop loads, its key from the environment and never stored. Not run here:
    a real call is the founder's."""
    from nexovue_platform.ai.chat_formats import chat_format

    from nexovue_scheduler.insight.assistant import TransportProvider
    from nexovue_scheduler.probes.addresses import PrivateEndpoints

    endpoints = PrivateEndpoints.parse(arguments.private)
    key = os.environ.get(KEY_VARIABLE) or None
    return ForConnection(
        lambda connection: TransportProvider(
            chat_format(connection.wire_format), connection, key, endpoints
        )
    )


def _chat_ids(arguments: argparse.Namespace) -> list[str]:
    known = harness.questions_by_id(answers.questions)
    chosen = arguments.questions.split(",") if arguments.questions else list(known)
    unknown = [each for each in chosen if each not in known]
    if unknown:
        raise SystemExit(f"unknown questions: {', '.join(unknown)}")
    return chosen


def _cells(arguments: argparse.Namespace) -> list[tuple[Payload, Place]]:
    payloads = (
        [PAYLOADS_BY_ID[each] for each in arguments.payloads.split(",")]
        if arguments.payloads
        else list(PAYLOADS)
    )
    places = list(arguments.places.split(",")) if arguments.places else list(PLACES)
    bad = [each for each in places if each not in PLACES]
    if bad:
        raise SystemExit(f"unknown places: {', '.join(bad)}")
    return [(payload, place) for payload in payloads for place in places]


async def run(arguments: argparse.Namespace) -> int:
    records_path = outside_repository(arguments.records)
    existing = done(load_records([records_path]), arguments.option)
    current = stores.Stores.from_environment()
    if not current.reachable():
        raise SystemExit("the bed's Postgres or ClickHouse is not reachable (see stores.py)")
    keys = ConfiguredKeys(generate_key(), "")
    if arguments.fake:
        connection = stores.Connection()
        real = None
    else:
        connection = real_connection(arguments)
        real = real_provider(arguments)
    runs = arguments.runs.split(",")
    saved = 0
    with stores.serve(current, keys) as api_url:
        bed = await harness.open_bed(current, api_url, keys, connection)
        try:
            if "chat" in runs:
                by_id = harness.questions_by_id(answers.questions)
                for qid in _chat_ids(arguments):
                    for trial in range(1, arguments.trials + 1):
                        if ("chat", qid, trial) in existing:
                            continue

                        def factory(
                            question: Question, anchor: datetime
                        ) -> ChatProvider | ForConnection:
                            if real is not None:
                                return real
                            return chat_fake(arguments.fake, question, anchor)

                        record = await harness.chat_trial(
                            bed, by_id[qid], factory, option=arguments.option, trial=trial
                        )
                        save(records_path, [record])
                        saved += 1
            if "injection" in runs:
                injection_fake = _INJECTION_FAKE.get(arguments.fake or "", "")
                for payload, place in _cells(arguments):
                    case = f"{payload.id}@{place}"
                    for trial in range(1, arguments.trials + 1):
                        if ("injection", case, trial) in existing:
                            continue

                        def inject(
                            question: Question, planted: Payload, anchor: datetime
                        ) -> ChatProvider | ForConnection:
                            if real is not None:
                                return real
                            return injection_run.fake(injection_fake, question, planted, anchor)

                        made = await injection_run.injection_trial(
                            bed, payload, place, inject, option=arguments.option, trial=trial
                        )
                        save(records_path, made)
                        saved += len(made)
        finally:
            await bed.close()
    print(f"saved {saved} trials to {records_path}")
    return 0


def render_command(arguments: argparse.Namespace) -> int:
    records = load_records([outside_repository(path) for path in arguments.records])
    known = {each.id: each for each in answers.questions(answers.CANONICAL_ANCHOR)}
    text = render(
        records,
        known,
        machines=dict(item.split("=", 1) for item in arguments.machine),
        models=dict(item.split("=", 1) for item in arguments.model_name),
        contexts=dict(item.split("=", 1) for item in arguments.context),
    )
    print(f"wrote {write_report(arguments.out, text)}")
    return 0


def parser() -> argparse.ArgumentParser:
    made = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    commands = made.add_subparsers(dest="command", required=True)
    run_command = commands.add_parser("run", help="one option's trials")
    run_command.add_argument(
        "--option", required=True, help="the option's name, as the report says"
    )
    run_command.add_argument("--fake", choices=sorted({*CHAT_FAKES, *_INJECTION_FAKE}))
    run_command.add_argument("--format", dest="format_name", default="openai_chat")
    run_command.add_argument("--base-url", default="")
    run_command.add_argument("--model", default="")
    run_command.add_argument("--private", default="", help="AI_PRIVATE_ENDPOINTS for a local model")
    run_command.add_argument("--call-seconds", type=int, default=stores.DEFAULT_CALL_SECONDS)
    run_command.add_argument("--runs", default=",".join(RUNS))
    run_command.add_argument("--trials", type=int, default=TRIALS)
    run_command.add_argument("--questions", default="", help="question ids, comma separated")
    run_command.add_argument("--payloads", default="", help="payload ids, comma separated")
    run_command.add_argument("--places", default="", help="places, comma separated")
    run_command.add_argument("--records", type=Path, required=True)
    shown = commands.add_parser("render", help="the report, from saved trials")
    shown.add_argument("--records", type=Path, nargs="+", required=True)
    shown.add_argument("--out", type=Path, required=True)
    shown.add_argument("--machine", action="append", default=[], help="OPTION=machine")
    shown.add_argument("--model-name", action="append", default=[], help="OPTION=model")
    shown.add_argument("--context", action="append", default=[], help="OPTION=context length")
    commands.add_parser("answers", help="rewrite answers.json from the seed")
    return made


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.command == "run":
        if not arguments.fake and not (arguments.base_url and arguments.model):
            raise SystemExit("a real option needs --base-url and --model (or use --fake)")
        return asyncio.run(run(arguments))
    if arguments.command == "render":
        return render_command(arguments)
    answers.ANSWER_FILE.write_text(answers.answer_file(), encoding="utf-8")
    print(f"wrote {answers.ANSWER_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
