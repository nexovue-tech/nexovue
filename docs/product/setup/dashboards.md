# Dashboards

A dashboard is a page of panels you choose, kept for your whole organization to read. Findings stay the first
screen; a dashboard is for the numbers a team wants side by side, or on a wall.

**Every limit below is the build's value and has not yet been measured on a customer-sized install.** They are
stated as protection for the system, not as a reason to upgrade. They may change after the measurement
(`docs/research/f20-measurement-2026-11-22.md`).

## What a dashboard holds

- Up to **30 panels**, a document of at most **64 KiB**, and at most **5 links** on the page.
- A panel is one of a **fixed list**, picked in the builder. There is no query language and no free-form SQL,
  on purpose: every read is a statement nexovue wrote, bound to your project, so a dashboard cannot read
  another customer's data or run an expensive query by accident.
- The list: over time, a project, a service, an endpoint, the top services or nodes, a node, a check, a database
  statement, a chosen metric series; tables of services, endpoints, errors, dependencies, database statements and
  web vitals; one number for a project or a service; open findings; a reliability target's budget; and a note.
- A dashboard refreshes at most every **30 seconds**, and one page reads at most **40** separate reads, counted
  at its worst case when you save. A save over a limit says which panel to remove.

## Latency is an estimate

Latency percentiles come from per-minute rollups, which keep a distribution and not every request. They are
close, not exact to the digit, and every latency on a dashboard is marked "(est.)". Request and error counts
are exact: they are counted before any sampling.

## Reading a dashboard

- Marks on charts: releases (a rule where a new version first appeared or a deploy was reported), findings
  (a shaded stretch) and alert incidents. A release says what happened and when, never that it caused anything.
- The page says what window it shows and why, for example when a link set it ("Showing staging from this link.
  You were on production") or after you drag across a chart ("Zoomed to 02:10 to 02:20").
- A panel that cannot read says so in words and the rest of the page still draws. A missing reading is a dash,
  never a zero.

## A wall display

Open a dashboard and choose **Full screen** (address ending `?display=1`). The sidebar and top bar go; the age
of the reading and a "paused" chip stay, and **Leave full screen** appears when the pointer moves or Tab is pressed
(Escape also leaves).

A session lasts seven days, so **a wall screen signs out about weekly.** Use a dedicated **viewer** account for
it: a viewer can read and change nothing. Whether anyone will accept the weekly sign-in is not yet known.

## What a copied link contains

**Copy link** offers two kinds: one that stays live (project and range) and one with exact times (project, start
and end). A link holds the project, the window and the service picked, never your name or any data. It opens
for anyone in the organization who can open that project, and anyone else sees that it is refused.

## Nothing leaves your install

Opening and using a dashboard sends nothing outside the install: no fonts, no analytics, no outside images. A
test holds this for the imports of both packages. There are **no public links** to a dashboard in this version.

## Names and labels can hold personal data

Service names, endpoint names, metric attribute values and notes are shown as they were sent. A hostile or odd
value is shown as literal characters (a right-to-left control shows as `[U+202E]`) and nothing in it runs. If
your telemetry puts personal data in a label, it will show on a dashboard that reads it; nexovue does not
mask labels.

**A note is read by everyone in the organization.** On a hosted install, support can also read it while an owner
has granted access. Do not paste secrets or personal details into a note.

## Purge and backups

Editing a dashboard keeps its earlier versions readable, and deleting keeps it for 30 days. To remove text at
once, an **owner or admin** may **purge** a dashboard or one version: its text is hard-deleted and its title is
replaced by `[removed]` in the audit trail. **Backups are outside this.** A purged note remains in any backup
taken before the purge until that backup expires.

## Mapping what you know

| You are used to | Use |
|---|---|
| A request-rate graph | A service's **rate** over time |
| An error-rate graph | A service's **errors** over time |
| A latency graph with p50 / p95 / p99 | A service's **latency** over time |
| A "top N" panel | The worst or slowest services over time, or the services table |
| A single-stat tile | One number for a service |
| A host CPU or memory graph | A node's line, or a chosen metric series |
| A list of failing things | Open findings, or the errors table |
| A text box | A note |

## Not in this version

No query language, no public links, no dashboards as code, no import of another tool's dashboard files, and no
AI that writes dashboards. A dashboard can be exported and imported as a nexovue file (64 KiB, checked as data
and never run, opened as an unsaved draft).

## Starting points

*Start from Overview*, *Service health* (opened from a service's screen) and *Infrastructure* open as drafts you can
change and save.
