"""F3: the provider contract, pure (foundation FD3; design *Provider interface*; ADR-047).

Three plain-HTTP wire formats, each two pure functions: `build` turns an `AIRequest` into
the exact bytes to send, and `parse` turns a status and body into an `AIResponse`. **No
HTTP here and no provider SDK** (both are on the no-AI deny-list, and an SDK would bypass
the connect-time address guard): the transport is `nexovue_scheduler.insight.transport`,
which sends through `probes.http_check.perform`. The API imports these pure parts only, to
show the preview and rebuild what a run sent (AC36).

Detection and the evidence job must never import this package (`import_walk.F3_AI_MODULES`).
"""
