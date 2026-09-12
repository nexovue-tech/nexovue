# Status: explore-skill

## Lifecycle status

Implemented

## Current task

None.

## Completed

- `.claude/skills/explore/SKILL.md` written: ground before inventing, no code or specification while
  exploring, an explicit split between founder decisions and agent recommendations, and four
  convergence outcomes each with an existing home.
- It also covers the interrupted case — `feature-work` or `change-work` hitting a decision nobody has
  made — by recording the outcome in the workspace already open rather than starting a second one.
- `docs/engineering/workflow.md` gains an exploration section before the lifecycle, so the step
  exists for Codex and human contributors and not only in a Claude Code skill.
- `CLAUDE.md` skills table updated.

## Next action

Use it. The immediate subject is the insight engine: F1 Findings is fully specified — seven user
stories, ten acceptance criteria — but its Dependencies line names an insight engine, anomaly
detection, and a live-update path, none of which has a specification, a catalog entry, or code.

`/feature-work` on F1 would stall at the design gate for that reason. The insight engine is the part
nobody has worked out, and is the product's actual differentiator.

## Blocked

Nothing.

## Validation

`just check` on 2026-09-12, exit code 0, output read: 2277 Python tests, 120 browser-SDK across 12
files, 613 web across 47 files, plus `buf lint` and `buf breaking`. No code was touched.

All 6 markdown links in the added and changed files resolve, checked by script.

**Not verified, and not verifiable by this change:** whether the skill fires when it should. Its
description has to distinguish "an idea without acceptance criteria" from "a request that states what
it wants", and whether that distinction works in practice is an observation to make over the next few
sessions. If `feature-work` starts firing on vague requests, or `explore` on clear ones, the
descriptions need narrowing rather than the skills changing.

## Last updated

2026-09-12
