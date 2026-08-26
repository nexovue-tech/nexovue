# 31. Service-map rendering = React Flow (xyflow)

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

The service map ([0017](0017-nine-architecture-additions-from-feature-audit.md), F9) needs a graph
renderer, which none of the confirmed frontend libraries provide. Cytoscape.js was the alternative.

## Decision

React Flow (xyflow), MIT. Nodes are React components, so each service box carries its name, health
colour, request and error rate, and a sparkline. Pan, zoom and drag come free; layout via dagre or
elkjs.

Cytoscape.js is the stronger engine for thousands of nodes with WebGL rendering, but our target
customers — 2–15 person startups — have 5–50 services, and Cytoscape's non-React rendering world
makes rich node content harder.

## Consequences

Service nodes are ordinary components, so they can show as much as any other part of the UI. The
choice is explicitly bounded by an assumption about customer size — revisit only if graph size ever
contradicts it.
