# Reading your data: the API and the MCP server

Everything the console shows you, your own scripts can read. A **read API token**
is the credential for that: it belongs to your organization rather than to a
person, it reads and never writes, and it is the same credential an AI assistant
uses through the MCP server at the end of this page.

> The examples below use `https://api.nexovue.com`. Use the address your own
> console is served from; in local development the API is
> `http://localhost:18000`.

## Create a token

Console → **Settings → API tokens → Create**. You choose three things:

| | |
|---|---|
| **Name** | What uses it: *Nightly report*, *CI*. It is what you revoke by, so five tokens called "token" is a bad afternoon. |
| **Reads** | One project, or every project. A token scoped to one project asking about another gets exactly the same answer as a project that does not exist. |
| **Expires in** | 30 days, 90 days, or a year. Every token ends; you choose when. |

The secret is shown **once**, at creation. We store a SHA-256 hash of it and
never the token, so nobody — including us — can read it back. If you lose it,
revoke that token and create another.

Tokens are owner-and-admin work to create and revoke; anyone signed in can see
the list, because the list holds no secrets.

**A token is not an ingest key.** Ingest keys (`nvk_live_…`) write telemetry and
read nothing; API tokens (`nvt_live_…`) read and write nothing. Each is refused
by the other's door, and says so.

## Authenticate

```bash
curl -H "Authorization: Bearer $NEXOVUE_TOKEN" \
  "https://api.nexovue.com/api/v1/projects"
```

Every endpoint below takes that header and nothing else. A revoked token is
refused on its very next request; an expired one is refused with the date it
expired on.

## The read surface

This is the whole of it. These endpoints are versioned under `/api/v1` and are
what a token may reach; anything else answers **403 — API tokens are read-only
and reach only the documented read API.**

| Endpoint | What it answers |
|---|---|
| `GET /projects` | The projects this token may read, with the ids the rest take |
| `GET /services` | Every service in a window, worst first |
| `GET /services/{name}` | One service's health in a window |
| `GET /traces` | Traces matching filters, newest first (paged) |
| `GET /traces/{trace_id}` | One trace and its spans |
| `GET /logs/search` | Log records matching a phrase (paged) |
| `GET /metrics` | The metric catalogue |
| `GET /metrics/{name}/histogram` | One metric's histogram over a window |
| `GET /error-groups` | Distinct errors in a window, most frequent first |
| `GET /error-groups/{group_id}` | One error group and sample occurrences |
| `GET /alerts/incidents` | Open incidents, then the last thirty days |
| `GET /alerts/incidents/{incident_id}` | One incident and who was told |
| `GET /findings` | Detected problems: open, then acknowledged, then closed; at most 200, muted ones apart |
| `GET /findings/stats` | Findings opened and marked false alarms over the last 7 days (`days` up to 90), per service-week |
| `GET /findings/{finding_id}` | One finding, its signals, and its trend while its readings last (35 days) |
| `GET /checks` | Synthetic checks and their confirmed state |
| `GET /checks/{check_id}` | One check |
| `GET /synthetics/checks` | Check results in a window |
| `GET /synthetics/checks/{check_id}/runs` | One check's recent runs |
| `GET /deploys` | Recent deploys, newest first |
| `GET /llm/calls` | Every model call reported in a window, grouped by service, span and model |

Most take `project_id` as a query parameter, and a window as `from` and `to`
(ISO-8601). Leaving the window out reads the last hour.

A finding read with a token leaves out two fields the console shows a signed-in
person: who acknowledged it (`acknowledged_by`) and the reason given for a false
alarm (`feedback_reason`).

**Machine-readable.** The same list is published as OpenAPI 3.1 at
`GET /api/v1/openapi.read.json` — no credential needed to fetch it — so you can
generate a typed client rather than writing calls by hand:

```bash
curl -s https://api.nexovue.com/api/v1/openapi.read.json -o nexovue.json
npx @openapitools/openapi-generator-cli generate \
  -i nexovue.json -g typescript-fetch -o ./nexovue-client
```

It describes this surface and nothing else, declares the bearer token every
endpoint needs, and is generated from the routes themselves — so it cannot
drift from what a token can actually reach.

## Paging

Two endpoints return a cursor, because their lists are long by nature:

```bash
curl -H "Authorization: Bearer $NEXOVUE_TOKEN" \
  "https://api.nexovue.com/api/v1/traces?project_id=$PROJECT&limit=100"
# → { "traces": [...], "next_cursor": "MTc2..." }
```

Pass it back as `cursor` to get the next page. A page without `next_cursor` is
the last one. **Every row appears on exactly one page** — the cursor is a
position, not an offset, so rows arriving while you page do not shift anything.

The other lists are capped rather than paged, and each says so: a response with
`"truncated": true` means the cap bit and there is more. Narrow by service, by
time, or with the endpoint's own `limit`.

## Rate limits

A token gets **300 requests a minute** and 8 in flight at once. That is its own
allowance: two tokens behind one CI runner do not spend each other's, and a
token never spends your colleagues' console budget.

Every response carries the limit, and a refusal tells you when to come back:

```
RateLimit-Policy: "token";q=300;w=60
RateLimit: "token";r=284;t=41
Retry-After: 41          # on a 429 only
```

## Errors

Failures are [RFC 9457 problem documents](https://www.rfc-editor.org/rfc/rfc9457):

```json
{
  "type": "https://nexovue.com/problems/not-found",
  "title": "Not found",
  "status": 404,
  "detail": "No such project.",
  "instance": "urn:nexovue:request:5f1c…"
}
```

| Status | What it means |
|---|---|
| 401 | The token is unknown, revoked, or expired — the detail says which |
| 403 | A real token pointed at something outside the read surface |
| 404 | The project, trace or incident does not exist **or** is outside this token's scope |
| 422 | A parameter is missing or malformed; the detail names it |
| 429 | Over the rate limit; `Retry-After` says when |

Quote `instance` when you ask us about a request: it is how we find it.

## A CI example

Fail a deploy when the error rate of a service is above a threshold over the
last fifteen minutes. It needs `curl` and `jq`, and nothing else.

```bash
#!/usr/bin/env bash
# Fails when SERVICE's error rate over the last 15 minutes exceeds THRESHOLD.
set -euo pipefail

: "${NEXOVUE_TOKEN:?set NEXOVUE_TOKEN}"
: "${NEXOVUE_PROJECT:?set NEXOVUE_PROJECT}"
API="${NEXOVUE_API:-https://api.nexovue.com}"
SERVICE="${SERVICE:-checkout}"
THRESHOLD="${THRESHOLD:-0.02}"

from=$(date -u -v-15M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
  || date -u -d '15 minutes ago' +%Y-%m-%dT%H:%M:%SZ)

rate=$(curl -sS --fail-with-body \
  -H "Authorization: Bearer $NEXOVUE_TOKEN" \
  --get "$API/api/v1/services/$SERVICE" \
  --data-urlencode "project_id=$NEXOVUE_PROJECT" \
  --data-urlencode "from=$from" \
  | jq -r '.service.error_rate // 0')

echo "$SERVICE error rate: $rate (threshold $THRESHOLD)"
awk -v rate="$rate" -v limit="$THRESHOLD" \
  'BEGIN { exit (rate > limit) ? 1 : 0 }'
```

Exit 0 means the service is within the threshold; exit 1 fails the pipeline.

## Connect an AI assistant

MCP is an open protocol, and this is an ordinary remote MCP server. Any client
that can reach one with a custom header works — the two things every client
needs are the same:

```
Endpoint:  https://api.nexovue.com/api/mcp/
Header:    Authorization: Bearer nvt_live_…
```

**Claude Code**

```bash
claude mcp add --transport http nexovue https://api.nexovue.com/api/mcp/ \
  --header "Authorization: Bearer $NEXOVUE_TOKEN"
```

**Cursor** — `~/.cursor/mcp.json`, or `.cursor/mcp.json` for one project

```json
{
  "mcpServers": {
    "nexovue": {
      "url": "https://api.nexovue.com/api/mcp/",
      "headers": { "Authorization": "Bearer nvt_live_…" }
    }
  }
}
```

**VS Code (GitHub Copilot)** — `.vscode/mcp.json`. The key is `servers`, not
`mcpServers`, and tools only appear in **agent mode** — in ask mode the server
looks connected and offers nothing.

```json
{
  "servers": {
    "nexovue": {
      "type": "http",
      "url": "https://api.nexovue.com/api/mcp/",
      "headers": { "Authorization": "Bearer nvt_live_…" }
    }
  }
}
```

**Codex** — `~/.codex/config.toml`. The CLI, the IDE extension and the ChatGPT
desktop app share this file.

```toml
[mcp_servers.nexovue]
url = "https://api.nexovue.com/api/mcp/"
http_headers = { "Authorization" = "Bearer nvt_live_…" }
```

Keeping the token out of the file is better, and Codex supports it directly —
`bearer_token_env_var` names an environment variable and Codex sends it as the
bearer:

```toml
[mcp_servers.nexovue]
url = "https://api.nexovue.com/api/mcp/"
bearer_token_env_var = "NEXOVUE_TOKEN"
```

`codex mcp add nexovue --url … --bearer-token-env-var NEXOVUE_TOKEN` writes that
second form for you. There is no `--header` flag, so a literal header is
hand-edited. Codex *cloud* tasks are not covered — MCP there is undocumented.

**Windsurf** — `~/.codeium/windsurf/mcp_config.json`, with `serverUrl`:

```json
{
  "mcpServers": {
    "nexovue": {
      "serverUrl": "https://api.nexovue.com/api/mcp/",
      "headers": { "Authorization": "Bearer nvt_live_…" }
    }
  }
}
```

**Zed** — `settings.json`, under `context_servers`. Without the `Authorization`
header Zed starts an OAuth flow instead, which this server does not serve.

```json
{
  "context_servers": {
    "nexovue": {
      "url": "https://api.nexovue.com/api/mcp/",
      "headers": { "Authorization": "Bearer nvt_live_…" }
    }
  }
}
```

**Gemini CLI** — `~/.gemini/settings.json`. The field is `httpUrl`; plain `url`
means Server-Sent Events, which is a different transport.

```json
{
  "mcpServers": {
    "nexovue": {
      "httpUrl": "https://api.nexovue.com/api/mcp/",
      "headers": { "Authorization": "Bearer nvt_live_…" }
    }
  }
}
```

**JetBrains IDEs, Cline, Roo Code and Continue** take the same URL-and-header
pair in their own MCP settings.

**Claude Desktop and claude.ai** are the awkward ones. Remote servers go through
**Settings → Connectors → Add custom connector**, not the config file — a `url`
in `claude_desktop_config.json` is ignored silently. Enter the endpoint, then a
request header named `authorization` with the value `Bearer nvt_live_…`. Request
headers there are still in beta and are not enabled for every organization yet.

**ChatGPT** cannot hold a static API key for a connector today; OpenAI supports
OAuth or no authentication. OpenAI's Responses API can connect, by passing the
token in its `authorization` field.

### Asking it things

Once connected: *"what is failing in production?"*, *"what deployed just before
the checkout errors started?"*, *"show me the slowest service this hour"*.

The server offers these tools, and no others:

`list_projects`, `list_services`, `get_service`, `list_traces`, `search_logs`,
`list_error_groups`, `list_incidents`, `list_checks`, `list_deploys`.

Three things are true of all of them:

- **Every tool reads.** There is no tool that changes a check, an alert rule, a
  member or a setting. A model cannot alter your account through this.
- **A tool sees exactly what the token sees.** Same project scope, same
  refusals, same rate limit. Revoking the token ends the assistant's access on
  its next call.
- **Answers are bounded** — at most 100 rows and 32 KB per call — and a cut
  answer says so and says how to narrow, so a model never treats a partial
  answer as the whole picture.

Every tool call counts against the token's rate limit and updates its *last
used*, so the token list in the console shows you when an assistant last read.
