"""F5, the AI solution: what a person may act on from a finding (spec, *Goal*).

Pure code shared by the API and, from Step 2, the scheduler's rooms. **No AI code lives
here** (design, *Where each step runs*): rungs 1 and 2 must work with AI off, stopped or
failing (BR12), so nothing under this package may import `nexovue_platform.ai`
(`test_fix_facts_hold_no_ai.py`).
"""
