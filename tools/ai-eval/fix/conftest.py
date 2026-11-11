"""The fixture repository's own tests show its seeded bugs, so they fail on purpose.
`test_fixtures.py` runs them one by one; pytest must never collect them as the gate's."""

collect_ignore = ["repository"]
