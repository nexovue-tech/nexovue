# 14. LLM strategy = provider-flexible behind our own interface

- **Status:** Accepted
- **Date:** 2026-08

## Context

Model quality, price and availability all move faster than our release cycle, and different tasks
want different models — a routine summary and a root-cause analysis are not the same problem.

## Decision

An internal abstraction, with providers behind it: OpenAI (hosted), Ollama (self-hosted/local
models), and fine-tuned models later — finding summarization trained on resolved findings is the
first candidate. Routing is per task: cheap or local for routine summaries, stronger models for the
RAG chatbot and complex root-cause work.

## Consequences

Providers are swappable without touching callers, and cost is controllable per task. The
abstraction has to be maintained against provider-specific features that do not generalize. See
also [0047](0047-ollama-not-viable-on-dev-machine.md) — the local half of this does not run on the
current dev machine.
