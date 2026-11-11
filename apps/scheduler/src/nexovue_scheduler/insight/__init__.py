"""F3: the AI role's tasks, run only by `scheduler-insight` (`registry.TASK_SETS["insight"]`).

The role runs idle in plan Step 3, the fence: its own account, network and task modules,
with no provider code. The connection test joins in Step 4 and the wording job in Step 5.
Detection and the evidence job must never import this package (`import_walk.F3_AI_MODULES`).
"""
