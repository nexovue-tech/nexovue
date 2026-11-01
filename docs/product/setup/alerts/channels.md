# Alert channels

A channel is where alerts go. Channels belong to your organization, and any
project's rules can send to them. Connect them under **Settings → Alert channels**,
or from a rule while you write it.

Every organization starts with one channel, **Organization owners**, and every
project with two rules sending to it: **Any check down**, and **Critical problems**,
which sends every critical finding anomaly detection opens. So the first check that
goes down, and the first critical finding, email you without any setup. Only an owner
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

PagerDuty and webhooks are read by machines, so they get one event per incident or
finding, never grouped or held. In PagerDuty each incident and each finding is one
alert, keyed `incident:<id>` or `finding:<id>`.

One problem pages once, whichever fires first. A finding and a threshold or *went
silent* incident on the same service are linked. If one of them has already paged a
PagerDuty integration, the other is added to that alert's log, under its key, instead
of paging again. The alert is resolved when the last of them ends. When one ends
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
