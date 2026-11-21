"""Rung 2b's rules, G1 to G9 (F5 plan T2b.3; spec FR36, AC69).

Each rule has a fixture it refuses and a near-miss it allows, AC69's named ones among them.
The near-miss shows the rule is not wider than written; the refusal that it is not
narrower. A suggestion is checked whole: the first broken rule decides.
"""

from __future__ import annotations

import json
from typing import Any, Final

import pytest
from nexovue_platform.fixes.general_lists import unlisted_imports
from nexovue_platform.fixes.general_rules import (
    CODE_MAX_LINES,
    GeneralAbstained,
    GeneralRefused,
    GeneralSuggestion,
    aliases_in,
    check_general,
    check_general_text,
)
from nexovue_platform.fixes.records import GENERAL_LANGUAGES, RETRY_SHAPE, RETRY_TOO_LONG

MODEL_INPUT: Final[dict[str, Any]] = {
    "finding": {"subject": "service_1", "signal": "failing", "severity": "warning"},
    "facts": [
        {"id": "e1", "kind": "error_group", "subject": "service_1", "error": "error_1"},
        {"id": "e2", "kind": "deploy", "subject": "service_1", "version": "version_1"},
    ],
    "likely_cause": {"evidence_id": "e2", "strength": "likely"},
    "schema": 1,
}
GOOD_CODE: Final = (
    "import json\n"
    "import time\n"
    "\n"
    "def call_with_retry(call, attempts=3):\n"
    "    for attempt in range(attempts):\n"
    "        try:\n"
    "            return call()\n"
    "        except TimeoutError:\n"
    "            time.sleep(2 ** attempt)\n"
    "    raise TimeoutError('gave up')\n"
)


def answer(**changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "outcome": "suggestion",
        "abstain_reason": None,
        "summary": "Calls from service_1 time out after version_1; retry with a backoff.",
        "steps": ["Wrap the outbound call in a retry.", "Cap the attempts at three."],
        "example": {"language": "python", "code": GOOD_CODE},
        "evidence_ids": ["e1", "e2"],
    }
    document.update(changes)
    return document


def with_code(code: str, language: str = "python") -> dict[str, Any]:
    return answer(example={"language": language, "code": code})


def with_step(step: str) -> dict[str, Any]:
    return answer(steps=["Wrap the outbound call in a retry.", step])


def verdict(document: object, language: str = "python") -> object:
    return check_general(document, language=language, model_input=MODEL_INPUT)  # type: ignore[arg-type]


def rule_of(document: object, language: str = "python") -> str | None:
    found = verdict(document, language)
    return found.rule if isinstance(found, GeneralRefused) else None


def test_a_good_suggestion_passes_and_stores_only_its_checked_values() -> None:
    found = verdict(answer())

    assert isinstance(found, GeneralSuggestion)
    assert found.stored() == answer()


def test_a_stored_suggestion_passes_the_same_rules_again_on_read() -> None:
    found = verdict(answer())
    assert isinstance(found, GeneralSuggestion)

    assert verdict(json.loads(json.dumps(found.stored()))) == found


@pytest.mark.parametrize("reason", ["evidence_too_thin", "needs_a_decision", "not_a_code_problem"])
def test_an_abstention_from_the_list_is_a_good_outcome(reason: str) -> None:
    document = answer(
        outcome="abstain", abstain_reason=reason, summary=None, steps=[], example=None
    )

    assert verdict(document) == GeneralAbstained(reason=reason)


@pytest.mark.parametrize(
    ("label", "document"),
    [
        ("an unknown key", {**answer(), "confidence": 0.9}),
        ("a missing key", {k: v for k, v in answer().items() if k != "evidence_ids"}),
        ("a third outcome", answer(outcome="partial")),
        ("no step", answer(steps=[])),
        ("six steps", answer(steps=["Step."] * 6)),
        ("an evidence id not in the input", answer(evidence_ids=["e1", "e9"])),
        ("no evidence id", answer(evidence_ids=[])),
        ("another language", with_code(GOOD_CODE, language="go")),
        ("an abstention off the list", answer(outcome="abstain", abstain_reason="unsure")),
        (
            "an abstention carrying a suggestion",
            answer(outcome="abstain", abstain_reason="evidence_too_thin"),
        ),
        (
            "an example with an extra key",
            answer(example={"language": "python", "code": "x", "run": True}),
        ),
        ("a list, not an object", [answer()]),
        ("a step that is not text", answer(steps=["Retry.", 3])),
    ],
)
def test_g1_refuses_the_wrong_shape(label: str, document: object) -> None:
    assert rule_of(document) == "G1", label


def test_g1_and_g2_carry_their_retry_codes_and_end_as_invalid_output() -> None:
    shape, caps = GeneralRefused("G1"), GeneralRefused("G2")

    assert (shape.retry_code, caps.retry_code) == (RETRY_SHAPE, RETRY_TOO_LONG)
    assert shape.reason == caps.reason == "invalid_output"
    assert GeneralRefused("G5").retry_code is None
    assert GeneralRefused("G5").reason == "general_withheld_G5"


def test_g1_refuses_text_that_is_not_json() -> None:
    found = check_general_text("Here is a fix: ...", language="python", model_input=MODEL_INPUT)

    assert found == GeneralRefused("G1")


@pytest.mark.parametrize(
    ("label", "document"),
    [
        ("a 301-character summary", answer(summary="x" * 301)),
        ("a 201-character step", with_step("y" * 201)),
        ("a 41-line example", with_code("x = 1\n" * CODE_MAX_LINES + "y = 2")),
        ("a 121-character line", with_code("x = '" + "a" * 120 + "'")),
        ("an example over 2 KiB", with_code(("x = 1  # " + "a" * 100 + "\n") * 20)),
    ],
)
def test_g2_refuses_anything_over_a_cap(label: str, document: dict[str, Any]) -> None:
    assert rule_of(document) == "G2", label


def test_g2_allows_exactly_the_caps() -> None:
    document = answer(
        summary="a" * 300,
        steps=["b" * 200],
        example={"language": "python", "code": "x = 1\n" * 39 + "y = 2"},
    )

    assert isinstance(verdict(document), GeneralSuggestion)


def test_g2_refuses_a_whole_answer_over_8_kib_before_reading_it() -> None:
    # Every field within its cap: only the raw text's size breaks the rule, before parsing.
    text = json.dumps(answer()) + " " * 9000

    assert check_general_text(text, language="python", model_input=MODEL_INPUT) == GeneralRefused(
        "G2"
    )


@pytest.mark.parametrize(
    ("label", "document"),
    [
        ("NUL in the summary", answer(summary="Retry\u0000 the call.")),
        ("a line feed in prose", with_step("Retry\nthe call.")),
        ("a lone surrogate", with_step("Retry \ud800 the call.")),
        ("a bidirectional override", with_step("Retry \u202e the call.")),
        ("a zero-width space in code", with_code("x\u200b = 1")),
        ("a tag character", with_step("Retry \U000e0041 the call.")),
        ("DEL", with_step("Retry \u007f the call.")),
        ("a C1 control", with_step("Retry \u0085 the call.")),
        ("a Cyrillic look-alike", with_step("Wrap the \u0441all in a retry.")),
        ("a carriage return in code", with_code("x = 1\r\ny = 2")),
    ],
)
def test_g3_refuses_hidden_and_look_alike_characters(label: str, document: dict[str, Any]) -> None:
    assert rule_of(document) == "G3", label


def test_g3_allows_tabs_and_line_feeds_in_code_and_other_scripts_alone() -> None:
    document = answer(
        steps=[
            "Wrap the call in a retry.",
            "\u0420\u0435\u0442\u0440\u0430\u0439 \u0442\u0440\u0438 \u0440\u0430\u0437\u0430.",
        ],
        example={"language": "python", "code": "def f():\n\treturn 1\n"},
    )

    assert isinstance(verdict(document), GeneralSuggestion)


@pytest.mark.parametrize(
    ("label", "document"),
    [
        ("a URL in a step", with_step("Read https://docs.example/retry first.")),
        ("an IP address", with_code("HOST = '10.0.0.1'")),
        ("an email address", with_step("Ask ops@example.org for access.")),
        ("a Markdown link", with_step("See [the guide](guide).")),
        ("a host name in code", with_code("connect('collector.evil.com')")),
        ("a host name in prose", answer(summary="Calls to api.example time out.")),
    ],
)
def test_g4_refuses_any_address_with_no_allowance(label: str, document: dict[str, Any]) -> None:
    assert rule_of(document) == "G4", label


def test_g4_allows_a_network_call_with_no_address() -> None:
    code = (
        "import urllib.request\n\n"
        "def read(url):\n"
        "    return urllib.request.urlopen(url, timeout=5)\n"
    )

    assert isinstance(verdict(with_code(code)), GeneralSuggestion)


def test_g4_allows_requests_get_with_no_address_as_far_as_g4_goes() -> None:
    """AC69's `requests.get(url, timeout=5)`: no address, so G4 passes; its import is G6's."""
    found = verdict(with_code("def read(url):\n    return requests.get(url, timeout=5)\n"))

    assert isinstance(found, GeneralSuggestion)


@pytest.mark.parametrize(
    ("label", "document"),
    [
        ("subprocess.run", with_code("import subprocess\nsubprocess.run(['ls'])")),
        ("eval(", with_code("value = eval(text)")),
        ("curl in prose", with_step("Check it with curl first.")),
        ("a pipe into sh", with_step("Run the script | sh to apply.")),
        ("a prompt line", with_code("$ python retry.py")),
        ("sudo", with_step("Restart it with sudo.")),
        ("rm -", with_step("Clear the cache with rm -r.")),
        ("os.system", with_code("import os\nos.system('restart')")),
        ("Runtime.getRuntime", with_code("Runtime.getRuntime().exec(cmd);", language="java")),
        ("exec.Command", with_code('cmd := exec.Command("ls")', language="go")),
        ("a Ruby backquote", with_code("out = `ls`", language="ruby")),
        ("Process.Start", with_code("Process.Start(name);", language="csharp")),
        ("Command::new", with_code('let c = Command::new("ls");', language="rust")),
        ("new Function", with_code("const f = new Function(body);", language="javascript")),
    ],
)
def test_g5_refuses_shell_commands_and_process_starts(label: str, document: dict[str, Any]) -> None:
    assert rule_of(document, document["example"]["language"]) == "G5", label


@pytest.mark.parametrize(
    ("language", "code"),
    [
        ("python", "def run(cursor, query):\n    return cursor.execute(query)\n"),
        ("javascript", "const message = `retry ${attempt}`;"),
        ("python", "def evaluate(score):\n    return score > 3\n"),
    ],
)
def test_g5_allows_near_misses(language: str, code: str) -> None:
    assert isinstance(verdict(with_code(code, language), language), GeneralSuggestion)


@pytest.mark.parametrize(
    ("label", "document"),
    [
        ("import requests", with_code("import requests\n\nrequests.get(url, timeout=5)\n")),
        ("pip install in a step", with_step("pip install tenacity, then wrap the call.")),
        ("install as a word", with_step("Install a retry library.")),
        ("download as a word", with_step("Download the newer client.")),
        ("npm", with_step("Add it with npm.")),
        ("go get", with_step("Fetch it with go get.")),
        ("a dependency file", with_step("Pin the client in requirements.txt.")),
        ("package.json", with_step("Add the client to package.json.")),
        ("from-import of a library", with_code("from tenacity import retry\n")),
        ("a relative import", with_code("from .db import session\n")),
        ("a JavaScript library", with_code("import axios from 'axios';", language="javascript")),
        ("a Go module", with_code('import "github_com/x/y"', language="go")),
        ("a Java library", with_code("import org.apache.http.Client;", language="java")),
        ("a PHP include", with_code("require 'vendor/autoload';", language="php")),
        ("a Rust crate", with_code("use tokio::time;", language="rust")),
        ("a C# package", with_code("using Polly;", language="csharp")),
    ],
)
def test_g6_refuses_setup_steps_and_imports_off_the_list(
    label: str, document: dict[str, Any]
) -> None:
    assert rule_of(document, document["example"]["language"]) == "G6", label


@pytest.mark.parametrize(
    ("language", "code"),
    [
        ("python", "import json\nfrom datetime import timedelta\n"),
        ("javascript", "import { setTimeout } from 'node:timers/promises';"),
        ("typescript", "const fs = require('fs');"),
        ("java", "import java.time.Duration;"),
        ("kotlin", "import kotlin.time.Duration"),
        ("go", 'import (\n\t"context"\n\t"time"\n)'),
        ("ruby", "require 'json'"),
        ("php", "use DateTimeImmutable;"),
        ("csharp", "using System.Threading.Tasks;"),
        ("rust", "use std::time::Duration;"),
    ],
)
def test_g6_allows_each_languages_standard_library(language: str, code: str) -> None:
    assert unlisted_imports(code, language) == []  # type: ignore[arg-type]
    assert isinstance(verdict(with_code(code, language), language), GeneralSuggestion)


def test_every_language_has_an_import_list() -> None:
    for language in GENERAL_LANGUAGES:
        assert unlisted_imports("", language) == []


@pytest.mark.parametrize(
    ("label", "document"),
    [
        ("a token prefix", with_code("TOKEN = 'ghp_" + "a1B2c3D4e5" * 4 + "'")),
        ("a private key header", with_step("Paste -----BEGIN RSA PRIVATE KEY----- here.")),
        ("a long opaque string", with_code("BLOB = 'Qz7Wn2xLk9Pv4Rt8Ys1Mb6Hc3Jd5Fg0Ne2Ua7Ki9Ow4'")),
    ],
)
def test_g7_refuses_secret_shapes_and_long_encoded_strings(
    label: str, document: dict[str, Any]
) -> None:
    assert rule_of(document) == "G7", label


def test_g7_allows_a_long_identifier() -> None:
    code = "max_attempts_before_giving_up_on_the_downstream_call = 3\n"

    assert isinstance(verdict(with_code(code)), GeneralSuggestion)


@pytest.mark.parametrize(
    ("label", "document"),
    [
        ("your code", with_step("Change your code to retry.")),
        ("in your", with_step("Add a retry in your client.")),
        ("a file and line", with_step("The bug is at app/db.py:42.")),
        ("a source file name in prose", with_step("Open db.py and add a retry.")),
        ("a path in code", with_code("# see app/db.py\nx = 1\n")),
        ("tested", answer(summary="This change is tested and retries the call.")),
        ("This has been tested and is safe", with_step("This has been tested and is safe.")),
        ("a fix claim", with_step("This fixes the timeouts.")),
    ],
)
def test_g8_refuses_claims_of_having_read_the_code(label: str, document: dict[str, Any]) -> None:
    assert rule_of(document) == "G8", label


def test_g8_allows_a_method_call_named_like_an_extension_in_code() -> None:
    code = "def start(engine):\n    return engine.go()\n"

    assert isinstance(verdict(with_code(code)), GeneralSuggestion)


@pytest.mark.parametrize(
    ("label", "document"),
    [
        ("service_9 absent from the aliases", with_step("Retry calls from service_9.")),
        ("an alias in code", with_code("SERVICE = 'service_1'\n")),
    ],
)
def test_g9_refuses_aliases_it_cannot_match(label: str, document: dict[str, Any]) -> None:
    assert rule_of(document) == "G9", label


def test_g9_allows_this_versions_aliases_in_prose() -> None:
    assert aliases_in(MODEL_INPUT) == {"service_1", "error_1", "version_1"}
    assert isinstance(verdict(with_step("Retry what error_1 shows.")), GeneralSuggestion)


def test_the_first_broken_rule_decides() -> None:
    document = answer(summary="Run curl on https://x.example", steps=["Change your code."])

    assert rule_of(document) == "G4"
