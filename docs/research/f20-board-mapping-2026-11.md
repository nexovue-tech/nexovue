# F20 board mapping: ten sample dashboards against F20's panel kinds (2026-11)

> **Internal research, not a comparison or a claim.** Written 2026-11-08 from files read that day. Another product's dashboard format is named here only to identify the files; nothing in this file may be quoted in sales or marketing (Phase 2 foundation, founder decision 15). Anything not checked is marked *unverified* in the text.

> **Type:** Research report for [`plan.md`](../features/dashboards/plan.md) task T2.0 (F20, Dashboards), written before Step 2.
> **Status:** Proposed. Nothing here is approved until the founder approves it.
> **Date:** 2026-11-08.
> **Source:** the ten dashboard files in `src/grafana/provisioning/dashboards/demo/` of the OpenTelemetry Demo at tag `3.0.0`, the tag `tools/otel-demo/otel-demo.sh` pins (`UPSTREAM_REF`). The checkout is not in this repository (the script clones it at run time), so these files were read from a separate clone at the clone's `HEAD` commit `1755859a9de82c2e5e225be68abc401a5ebf2b4f`. That this commit is tag `3.0.0`'s was not re-checked here (*unverified*). The path the plan gives, `tools/otel-demo/upstream/src/grafana/provisioning/dashboards/demo/`, is where `otel-demo.sh` puts the same files.
> **Used by:** the buyer round, question Q37 ([`phase-2-buyer-round.md`](../product/phase-2-buyer-round.md), Part 7). The plan and spec say this report "goes into the buyer conversations".

## The short answer

- **143 panels, the same as the plan's 143.** The count matches, so the plan's figure stands.
- **7 map to a panel kind (4.9%), 68 map partly (47.6%), 68 are not offered (47.6%).** Six of the seven that map are plain text notes. Only one panel that draws data maps one-for-one, and it needs the metric series panel (plan Step 8).
- **40 of the 75 that map or partly map need the metric series panel, which plan Step 8 builds** and the templates-only fallback drops. Without Step 8: 6 map (4.2%), 29 map partly (20.3%), 108 are not offered (75.5%).
- **The biggest losses:** calculations across metrics (39 panels), many lines in one chart (32 panels split into one panel per line), machine facts nexovue does not read (11), logs (6), histograms and exemplars (4).
- **These are the demo's sample boards, not a buyer's.** They say what F20's fixed list can and cannot express. They do not say what share of a buyer's own boards will carry over. Q37 asks the buyer that.
- **The mapping here is a reading of the files against the design.** F20 is not built beyond Step 1 (`status.md`). No panel was drawn and no number was compared.

## Words used here

| Word | Meaning |
|---|---|
| **Board** | One saved dashboard, here one JSON file |
| **Panel** | One chart, number or table on a board |
| **Panel kind** | One of the fixed panel types F20 offers (`spec.md` FR6; `design.md`). There is no free-form query |
| **Metric** | A named measurement a program sends, such as requests served. A **counter** only rises (a running total); a **gauge** goes up and down |
| **Series** | One line's worth of a metric: the metric plus one fixed set of labels, such as `service = checkout` |
| **Query** | The text a panel sends to a data store. These boards use the metric store's own query language (PromQL) for metrics, and other query languages for logs and traces |
| **Histogram** | A metric that counts how many readings fell into each size band, so a percentile such as the 95th can be worked out later |
| **Exemplar** | A dot on a latency chart that links a metric reading to one example trace |
| **Collector** | The OpenTelemetry Collector, the program that receives, changes and forwards telemetry |
| **SDK** | The library inside an application that creates telemetry |
| **Row** | A header on a board that groups panels. It can be **collapsed**, which hides the panels under it |

## Source and method

**What the files are.** The OpenTelemetry Demo ships ten dashboards for the demo's own observability stack: a metric store, a log store and a trace store (named only as data sources: Prometheus, OpenSearch and Jaeger). The files are in the dashboard tool's JSON format. They were provisioned with the demo and are not a customer's boards.

| File | Title | What the board is about |
|---|---|---|
| `NGINX-metrics.json` | NGINX Metrics | a web server's own counters |
| `apm-dashboard.json` | APM Dashboard | one service's rate, errors, latency, calls out, logs and traces |
| `demo-dashboard.json` | Demo Dashboard | per-operation request health, logs, a few custom metrics, a service graph |
| `events-by-name.json` | Events by Name | log events counted by name |
| `exemplars-dashboard.json` | Cart Service Exemplars | latency histograms with trace-linked dots |
| `linux-dashboard.json` | Linux | one machine's CPU, memory, disk, network |
| `opentelemetry-collector.json` | OpenTelemetry Collector | the collector's own receive, process and export counters |
| `postgresql-dashboard.json` | PostgreSQL | a database server's own counters |
| `self-observability.json` | Self-Observability | the SDKs' own health counters |
| `spanmetrics-dashboard.json` | Spanmetrics Demo Dashboard | top services and operations by rate, errors, latency |

**How the panels were counted.** A script (below) parsed each file as JSON. It took the board's top-level `panels` list and, for every panel, also walked that panel's own `panels` list, which is where a collapsed row keeps the panels hidden under it. It counted every panel whose `type` is not `row`.

```python
import json, sys
from pathlib import Path

def walk(panels):
    for p in panels:
        yield p
        yield from walk(p.get("panels", []))   # panels inside a collapsed row

total = 0
for f in sorted(Path(sys.argv[1]).glob("*.json")):
    board = json.loads(f.read_text())
    ps = list(walk(board.get("panels", [])))
    rows = [p for p in ps if p["type"] == "row"]
    total += len(ps) - len(rows)
print("total", total)
```

Run as `python3 -I count.py <path to the demo folder>`. The script is not committed; it is the whole of the method.

| Count | Number |
|---|---|
| Panels that are not row headers (the figure used everywhere here) | **143** |
| Row headers (not counted as panels) | 28 |
| Collapsed rows | 6 (APM Dashboard 1, OpenTelemetry Collector 5) |
| Panels hidden under those collapsed rows (counted) | 27 (APM Dashboard 1, OpenTelemetry Collector 26) |

**It matches the plan.** `plan.md` T2.0 says 143, counted on 2026-11-02. This count gives 143. The two figures agree only because collapsed rows are counted into and row headers out: counting only what is visible on first load would give 116, and counting row headers as panels would give 171. The plan does not say how it counted, so that the same rule was used is an inference from the matching figure (*unverified*). A search of the files' text found no library panels and no repeated panels (`repeat`), which would have changed a count.

**One board repeats itself.** The OpenTelemetry Collector board asks the same receiver, processor and exporter questions twice: once in its Overview and again in the collapsed rows. 19 of its 49 panels are second copies. They are counted as the plan counts them, and marked "second copy" in the tables.

**How each panel was judged.** Each panel's type, title, description and queries were read. The data stores' queries are summarised in plain words in the tables, not pasted. The panel was then set against the design's list of panel kinds ([`spec.md`](../features/dashboards/spec.md) FR6, [`design.md`](../features/dashboards/design.md) section 4) and given one result:

- **Maps.** The same question and the same quantity, at a scope the kind offers, and the kind's look is an accepted way to show it. A text panel that can be retyped as a note counts.
- **Partly.** A panel kind answers a nearby question, and something is lost. The table says what. The loss falls into three kinds: **split** (one chart of many lines, where F20 has one line per panel; the spec rules out "group by any label"), **look** (a number or a dial becomes a line, or a graph becomes a table) and **content** (a filter, a percentile, a scale or a scope is lost).
- **Not offered.** No panel kind answers the question, and the table says why.

These rules are this report's, and a different reading moves the shares. The clearest case is the 13 panels that map partly **only** because a number or dial becomes a line. If a buyer does not mind that, they count as maps.

**Where the numbers would come from.** The boards read the demo's metric store. F20's panels read nexovue's own stored telemetry. A panel "maps" when F20 can answer the same question from nexovue's data. Whether the numbers would be equal was not checked (*unverified*). In particular, rate, error and latency panels on these boards come from "span metrics" the collector makes from traces. F20's request panels read nexovue's own per-minute totals made from the same traces (`design.md`, section 4), which is a different calculation of the same thing.

**Board variables are judged on the board, not on each panel.** The ten boards hold 33 variables, the drop-down filters at the top of a board. Of these, 6 choose a service, which F20's one service picker covers. 8 choose a data source and 19 are other filters. See *What is lost*.

## Totals

Counts and shares, overall and per board. A panel is counted once, under the result the board tables below give it.

### Overall

| | Panels | Share |
|---|---|---|
| Maps to a panel kind | 7 | 4.9% |
| Partly maps (something is lost) | 68 | 47.6% |
| Not offered | 68 | 47.6% |
| **All panels** | **143** | 100% |

Shares are rounded to one decimal, so the three add to 100.1%.

### Per board

| Board | Panels | Maps | Partly | Not offered | Maps or partly |
|---|---|---|---|---|---|
| NGINX Metrics | 4 | 0 (0.0%) | 4 (100.0%) | 0 (0.0%) | 4 (100.0%) |
| APM Dashboard | 14 | 0 (0.0%) | 9 (64.3%) | 5 (35.7%) | 9 (64.3%) |
| Demo Dashboard | 11 | 2 (18.2%) | 7 (63.6%) | 2 (18.2%) | 9 (81.8%) |
| Events by Name | 2 | 0 (0.0%) | 0 (0.0%) | 2 (100.0%) | 0 (0.0%) |
| Cart Service Exemplars | 5 | 1 (20.0%) | 0 (0.0%) | 4 (80.0%) | 1 (20.0%) |
| Linux | 19 | 0 (0.0%) | 9 (47.4%) | 10 (52.6%) | 9 (47.4%) |
| OpenTelemetry Collector | 49 | 3 (6.1%) | 14 (28.6%) | 32 (65.3%) | 17 (34.7%) |
| PostgreSQL | 6 | 0 (0.0%) | 4 (66.7%) | 2 (33.3%) | 4 (66.7%) |
| Self-Observability | 25 | 0 (0.0%) | 14 (56.0%) | 11 (44.0%) | 14 (56.0%) |
| Spanmetrics Demo Dashboard | 8 | 1 (12.5%) | 7 (87.5%) | 0 (0.0%) | 8 (100.0%) |
| **All ten** | **143** | **7 (4.9%)** | **68 (47.6%)** | **68 (47.6%)** | **75 (52.4%)** |

### By panel kind

A panel is counted under the one kind it was matched to. Kinds that no panel matched are listed after the table.

| F20 panel kind | Plan step that builds it | Maps | Partly | Maps or partly |
|---|---|---|---|---|
| `metric_series` | 8 (T8.2) | 1 | 39 | 40 |
| `node_trend` | 4 (T4.2) | 0 | 9 | 9 |
| `note` | 2 | 6 | 1 | 7 |
| `endpoints_table` | 5 (T5.2, T5.6a) | 0 | 5 | 5 |
| `dependencies_table` | 5 (T5.2, T5.6a) | 0 | 4 | 4 |
| `endpoint_trend` | 5 (T5.1a) | 0 | 4 | 4 |
| `service_trend` | 2 | 0 | 3 | 3 |
| `services_table` | 5 (T5.2, T5.6a) | 0 | 2 | 2 |
| `top_services_trend` | 4 (T4.1) | 0 | 1 | 1 |
| **All** | | **7** | **68** | **75** |

No panel matched `project_trend`, `top_nodes_trend`, `check_trend`, `statement_trend`, `project_number`, `service_number`, `open_findings`, `database_statements_table`, `error_groups_table`, `web_vitals_table` or `slo_budget`. Those kinds answer questions that none of these ten boards asks (project-wide totals, checks, database statements, errors, browser readings, findings, targets).

### By what the board is about

| Group | Boards | Panels | Maps | Partly | Not offered |
|---|---|---|---|---|---|
| Request health (rate, errors, latency of services) | APM Dashboard, Demo Dashboard, Spanmetrics Demo Dashboard | 33 | 3 (9.1%) | 23 (69.7%) | 7 (21.2%) |
| Health of the telemetry pipeline itself (collector, SDK self-metrics) | OpenTelemetry Collector, Self-Observability | 74 | 3 (4.1%) | 28 (37.8%) | 43 (58.1%) |
| Machines and other software (hosts, web server, database server) | NGINX Metrics, Linux, PostgreSQL | 29 | 0 (0.0%) | 17 (58.6%) | 12 (41.4%) |
| Logs and exemplars | Events by Name, Cart Service Exemplars | 7 | 1 (14.3%) | 0 (0.0%) | 6 (85.7%) |

### If the metric series panel (plan Step 8) is not built

40 of the 75 panels that map or partly map need `metric_series`, which plan Step 8 builds and which the templates-only fallback drops (spec, founder decision 1). Without it:

| | Panels | Share |
|---|---|---|
| Maps | 6 | 4.2% |
| Partly maps | 29 | 20.3% |
| Not offered (68 anyway, 40 more because they wait on Step 8) | 108 | 75.5% |

### Why the partly-mapped panels are partly

A partly-mapped panel can lose more than one thing, so these overlap.

| What is lost | Panels (of 68) |
|---|---|
| One chart of many lines becomes one line, one panel per line, or one combined line (split) | 32 |
| A number, dial or graph becomes a line or a table (look) | 31 |
| Something else in the content or scope: a filter, a percentile, a scale, project-wide instead of one service (content) | 28 |
| Of these, partly **only** because a number or dial becomes a line | 13 |

### Why the not-offered panels are not offered

| Reason | Panels (of 68) |
|---|---|
| Calculation across metrics (ratio, sum of two metrics, average from a histogram's sum and count) | 39 |
| Machine facts and tables not among the machine metrics nexovue reads | 11 |
| Logs | 6 |
| Other (process-uptime tables, a list of reporting components, "last received" time) | 5 |
| Histograms, heatmaps, exemplars | 4 |
| List of alert rules | 2 |
| List of traces | 1 |

## The ten boards, panel by panel

Each table lists every panel on the board, in the order the file holds them. The "No." is the panel's own number in the file. A title shown in brackets means the panel has no title in the file, and the bracket says what it is. "F20 panel kind" uses the design's names (`spec.md` FR6). The step that builds each kind is in the *By panel kind* table above.

### NGINX Metrics

`NGINX-metrics.json`, 4 panels. Panel numbers are the board's own.

| No. | Panel | Visual type | What it shows | F20 panel kind | Fit, and what is lost or why not |
|---|---|---|---|---|---|
| 1 | Accepted connections | stat | running total of client connections the web server has accepted (a counter) | `metric_series` | **Partly.** Step 8 draws the counter's rise over time as a line; the single running total is not shown. |
| 3 | Connections handled | stat | running total of connections the web server handled | `metric_series` | **Partly.** Same as above. |
| 4 | Total number of client requests | stat | running total of requests the web server has served | `metric_series` | **Partly.** Same as above. |
| 2 | Number of connections by state | timeseries | connections open right now, one line per connection state | `metric_series` | **Partly.** One line per state becomes one panel per state, or one combined line (spec: group by any label is out of scope). |

### APM Dashboard

`apm-dashboard.json`, 14 panels. Panel numbers are the board's own.

| No. | Panel | Visual type | What it shows | F20 panel kind | Fit, and what is lost or why not |
|---|---|---|---|---|---|
| 20 | (no title: heading) | text (HTML) | a heading with the OpenTelemetry logo and the service name filled in from the board's variables | `note` | **Partly.** The logo and the live service name are lost; notes are plain text only (spec BR10). |
| 39 | Latest metrics received | stat | time the newest metrics arrived from this service in the last 24 hours | not offered | **Not offered.** No panel shows when a service last reported; that time appears only inside empty-panel messages. |
| 17 | Duration | timeseries | response time of the service's HTTP and gRPC server calls: 95th percentile and average, for each protocol (four lines) | `service_trend` (latency) | **Partly.** One chart of p50, p95 and p99 for all server calls; the average and the HTTP / gRPC split are lost. |
| 18 | Error | timeseries | share of HTTP calls answered 5xx and of gRPC calls with a non-OK status | `service_trend` (errors) | **Partly.** One error line by nexovue's own definition of an error (how it treats 5xx is unverified); protocols not shown apart. |
| 19 | Request Rate | timeseries | HTTP and gRPC requests per second, two lines | `service_trend` (rate) | **Partly.** One combined line; the protocol split is lost. |
| 40 | Alerts | alertlist | a list of the dashboard tool's own alert rules and their state | not offered | **Not offered.** No panel lists alerts; alert incidents appear in the strip under the header, not as a panel (spec: alerts from a panel is out of scope). |
| 21 | HTTP Operations | table | per HTTP route: requests per second, 5xx percent, 99th-percentile latency | `endpoints_table` | **Partly.** The table lists the service's HTTP and gRPC endpoints together; column set is the table's own (not checked). |
| 27 | gRPC Operations | table | per gRPC method: requests per second, error percent, 99th-percentile latency | `endpoints_table` | **Partly.** Same table as the HTTP one; the protocol filter is lost. |
| 23 | Outbound HTTP Services | table | per outgoing HTTP target (host, method, URL template): calls per second, 5xx percent, p99 | `dependencies_table` | **Partly.** Lists which services call which, project-wide, not one service's calls; per-URL detail lost. |
| 24 | Outbound Databases | table | per database the service calls (server, name): calls per second, errors, p99 | `dependencies_table` | **Partly.** Project-wide list of dependencies; whether a database shows as one depends on how the service map treats it (unverified). |
| 32 | Outbound gRPC Services | table | per outgoing gRPC service and method: calls per second, errors, p99 | `dependencies_table` | **Partly.** Project-wide list of dependencies; per-method detail lost. |
| 41 | Service instance / host | table | CPU percent and memory percent of each service instance, joined to the host it runs on | not offered | **Not offered.** No table of machines; node charts are lines, and joining a service instance to its host is not offered. |
| 26 | (no title: logs) | table | recent log lines of the service, read from a log store with a query language | not offered | **Not offered.** Logs are not offered on a dashboard (spec: error groups and notes only). |
| 30 | (no title: traces) | table | traces that contain a span from the service, searched in a trace store | not offered | **Not offered.** No panel lists traces; the console has a Traces screen, which is not a panel. |

### Demo Dashboard

`demo-dashboard.json`, 11 panels. Panel numbers are the board's own.

| No. | Panel | Visual type | What it shows | F20 panel kind | Fit, and what is lost or why not |
|---|---|---|---|---|---|
| 21 | (no title: description) | text (HTML) | two sentences saying what the board shows and a warm-up delay | `note` | **Maps.** Retype the sentences; the HTML line break is dropped. |
| 12 | Requests Rate by Span Name | timeseries | requests per second, one line per operation (span name), for the chosen services | `endpoint_trend` (rate) | **Partly.** One operation per panel; many lines in one chart are lost. |
| 10 | Error Rate by Span Name | timeseries | failing calls per second, one line per operation | `endpoint_trend` (errors) | **Partly.** One operation per panel. |
| 2 | Average Duration by Span Name | timeseries | median (50th percentile) latency, one line per operation; the title says average | `endpoint_trend` (latency) | **Partly.** One operation per panel. |
| 20 | Log Records by Severity | table | count of log lines by severity, from a log store | not offered | **Not offered.** Logs are not offered on a dashboard. |
| 17 | Log Records (100 recent entries) | table | the 100 newest log lines of the chosen services | not offered | **Not offered.** Logs are not offered on a dashboard. |
| 6 | Python services (CPU%) | timeseries | CPU time used per second, system and user, as a percent, per Python service | `metric_series` | **Partly.** System and user are two series, so two panels; one line per service lost; the percent scaling is lost. |
| 8 | Python services (Memory) | timeseries | resident memory of each Python service | `metric_series` | **Partly.** One line per service becomes one combined line, or one panel per service. |
| 4 | Recommendations Rate | timeseries | rate of a custom counter of recommendation requests, type catalog | `metric_series` | **Maps.** Maps if the filter leaves one series; whether it does on a real install is unverified. |
| 16 | Quote Service batch span processor | timeseries | spans handled per second by one service's span processor, by state, times 120 | `metric_series` | **Partly.** One line per state becomes one panel per state; the times-120 scaling is lost. |
| 22 | Service Dependency | nodeGraph | a graph of which services call which, drawn from the trace store | `dependencies_table` | **Partly.** A table of the busiest dependencies, not a graph; the console's Service map screen is not a panel. |

### Events by Name

`events-by-name.json`, 2 panels. Panel numbers are the board's own.

| No. | Panel | Visual type | What it shows | F20 panel kind | Fit, and what is lost or why not |
|---|---|---|---|---|---|
| 1 | Top Event Names by Volume | table | the 20 most frequent log event names with counts, filtered by service, event name and severity | not offered | **Not offered.** Logs are not offered on a dashboard. |
| 2 | Event Volume Over Time (Top Event Names) | timeseries | log events over time, split by event name, searched with a text-search query | not offered | **Not offered.** Logs are not offered on a dashboard. |

### Cart Service Exemplars

`exemplars-dashboard.json`, 5 panels. Panel numbers are the board's own.

| No. | Panel | Visual type | What it shows | F20 panel kind | Fit, and what is lost or why not |
|---|---|---|---|---|---|
| 8 | (no title: description) | text (HTML) | a few sentences saying what exemplars are | `note` | **Maps.** Retype the sentences; the HTML line break is dropped. |
| 2 | GetCart Latency Heatmap with Exemplars | heatmap | how the cart service's GetCart latency spreads across histogram buckets over time, with dots that open a trace | not offered | **Not offered.** Histograms, heatmaps and exemplars are not offered (spec D49). |
| 5 | 95th Pct Cart GetCart Latency with Exemplars | timeseries | 95th percentile of a custom latency histogram, with trace-linked dots | not offered | **Not offered.** Custom histograms are not offered; the nearest is endpoint_trend latency from span durations, a different measurement, so counted as not offered. |
| 6 | AddItem Latency Heatmap with Exemplars | heatmap | the same heatmap for the AddItem call | not offered | **Not offered.** Histograms, heatmaps and exemplars are not offered. |
| 1 | 95th Pct Cart AddItem Latency with Exemplars | timeseries | 95th percentile of the AddItem latency histogram, with trace-linked dots | not offered | **Not offered.** Custom histograms are not offered; same note as above. |

### Linux

`linux-dashboard.json`, 19 panels. Panel numbers are the board's own.

| No. | Panel | Visual type | What it shows | F20 panel kind | Fit, and what is lost or why not |
|---|---|---|---|---|---|
| 300 | Host Metadata | table | host and operating-system facts (name, OS, and so on) read from a resource-info metric | not offered | **Not offered.** Facts about a machine are not a machine metric nexovue draws. |
| 301 | CPU Metadata | table | CPU model and similar facts | not offered | **Not offered.** Same as above. |
| 20 | CPU Busy | gauge | share of CPU in use right now, all cores | `node_trend` (system.cpu.utilization) | **Partly.** A line over time for one machine; no dial or colour bands. |
| 16 | RAM Used | gauge | share of memory in use right now | `node_trend` (system.memory.utilization) | **Partly.** A line, not a dial. |
| 154 | FS Used | gauge | share of disk space used, computed from free and total bytes | `node_trend` (system.filesystem.usage) | **Partly.** A line of bytes used; the used share needs a division the kind may not do (unverified). |
| 155 | Sys Load (5m avg) | gauge | five-minute load average | not offered | **Not offered.** Load average is not among the machine metrics nexovue reads. |
| 19 | Sys Load (15m avg) | gauge | fifteen-minute load average | not offered | **Not offered.** Load average is not among the machine metrics nexovue reads. |
| 21 | SWAP Used | gauge | share of swap space used, from paging bytes | `node_trend` (system.swap.utilization) | **Partly.** A line; the board reads paging bytes and nexovue reads a swap-utilization metric, so the same data arriving is unverified. |
| 14 | CPU Cores | stat | number of logical CPUs | not offered | **Not offered.** Not a machine metric nexovue reads. |
| 75 | RAM Total | stat | total memory of the machine | `node_trend` (system.memory.limit) | **Partly.** A flat line of a constant, not a number. |
| 18 | SWAP Total | stat | total swap space, from paging bytes | not offered | **Not offered.** Paging bytes are not a metric nexovue reads. |
| 23 | FS Total | stat | total disk size, adding every state of the filesystem-usage metric | not offered | **Not offered.** The total is every state of the usage metric added together, which a single machine line does not do. |
| 15 | Uptime | stat | seconds since the machine started | not offered | **Not offered.** Not a machine metric nexovue reads. |
| 77 | CPU | timeseries | share of CPU time by mode: system, user, wait, interrupts, other, idle (six lines) | `node_trend` (system.cpu.utilization) | **Partly.** One line per machine metric; the split by mode is lost. |
| 78 | Memory | timeseries | memory by state: available, free, cache plus buffer, used (four lines) | `node_trend` (system.memory.usage) | **Partly.** One line; the split by state is lost. |
| 74 | Network Traffic | timeseries | bytes per second received and sent | `node_trend` (system.network.io) | **Partly.** The split by direction is not offered (unverified how the line is drawn). |
| 42 | I/O Usage Read / Write | timeseries | disk bytes per second read and written | `node_trend` (system.disk.io) | **Partly.** The split by direction is not offered (unverified how the line is drawn). |
| 270 | Network traffic | table | per network device: bytes, dropped packets and errors in each direction | not offered | **Not offered.** No table of a machine's devices, and no split by device. |
| 269 | Disk usage | table | per mount point: free and total bytes | not offered | **Not offered.** No table of a machine's disks, and no split by mount point. |

### OpenTelemetry Collector

`opentelemetry-collector.json`, 49 panels. Panel numbers are the board's own.

| No. | Panel | Visual type | What it shows | F20 panel kind | Fit, and what is lost or why not |
|---|---|---|---|---|---|
| 57 | (no title: collectors) | table | the running collectors and how long each has been up | not offered | **Not offered.** A table of process uptimes is not a panel kind. |
| 56 | Alerts | alertlist | a list of alert rules and their state | not offered | **Not offered.** No panel lists alerts. |
| 63 | (no title: label) | text (markdown) | the word Receivers, a section label | `note` | **Maps.** Plain text. |
| 64 | Spans | stat | spans per second received, accepted plus refused | not offered | **Not offered.** Adds two different metrics; metric_series reads one. |
| 65 | Span Errors | gauge | share of received spans that were refused: 1 minus accepted over (accepted plus refused) | not offered | **Not offered.** A ratio of two metrics. |
| 66 | Logs | stat | log records per second received, accepted plus refused | not offered | **Not offered.** Adds two different metrics. |
| 67 | Log Errors | gauge | share of received log records that were refused | not offered | **Not offered.** A ratio of two metrics. |
| 68 | Metrics | stat | metric points per second received, accepted plus refused | not offered | **Not offered.** Adds two different metrics. |
| 69 | Metric Errors | gauge | share of received metric points that were refused | not offered | **Not offered.** A ratio of two metrics. |
| 80 | (no title: label) | text (markdown) | the word Processors, a section label | `note` | **Maps.** Plain text. |
| 88 | Spans | stat | spans per second entering the processors, traces only | `metric_series` | **Partly.** The filter on one signal becomes 'choose one series'; with several processors reporting, they would have to be chosen one by one or added together. |
| 89 | Drop Rate | gauge | share of spans that entered the processors and did not come out: (in minus out) over in | not offered | **Not offered.** A ratio of two metrics. |
| 90 | Logs | stat | log records per second entering the processors | `metric_series` | **Partly.** Same as the traces version. |
| 91 | Drop Rate | gauge | share of log records dropped by the processors | not offered | **Not offered.** A ratio of two metrics. |
| 92 | Metrics | stat | metric points per second entering the processors | `metric_series` | **Partly.** Same as the traces version. |
| 93 | Drop Rate | gauge | share of metric points dropped by the processors | not offered | **Not offered.** A ratio of two metrics. |
| 87 | (no title: label) | text (markdown) | the word Exporters, a section label | `note` | **Maps.** Plain text. |
| 74 | Spans | stat | spans per second sent by all exporters added together | `metric_series` | **Partly.** All series combined into one line; number becomes a line. |
| 82 | Span Errors | gauge | share of spans that failed (this query reads the receiver counters although the panel sits under Exporters) | not offered | **Not offered.** A ratio of two metrics. |
| 76 | Logs | stat | log records per second sent by all exporters | `metric_series` | **Partly.** Number becomes a line. |
| 77 | Logs Errors | gauge | share of log records that failed to send | not offered | **Not offered.** A ratio of two metrics. |
| 78 | Metrics | stat | metric points per second sent by all exporters | `metric_series` | **Partly.** Number becomes a line. |
| 79 | Metrics Errors | gauge | share of metric points that failed to send | not offered | **Not offered.** A ratio of two metrics. |
| 54 | (no title: collectors) | table | the running collectors and how long each has been up (second copy, inside the Collector row) | not offered | **Not offered.** A table of process uptimes is not a panel kind. |
| 50 | CPU Usage | stat | CPU time used per second by each collector process | `metric_series` | **Partly.** One stat per collector instance becomes one combined line, or one panel per instance. |
| 52 | Memory Usage (RSS) | stat | resident memory of each collector process | `metric_series` | **Partly.** One stat per instance becomes one combined line, or one panel per instance. |
| 51 | Uptime | stat | seconds each collector has been up | not offered | **Not offered.** A running total whose current value is the point; the metric series panel draws a counter's rise, which here is a flat line. |
| 39 | Span Rate | stat | spans per second received, accepted plus refused (second copy) | not offered | **Not offered.** Adds two different metrics. |
| 40 | Span Errors | gauge | share of received spans refused (second copy) | not offered | **Not offered.** A ratio of two metrics. |
| 59 | Logs | stat | log records per second received (second copy) | not offered | **Not offered.** Adds two different metrics. |
| 61 | Log Errors | gauge | share of log records refused (second copy) | not offered | **Not offered.** A ratio of two metrics. |
| 60 | Metrics | stat | metric points per second received (second copy) | not offered | **Not offered.** Adds two different metrics. |
| 62 | Metric Errors | gauge | share of metric points refused (second copy) | not offered | **Not offered.** A ratio of two metrics. |
| 41 | Receivers | table | per receiver and signal: accepted rate, refused rate, failed rate, error percent | not offered | **Not offered.** Several metrics side by side, with a calculated column. |
| 81 | Spans | stat | spans per second entering the processors (second copy) | `metric_series` | **Partly.** Same as the first copy. |
| 75 | Drop Rate | gauge | share of spans dropped by the processors (second copy) | not offered | **Not offered.** A ratio of two metrics. |
| 83 | Logs | stat | log records per second entering the processors (second copy) | `metric_series` | **Partly.** Same as the first copy. |
| 84 | Drop Rate | gauge | share of log records dropped (second copy) | not offered | **Not offered.** A ratio of two metrics. |
| 85 | Metrics | stat | metric points per second entering the processors (second copy) | `metric_series` | **Partly.** Same as the first copy. |
| 86 | Drop Rate | gauge | share of metric points dropped (second copy) | not offered | **Not offered.** A ratio of two metrics. |
| 49 | Processors | table | per processor and signal: discarded percent, inbound rate, outbound rate | not offered | **Not offered.** Several metrics side by side, with a calculated column. |
| 43 | Spans | stat | spans per second sent by all exporters (second copy) | `metric_series` | **Partly.** Number becomes a line. |
| 44 | Span Errors | gauge | share of spans that failed to send (second copy) | not offered | **Not offered.** A ratio of two metrics. |
| 70 | Logs | stat | log records per second sent by all exporters (second copy) | `metric_series` | **Partly.** Number becomes a line. |
| 72 | Logs Errors | gauge | share of log records that failed to send (second copy) | not offered | **Not offered.** A ratio of two metrics. |
| 71 | Metrics | stat | metric points per second sent by all exporters (second copy) | `metric_series` | **Partly.** Number becomes a line. |
| 73 | Metrics Errors | gauge | share of metric points that failed to send (second copy) | not offered | **Not offered.** A ratio of two metrics. |
| 45 | Exporters | table | per exporter and signal: sent rate, failure rate, queue size and queue percent | not offered | **Not offered.** Several metrics side by side, with a calculated column. |
| 42 | OpenTelemetry Collector Logs | logs | the collector's own recent log lines, read from a log store | not offered | **Not offered.** Logs are not offered on a dashboard. |

### PostgreSQL

`postgresql-dashboard.json`, 6 panels. Panel numbers are the board's own.

| No. | Panel | Visual type | What it shows | F20 panel kind | Fit, and what is lost or why not |
|---|---|---|---|---|---|
| 11 | QPS | stat | queries per second, taken as commits plus rollbacks, added across databases | not offered | **Not offered.** Adds two different metrics. |
| 1 | Rows | timeseries | rows fetched, returned, inserted, updated and deleted per second (five counters) | `metric_series` | **Partly.** Five counters on one chart become five panels. |
| 2 | Buffers | timeseries | buffers allocated and written, backend and clean writes, checkpoints (five metrics, two from a different exporter's names) | `metric_series` | **Partly.** Five metrics on one chart become five panels. |
| 3 | Conflicts/Deadlocks | timeseries | running totals of deadlocks and of conflicts | `metric_series` | **Partly.** Two panels; the panel draws the rise of a counter, not its running total. |
| 12 | Cache hit ratio | timeseries | per database: blocks found in cache over (found plus read) | not offered | **Not offered.** A ratio of two metrics. |
| 13 | Number of active connections | timeseries | open connections, one line per database | `metric_series` | **Partly.** One line per database becomes one panel per database, or one combined line. |

### Self-Observability

`self-observability.json`, 25 panels. Panel numbers are the board's own.

| No. | Panel | Visual type | What it shows | F20 panel kind | Fit, and what is lost or why not |
|---|---|---|---|---|---|
| 2 | Services reporting | stat | how many services currently send the SDK's own health metrics | not offered | **Not offered.** A count of distinct series matching a name pattern. |
| 3 | Spans exported | stat | spans per second exported successfully, summed over the chosen services | `metric_series` | **Partly.** Number becomes a line; the filter on the success series becomes 'choose one series'. |
| 4 | Metric data points exported | stat | metric points per second exported successfully | `metric_series` | **Partly.** Same as above. |
| 5 | LogRecords exported | stat | log records per second exported successfully | `metric_series` | **Partly.** Same as above. |
| 6 | Export errors | stat | failed exports per second: spans, logs and metrics added together | not offered | **Not offered.** Adds three different metrics. |
| 7 | Items in-flight | stat | items waiting to be exported: three gauges added together | not offered | **Not offered.** Adds three different metrics. |
| 69 | Processing errors (spans + LogRecords) | stat | items dropped by the processors' queues, two metrics added | not offered | **Not offered.** Adds two different metrics. |
| 21 | Spans started rate | timeseries | spans started per second, one line per service | `metric_series` | **Partly.** One line per service becomes one combined line, or one panel per service. |
| 22 | Live spans | timeseries | spans started but not yet ended, one line per service | `metric_series` | **Partly.** One line per service. |
| 23 | Span processor queue utilization | timeseries | queue size over queue capacity, per service and component | not offered | **Not offered.** A ratio of two metrics. |
| 24 | Spans processed error rate (by error type) | timeseries | spans dropped by the processor per second, one line per service and error type | `metric_series` | **Partly.** One line per error type becomes one panel per type. |
| 25 | Spans exported error rate (by error type) | timeseries | failed span exports per second, one line per service and error type | `metric_series` | **Partly.** One panel per error type. |
| 26 | Spans in-flight | timeseries | spans passed to an exporter and not yet exported, per service | `metric_series` | **Partly.** One line per service. |
| 66 | Span export operation duration (avg) | timeseries | average time of the exporter's export call: duration sum over duration count | not offered | **Not offered.** Needs a histogram's sum and count; histograms are not offered. |
| 61 | LogRecords created rate | timeseries | log records created per second, one line per service | `metric_series` | **Partly.** One line per service. |
| 62 | LogRecords exported error rate (by error type) | timeseries | failed log exports per second, by service and error type | `metric_series` | **Partly.** One panel per error type. |
| 63 | Log processor queue utilization | timeseries | queue size over queue capacity | not offered | **Not offered.** A ratio of two metrics. |
| 64 | LogRecords processed error rate (by error type) | timeseries | log records dropped by the processor, by service and error type | `metric_series` | **Partly.** One panel per error type. |
| 65 | LogRecords in-flight | timeseries | log records waiting to be exported, per service | `metric_series` | **Partly.** One line per service. |
| 67 | LogRecord export operation duration (avg) | timeseries | average time of the log exporter's export call | not offered | **Not offered.** Needs a histogram's sum and count. |
| 41 | Metric data points exported error rate (by error type) | timeseries | failed metric-point exports per second, by service and error type | `metric_series` | **Partly.** One panel per error type. |
| 42 | Metric data points in-flight | timeseries | metric points waiting to be exported, per service | `metric_series` | **Partly.** One line per service. |
| 43 | Metric collect duration (avg) | timeseries | average time of the metric reader's collection step | not offered | **Not offered.** Needs a histogram's sum and count. |
| 68 | Metric export operation duration (avg) | timeseries | average time of the metric exporter's export call | not offered | **Not offered.** Needs a histogram's sum and count. |
| 8 | SDK components reporting | table | a list of which SDK components (exporters, processors, readers) report, per service | not offered | **Not offered.** A list of distinct series is not a panel kind. |

### Spanmetrics Demo Dashboard

`spanmetrics-dashboard.json`, 8 panels. Panel numbers are the board's own.

| No. | Panel | Visual type | What it shows | F20 panel kind | Fit, and what is lost or why not |
|---|---|---|---|---|---|
| 26 | (no title: description) | text (HTML) | two sentences saying where the numbers come from and a warm-up delay | `note` | **Maps.** Retype the sentences; the HTML line break is dropped. |
| 2 | Top 3x3 - Service Latency - quantile95 | gauge | for the slowest services (top 7): median, 95th, 99th and 99.9th percentile latency as gauges | `top_services_trend` (latency) | **Partly.** Up to 5 lines, not 7; which percentile the lines show is unverified; the 99.9th is not offered; a chart, not gauges. |
| 4 | Top 7 Services Mean Rate over Range | bargauge | the seven busiest services by request rate averaged over the window | `services_table` | **Partly.** A table ranked worst or slowest first, not busiest; up to 20 rows, not 7; no bars. |
| 15 | Top 7 Services Mean ERROR Rate over Range | bargauge | the seven services with the highest error rate over the window | `services_table` (worst) | **Partly.** Ranked by error count first, not error rate; a table, not bars. |
| 22 | Top 7 span_names and Errors (APM Table) | table | the seven busiest operations with request rate and error rate | `endpoints_table` | **Partly.** Ranked worst or slowest first, not busiest; up to 20 rows, not 7. |
| 25 | Top 3x3 - span_name Latency - quantile95 | gauge | the slowest operations: median, 95th, 99th and 99.9th percentile latency | `endpoints_table` (slowest) | **Partly.** A table of percentiles; no 99.9th; no gauges. |
| 10 | Top 7 Highest Endpoint Latencies Mean Over Range | bargauge | the seven operations with the highest mean latency | `endpoints_table` (slowest) | **Partly.** Percentiles, not a mean; a table, not bars. |
| 16 | Top 7 Latencies Over Range | timeseries | mean latency over time for the seven slowest operations, with trace-linked dots | `endpoint_trend` (latency) | **Partly.** One operation per panel; the top-seven lines and the dots are lost. |

## What is lost

Grouped by what a person would notice. "Covered by" says which later F20 step or other feature, if any, would answer it, citing [`plan.md`](../features/dashboards/plan.md) and [`spec.md`](../features/dashboards/spec.md). F20 is not built beyond Step 1, so "covered by" means planned, not working.

| What is lost | Panels | Why | Covered by |
|---|---|---|---|
| **Importing the board itself** | all | A board file holds queries written for another data store. The spec leaves import out: "a half-working import is worse than none" | Nothing planned (spec, *Out of scope*). Plan T11.1 writes a setup page mapping common request, error and machine panels to panel kinds, naming no rival. This report is its raw material |
| **Calculations across metrics**: a ratio such as accepted over (accepted plus refused), a sum of two different metrics, an average made from a histogram's sum and count | 39 not offered (collector 27, SDK self-metrics 10, PostgreSQL 2) | F20 has no query language and no free-form query (spec, *Out of scope*; foundation section 9). A panel reads one metric | Nothing planned. After Step 8 each half of a ratio could be a panel of its own, but not the ratio |
| **Many lines in one chart** (a line per service, state, mode, direction, error type or database) | 32 partly | The spec leaves out "group by any label": it needs SQL built at runtime or a read measured 25 times slower. Its own answer to "can I split by region or status?" is no, except one chosen series per panel, "later, if asked" | Step 8 (T8.2, T8.4): one series per panel, so one chart of five lines becomes five panels. A dashboard holds at most 30 panels and 40 reads (FR7, *guesses*) |
| **The look**: a number, dial or bar becomes a line or a table | 31 partly | Number panels exist only for request measures (`project_number`, `service_number`). There is no dial, no bars and no heatmap | Nothing planned |
| **Machine facts and tables**: load average, uptime, CPU count, swap and paging bytes, host and CPU metadata, a table per network device or mount point | 11 not offered; 9 more partly | `node_trend` draws one named machine metric per node. The names are the list `INFRASTRUCTURE_METRIC_NAMES` in `infrastructure/conventions.py`, which has no load average, uptime, CPU count or paging bytes. No table of a machine's devices | `node_trend` is T4.2 and `top_nodes_trend` is T5.1b; the *Infrastructure* template is T9.1. The console's Infrastructure screen already exists but is not a panel |
| **Logs** | 6 not offered | "Logs on a dashboard? No. Error groups and notes only" (spec, buyer questions table, "Not planned") | The console's Logs screen exists today. `error_groups_table` (T5.2) is the only log-derived panel |
| **A list of traces** | 1 not offered | No panel kind lists traces | The console's Traces screen exists today. Not planned as a panel |
| **Histograms, heatmaps and exemplars** | 4 not offered. 4 more are calculations built on a histogram's sum and count, counted under calculations | Spec D49: "state the gap". A custom latency histogram is not drawn in this version. Exemplars are stored by the workers (`apps/workers/internal/pipeline/metricwriting/rows.go`) but no F20 panel reads them | T8.1 first checks a suspected fault in the existing histogram read, and D49 says a `histogram_quantile_trend` kind "can follow". The nearest panel today, `endpoint_trend` latency, is a different measurement (span durations, not the custom histogram), so these four are counted as not offered |
| **Health of the telemetry pipeline itself** (collector and SDK self-metrics: 74 panels, 52% of the 143) | 43 not offered, 28 partly | Mostly the calculations above. A single counter can be a `metric_series` panel. A receive-versus-refuse comparison cannot | Single counters: Step 8. Comparisons: nothing planned. No F20 panel is aimed at pipeline health |
| **PostgreSQL server internals** (commits, rows, buffers, deadlocks, cache hits, connections) | 2 not offered, 4 partly | Server counters, not queries. F20's database panels (`statement_trend`, `database_statements_table`) are about slow statements, which none of these boards shows | Step 8 for single counters, one per panel |
| **A list of alert rules** | 2 not offered | "Alerts from a panel" is out of scope: it would be a second alert system beside F21 | The "In this window" strip lists alert incidents on every dashboard (FR47), as text, not as a panel |
| **Smaller items**: "latest metrics received" time, process-uptime tables, a list of which SDK components report, an uptime number | 5 not offered | No panel kind shows them | Nothing planned |
| **The service graph** | 1 partly (becomes a table of the busiest dependencies) | No graph panel | The console's Service map screen exists but is not a panel |
| **Board variables** (not panels) | 33 variables on 8 of the 10 boards | 6 choose a service, which the one service picker covers (D7). 8 choose a data source, which nexovue does not need (one store). 2 choose an environment, which nexovue's project selector in the header does instead (*unverified* that the two match). 17 are other filters: namespace, cluster, node, host, instance, database, span name, event name, severity, trace id | Spec, *Out of scope*: general template variables come back "if a second variable is asked for repeatedly". Buyer question Q36 asks about variables directly |

The two largest losses are the calculations (39) and the many-line charts (32). Together they are 71 of the 136 panels that do not map exactly, and they follow the design's deliberate line (no query language, no group by any label), not an omission.

## For the buyer conversations

For when someone asks "could I bring my dashboards over?" This answers question **Q37** ("What would you do with your existing dashboards?"). Its last sentence leads into **Q35** ("Show me the last dashboard you built"), and its second sentence touches **Q36**'s "no variables beyond a service picker".

> Dashboards are still being designed, so what I describe is a plan, not something you can use today. We do not plan to import dashboards from other tools, because those files hold free-form queries that we deliberately do not run, so you would rebuild the boards you use from a fixed list of panel types. We checked that list against ten sample dashboards from an open-source demo, 143 panels in all: if we build everything planned, about half of them (68) would have a close equivalent that loses something, such as one chart of many lines becoming several panels, and only 7 would carry over as they are, six of those being plain text. The other half (68) we do not plan to offer in the first version, mostly calculations between two metrics, logs, and histograms. Those were sample boards, not yours, so tell me which three or four you actually look at and I can tell you which of them we would be able to show.

Every figure in it is from this report: 143, 68, 7, 68, and six of the seven being notes. Notes for Raj:

- It does not say "has dashboards". Plan T11.4 requires a signed checklist before anyone says that.
- It claims nothing from the foundation's *Must not be claimed* table and names no product.
- It says "fixed list", which holds on both paths of founder decision 1. It does not say "build your own", which would be false on the templates-only path.
- The spec's buyer table gives "recreate the few you actually use from templates" as the answer to this question. Do not use that line unchanged: on the templates-only path nothing can be edited, so a buyer's board could not be recreated at all.
- If the buyer shows a board, count it toward F20's rule only if they built it and still use it (Q35).
- These sentences are for speaking. They are not public wording (foundation, founder decision 14).

## Not checked

1. **Nothing was built or drawn.** F20 is at Step 1 (`status.md`). Every "maps" and "partly" is the design's intent read against the files, not a panel that exists.
2. **No numbers were compared.** Whether nexovue's rate, error and latency lines would equal the ones these boards draw was not tried. How nexovue counts an error against a 5xx status was not read.
3. **Table columns and line contents were taken from the design, not the code.** I did not read what columns `services_table`, `endpoints_table` and `dependencies_table` have, which percentile `top_services_trend` draws, whether `node_trend` splits by state or direction (the design describes one row per metric per time bucket, so I assumed it does not), or whether a database shows up as a dependency.
4. **Metric names.** `metric_series` reads a metric by its OpenTelemetry name. These boards use the metric store's converted names (`nginx_connections_accepted_total` and so on). That each metric would reach nexovue under a matching name was not checked. For machine metrics only the names in `INFRASTRUCTURE_METRIC_NAMES` were compared, and whether the demo's collector sends each one was not.
5. **The "close equivalent" count assumes everything planned is built.** 40 of the 75 depend on Step 8, which founder decision 1 allows to be dropped. The caps (30 panels, 40 reads) were not applied to the split panels: turning a chart of many lines into one panel per line could pass those caps on a large board.
6. **The shares depend on this report's three rules** (*Source and method*). A different reading of "partly" moves them; the 13 look-only panels are the clearest swing.
7. **Representativeness.** Ten boards from one demo, built for a demo stack, are not a sample of buyers' boards. The plan, the spec and the buyer guide call them real boards; they are real files, but the demo's own. The right use of the shares is to say what the fixed list can express, not to forecast what a buyer's boards will do.
8. **The pinned tag.** The files came from a clone at commit `1755859a9de82c2e5e225be68abc401a5ebf2b4f`, made by the coordinator. That it is tag `3.0.0` was not re-checked here. A different tag could hold different boards.
9. **Counting.** I counted with the script above and cross-checked each panel's number, title and type against the files with a second script, which found only whitespace differences in three titles. I did not recount by hand. A search of the files' text found no library panels and no repeated panels.
10. **The buyer sentences** have not been read by the founder, and were checked against the foundation's *Must not be claimed* table only.
11. **The plan's counting rule** was not given, so that the plan's 143 used the same rule is an inference from the matching figure.

## Sources

- The ten board files named above, at the clone described in the header.
- [`docs/features/dashboards/spec.md`](../features/dashboards/spec.md): the panel table (FR6), FR7, FR47, *Out of scope*, the buyer questions table, D7, D49, founder decision 1.
- [`docs/features/dashboards/design.md`](../features/dashboards/design.md): section 4, *How one panel becomes a read*.
- [`docs/features/dashboards/plan.md`](../features/dashboards/plan.md): T2.0, Steps 2, 4, 5, 8 and 9, T11.1, T11.4.
- [`docs/features/dashboards/status.md`](../features/dashboards/status.md): F20's lifecycle state.
- [`docs/product/phase-2-foundation.md`](../product/phase-2-foundation.md): section 12 (*Must not be claimed*), founder decisions 14 and 15.
- [`docs/product/phase-2-buyer-round.md`](../product/phase-2-buyer-round.md): Part 7, Q35 to Q37, *Not checked* item 6.
- Code, read 2026-11-08: `apps/api/src/nexovue_api/infrastructure/conventions.py` (machine metric names), `services/queries.py` (what a service trend and the endpoint ranking hold), `servicemap/queries.py` (dependency edges), `apps/web/src/app/routes/route-manifest.ts` (which screens exist), `apps/workers/internal/pipeline/metricwriting/rows.go` (exemplars are stored).
