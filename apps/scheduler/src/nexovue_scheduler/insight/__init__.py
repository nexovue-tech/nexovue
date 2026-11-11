"""F3: the AI role's tasks, run only by `scheduler-insight` (`registry.TASK_SETS["insight"]`).

Its own account, network and task modules (plan Step 3, the fence). `check` is the
connection test (Step 4) and `word` the wording job (Step 5), both over `transport`, the one
way a provider is called; `sweep` ends what a killed run or a stopped container left.
Detection and the evidence job must never import this package (`import_walk.F3_AI_MODULES`).
"""
