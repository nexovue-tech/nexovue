# Alert channels

A channel is where alerts go. Channels belong to your organization, and any
project's rules can send to them. Connect them under **Settings → Alert channels**,
or from a rule while you write it.

Every organization starts with one channel, **Organization owners**, and every
project with three rules sending to it: **Any check down**; **Critical problems**,
which sends every critical finding anomaly detection opens; and **Reliability
targets**, which sends the warnings and criticals of every reliability target's
allowed failures running out too fast, and stays silent until you set a target. So the
first check that goes down, and the first critical finding, email you without any setup. Only an owner
or an admin can change or delete an anomaly-finding rule. Owners are emailed at their verified
address only — verify yours from the link in your sign-up email.

## Connecting each kind

| Kind | What to paste | Where to find it |
|---|---|---|
| Slack | The incoming webhook URL | Create a Slack app with **Incoming Webhooks**, add one to the channel, copy its URL |
| Microsoft Teams | The Workflows webhook URL | In the channel, add the Workflows template *Post to a channel when a webhook request is received*, copy its URL |
| Discord | The webhook URL | **Server settings → Integrations → Webhooks → New webhook → Copy webhook URL** |
| PagerDuty | The integration key, and your region (US or EU) | On a service: **Integrations → Add integration → Events API V2**, copy the Integration Key |
| Webhook | Your endpoint's `https://` URL | See [Receiving webhooks](webhooks.md). nexovue generates the signing secret and shows it once |
| Email | One or more addresses | — |

A webhook URL for Slack, Teams or Discord *is* a password: anyone holding it can
post to your channel. nexovue stores it encrypted, shows only its host, and never
returns it. To change it, paste a new one.

## Before you rely on it: send a test

Every channel's page has **Send test**. It sends a message labelled as a test
through exactly the path a real alert takes, and tells you what happened — or why
it did not, in the receiver's own words. A PagerDuty test is resolved the moment
it is triggered, so nobody stays paged.

## What a message says

The rule's name, how bad it is, what happened, why, when it started, how long it
lasted once it is over, and one link to the incident's page in nexovue. That page
says what happened and who was told, and links on to the evidence — the check and
its runs, the service, or the error — for as long as it exists: a message about a
check you have since deleted still opens the incident. Never a request or response
body, a header, a secret, or a stack trace: those stay behind the link.

## Keeping it quiet

- **Grouping.** When several checks fail within half a minute of each other, a
  channel gets one message listing them. Each still has its own incident.
- **At most ten a minute.** Past that, the rest of the minute arrives as one
  summary.
- **Flapping.** A check that goes down more than four times in an hour is said to
  be flapping, once; after that you hear nothing about it until it has stayed up
  for 30 minutes.
- **Quiet hours** (email, Slack, Teams, Discord). Warnings raised during them wait
  and arrive together when they end. Critical alerts are never held.
- **Mute** a channel or a rule for an hour, four hours, or until 09:00 tomorrow.
  What fires during a mute is recorded on the Alerts screen and never sent.
- **A reliability target and the problem beside it** (email, owners, Slack, Teams,
  Discord). A target's allowed failures running out too fast is often the same news
  as a finding or a threshold incident on its service. When a channel was told one of
  them within the last hour, at the same or a higher severity, and it is still open,
  the other is not sent there: it is in the channel's **Delivery log** as *not sent*,
  with why. This only happens when a reliability target is one of the two; a finding
  and a threshold incident with no target involved still send two messages. What
  counts as the same news: a target and a finding linked to it that is failing (for
  availability) or slower (for latency); a target and an error-rate or p95 incident on
  its service; two targets of the same kind on one service, unless each watches a
  different endpoint; one target under two rules; and a check's target beside that
  check's own incident. A service going silent, a check going down, a finding about
  traffic alone, and the *Nothing left* and exclusion notices are never the same news.
  - A higher severity always goes. A channel told about a problem is always told it
    ended, and is never told an ending of news it never heard.
  - When the one that was told ends, or its message fails for good, or it is a target
    that has not been judged for 15 minutes, while the other is still open, the other
    is sent then, once, in the same message as the ending where there is one.
  - Nothing waits to see whether another alert arrives: the decision is made as each
    message is sent.
- **Snooze or mute a service** (from its page or a finding). A snooze also holds back
  its reliability targets' messages to people, and their new alerts in PagerDuty; the
  target still counts its failures, and a PagerDuty alert already open is still
  resolved. When the snooze ends and the target is still running out, one message
  goes; if it ended during the snooze, a channel told it began is told it ended. A
  mute stops findings only: reliability target alerts continue, because a mute can
  last 30 days. To silence a target, mute its rule or turn the target off.

PagerDuty and webhooks are read by machines, so they get one event per incident or
finding, never grouped or held. In PagerDuty each incident and each finding is one
alert, keyed `incident:<id>` or `finding:<id>`.

One problem pages once, whichever fires first. A finding and a threshold or *went
silent* incident on the same service are linked. If one of them has already paged a
PagerDuty integration, the other is added to that alert's log, under its key, instead
of paging again. A reliability target's alert shares the same way: with a finding
linked to it, with another target or an error-rate or p95 incident on its service,
and, for a check's target, with that check's alert, as long as that alert was sent at
the same or a higher severity, so a critical after a warning still pages. A service
going silent and a check going down always page on their own. The alert is resolved when the last of them ends. When one ends
while the other is still open, nothing is sent: a resolve would close the alert the
open one is still on, and its next update would page again. The delivery log shows
that message as not sent, with the reason.

## When a channel stops working

A failed delivery is retried for up to an hour — on a timeout, a network error, a
`429` (honouring `Retry-After`) or a `5xx`. Any other refusal is final. Every attempt
is in the channel's **Delivery log**. A channel that has delivered nothing for an
hour is marked **failing**, and the organization's owners are emailed once — by
email, never through the broken channel. The next successful delivery makes it
healthy again.
