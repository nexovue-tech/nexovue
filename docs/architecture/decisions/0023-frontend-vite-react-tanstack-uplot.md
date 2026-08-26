# 23. Frontend = Vite + React + TanStack Router/Query, uPlot for time series

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

The application is entirely behind authentication, so server-side rendering has no SEO or
first-paint-for-anonymous-visitors case to make. Its charts are dense telemetry, not dashboard
cards.

## Decision

Vite + React with TanStack Router and TanStack Query. **No Next.js** — behind auth, React Server
Components buy complexity and nothing else. uPlot for time series, because dense telemetry charts
need 100k-point rendering that card-oriented chart libraries cannot do. Auth is FastAPI sessions
with argon2, self-hosted — no Keycloak, no Ory.

## Consequences

A plain SPA build with no server runtime to operate, and charts that stay interactive at telemetry
volumes. uPlot is low-level: axes, tooltips and legends are ours to build. Self-hosted auth means
password reset, session handling and lockout are also ours. Router choice re-examined and confirmed
in [0028](0028-tanstack-router-for-the-frontend.md).
