# Change: SDD adoption

## Classification

Engineering-system and documentation change.

## Current behavior and problem

Nexovue had strong source-derived documentation and a Claude-specific instruction file, but no agent-agnostic SDD lifecycle, persistent feature/change status structure, or clear authoritative locations for product, architecture, standards, decisions, and operations knowledge.

## Intended behavior

The repository itself enables a new human or AI agent to locate source-of-truth documents, determine current work, use a proportional SDD lifecycle, and preserve current-state versus future-state distinctions without changing application behavior.

## Scope

Documentation structure, agent instructions, templates, current architecture baseline, initial ADRs, and production-readiness baseline. No application or infrastructure implementation changes.
