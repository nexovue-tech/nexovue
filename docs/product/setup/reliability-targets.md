# Reliability targets (SLOs)

A reliability target says how much of your traffic may fail over a stretch of time. nexovue counts real
requests against it, shows how much failure is still allowed (the error budget), tells you when it is being
used up too fast, and keeps a figure for each past month. The page is **Targets** in the sidebar.

**Every limit below is the build's value and has not yet been measured on a customer-sized install.** They
protect the system and are not a reason to upgrade. They may change once they are measured.

## What a target counts

You choose a kind, a source, an objective and a window.

| Kind | Counts as good | Reads |
|---|---|---|
| Service availability | requests that did not fail | the service's per-minute counts |
| Service latency | requests at or under a threshold | the service's latency buckets |
| Endpoint availability | requests to one route that did not fail | that route's per-minute counts |
| Check availability | confirmed runs of a check that were up | the check's runs, counted as the Synthetics screen counts them |

- **The objective** is between 90% and 99.99%, to at most two decimals. A finer one is refused: at 99.999%
  over 28 days one short outage spends all of it, and the counts are by the minute.
- **The window** is 7 or 28 whole days in your project's time zone. It is rolling by whole days, so the figure
  steps at midnight.
- **A failure** is a request whose span status is ERROR. A 4xx usually does not count.
- **A latency threshold** is one of 63 fixed boundaries, from about 1.2 ms to about 50.5 s. You type any
  number and the screen says which boundary it counts: a typed 500 ms counts requests at or under 487.2 ms.
  A fast failure counts as good on a latency target, so pair it with an availability target.
- **A project holds at most 200 targets.**
- **Suggestions.** The form reads the last four 7-day windows and shows the worst, typical and best, then
  suggests from them. The arithmetic is on screen and you can change it. No model is involved.

## What a target does not measure

Read this before quoting a figure.

- **Requests that never reached a service.** A request rejected by a load balancer or gateway before your
  service saw it is not counted. Add a check for that.
- **Requests you sample before sending.** If you sample before the data reaches nexovue, the counts are of
  your sample. The form asks, and a target created with "yes" starts with alerts off.
- **Browser data.** Spans sent with a browser key count in no figure here.
- **Services past the cap.** An organization counts at most 2,000 service names a minute, keeping the busiest.
  A service left out shows as *not counted for N minutes*, never as quiet.
- **Time nexovue may have lost.** The screen tells *quiet* (no requests came) from *may have lost* (nexovue's
  own intake failed). Neither is counted as good.
- **Planned work, unless you leave it out.** An exclusion removes a stretch of up to 7 days, visibly: everyone
  gets a notice and the reason shows. It is visible, not preventable.
- **A service that stops completely is not a burn.** No requests means nothing is failing. Add a silence rule
  or a check.
- **Health checks and background jobs** count as good requests on a service target unless you leave
  them out; the form leaves `GET /health` out of availability targets to begin with.

These figures are measurements of requests nexovue received. They are not a contract figure, and nexovue does
not do SLAs. Share one only with its label, and ask your lawyer before promising anything on it.

## What you read

- **Failures still allowed**, for example "360 of 672", how fast it is going, and since when.
- **State names:** *On track*, *Running out*, *Nothing left*, *Counting*, *Building history*,
  *Too little traffic*, *No recent requests*, *Not measured*, *Source gone*, *Not up to date* and *Disabled*.
  *Running out* means failures are using the allowance faster than the
  window can afford, so it would be gone before the window ends.
- **Months.** Each past month keeps one figure, with what it covered and what it left out, for up to 13 months
  on every plan and on a self-hosted install. A service target reaches back about a month from the day you set
  it, and the rest builds up forward, so the first month may be partial.
- **Every time** is in the project's time zone, with the date when it is not today.

## Where alerts go

- **Default routing.** Every project has a rule called *Reliability targets* that covers every target and
  sends to the organization's owners. Nothing needs setting for a burn to reach someone, and the form shows
  where a target's alerts will go, or that they go nowhere.
- **The form's channels.** Ticking channels on the form writes that target's own rule, warnings and criticals,
  or criticals only.
- **Burns are judged every minute on three fixed pairs of windows.** The two fastest are critical and the slowest is a
  warning. A burn needs enough events to judge: 20 requests for a request kind, and for a check, a
  check that runs often enough (the form says what its interval allows).
- **One message per problem, within limits.** A burn joins a finding already open on the same service, and
  on a channel a person reads, a second message about the same news is not sent when that channel was
  told within the last hour at the same or a higher severity. Each message held back stays in the channel's
  delivery log with its reason, and goes out later if nothing else is holding it. This holds for burns only.
  **Setting a target adds alerts**, and a burn with no finding sends its own. The warning that the
  allowance has run out and the notice of an exclusion are never held back. Webhooks are never held back.
- **Snoozing** a service holds its burn messages and does not stop failures counting.
- **When nexovue goes blind.** If a project's targets have not been judged for 15 minutes, one critical message
  goes to the channels its rules name, or to the owners, and one when judging returns.

## What leaves the server

A message carries a label nexovue composes: the service, route or check name, the objective and the window,
such as "payment availability, 99.9% over 28 days". Those names come from your telemetry or your checks. The
**name and description you type for a target never leave the server**: not in a message, an incident, an
audit entry or a webhook. They are shown to people in your organization. Anyone you give a read token can read
targets and their budgets through the API, and the MCP server marks every name in them as untrusted text.

## Moving to or from another tool

There is no import and no export of targets, and API tokens only read. A target cannot be defined in code in
this version; tell us if that decides it for you. If you run Prometheus with Sloth or Pyrra, you can keep them:
nexovue's figures come from its own rollups and do not read your Prometheus.

## An outside heartbeat

If nexovue itself is down, nothing inside it can tell you, and a burn cannot reach you. Run a monitor outside
the install that requests your console's address every few minutes and tells you when it stops answering. Any
outside monitoring service does this. This advice has not been tried against a real install.

## Not measured yet

How much work judging costs at a given number of projects, and how quickly a new target shows its first
figure, were measured only on a development machine, labelled as such in the feature's status. The figures
for a deployed server are owed after the first deployment.
