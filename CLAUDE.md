# Claude Code compatibility entry point

`AGENTS.md` is the repository's agent-agnostic operating manual and is authoritative for Codex, Claude Code, other AI agents, and human contributors.

Before working, read [AGENTS.md](AGENTS.md), then follow its source-of-truth and lifecycle instructions. Repository-specific commands and technical invariants are retained there and in `docs/engineering/` and `docs/architecture/`.

## Skills

`.claude/skills/` holds the procedures that run the workflow in
[`docs/engineering/workflow.md`](docs/engineering/workflow.md). They are Claude Code only; the
workflow document is the agent-agnostic source both they and other agents read.

| Skill | Use for |
|---|---|
| `/feature-work` | A new or changed customer-facing capability, through spec → design → plan → implement. |
| `/change-work` | A bug fix, refactor, migration, or infrastructure change, through a change record. |
| `/review-diff` | The implementation self-review before any handoff. |
| `/readiness-check` | Whether a change or the repository is ready for a production rollout. |

A skill owns *what to do and in what order*. `docs/engineering/` and `docs/quality/` own *what is
correct*. A skill that starts restating a standard is a defect — delete the restatement and link
instead.
