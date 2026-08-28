"""Fail when a test suite reports success by not having run.

`apps/api/tests/test_tenant_isolation_live.py` skips itself at module level when
the dev stack is unreachable, and pytest exits 0 when it does. That makes
"nobody ran the tenant isolation suite" and "tenant isolation holds"
indistinguishable — for P1-25, a Phase 1 invariant. This reads the JUnit report
and turns the difference back into an exit code.

Usage: assert_suite_ran.py <junit-xml>
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final
from xml.etree import ElementTree

# The module-level guard in the live suite. A skip carrying this text means the
# stack was not there, which in CI is a broken job, never a passing one.
STACK_UNREACHABLE_MARKER: Final = "the dev stack is not running"

# Skips this gate tolerates, each an accepted gap rather than an accident.
# Both fire when the seeded organization has too little traffic for the test to
# have anything to attack. Removing them needs a change in apps/api — either the
# fixture guarantees the data or the test fails instead of skipping — and this
# list should empty out when that lands.
TOLERATED_SKIP_REASONS: Final = (
    "the seeded organization has no second page to forge a cursor from",
    "the seeded organization has no traces in the default window",
)


def _write(line: str) -> None:
    sys.stdout.write(f"{line}\n")


def _summarize(line: str) -> None:
    """Echo to the job summary as well, so the counts survive log expiry."""
    _write(line)
    summary_path = sys.argv[2] if len(sys.argv) > 2 else ""
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write(f"{line}\n")


def _skip_reason(case: ElementTree.Element) -> str | None:
    skipped = case.find("skipped")
    if skipped is None:
        return None
    return skipped.get("message") or (skipped.text or "").strip()


def _is_tolerated(reason: str) -> bool:
    return any(tolerated in reason for tolerated in TOLERATED_SKIP_REASONS)


def _case_name(case: ElementTree.Element) -> str:
    return case.get("name") or case.get("classname") or "<unnamed>"


def main() -> int:
    if len(sys.argv) < 2:
        _write("usage: assert_suite_ran.py <junit-xml> [github-step-summary]")
        return 2

    report = Path(sys.argv[1])
    if not report.is_file():
        _write(f"no JUnit report at {report}: the suite produced no evidence it ran")
        return 1

    cases = list(ElementTree.parse(report).getroot().iter("testcase"))
    skips = {_case_name(case): reason for case in cases if (reason := _skip_reason(case))}
    executed = len(cases) - len(skips)

    _summarize(f"Tenant isolation (P1-25): {executed} executed, {len(skips)} skipped.")

    verdicts: list[str] = []
    for name, reason in skips.items():
        if STACK_UNREACHABLE_MARKER in reason:
            verdicts.append(f"the suite skipped itself — the stack was not reachable: {reason}")
        elif not _is_tolerated(reason):
            verdicts.append(f"{name} skipped for an unrecognized reason: {reason}")
        else:
            _summarize(f"  tolerated skip — {name}: {reason}")

    if executed == 0:
        verdicts.append("not one test executed, so a green result proves nothing")

    for verdict in verdicts:
        _summarize(f"FAILED: {verdict}")
    return 1 if verdicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
