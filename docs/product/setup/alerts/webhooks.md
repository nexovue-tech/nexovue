# Receiving webhooks

A webhook channel sends one JSON `POST` for every notification, signed so you can
prove it came from nexovue. The signing follows [Standard Webhooks](https://www.standardwebhooks.com/),
so any of its libraries can verify it.

## Headers

| Header | Value |
|---|---|
| `webhook-id` | The delivery's id. **The same on every retry of one delivery** — store it and ignore a repeat |
| `webhook-timestamp` | When this attempt was sent, in Unix seconds |
| `webhook-signature` | `v1,` then the base64 HMAC-SHA256 of `{webhook-id}.{webhook-timestamp}.{body}` |

The signing secret is shown once, when you create the channel, and looks like
`whsec_…`. The key is the base64 text after `whsec_`, decoded. If you lose it,
replace it on the channel's page; the old one stops working at once.

## Verifying a request

```python
import base64, hashlib, hmac


def verify(secret: str, headers: dict[str, str], body: bytes) -> bool:
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed = f"{headers['webhook-id']}.{headers['webhook-timestamp']}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(f"v1,{expected}", headers["webhook-signature"])
```

```javascript
import { createHmac, timingSafeEqual } from "node:crypto";

export function verify(secret, headers, body) {
  const key = Buffer.from(secret.replace(/^whsec_/, ""), "base64");
  const signed = `${headers["webhook-id"]}.${headers["webhook-timestamp"]}.${body}`;
  const expected = `v1,${createHmac("sha256", key).update(signed).digest("base64")}`;
  const given = Buffer.from(headers["webhook-signature"]);
  return given.length === expected.length && timingSafeEqual(given, Buffer.from(expected));
}
```

Verify against the raw body exactly as received, before parsing it. Reject a
timestamp more than five minutes from your clock to refuse a replayed request.

## The body

```json
{
  "version": 2,
  "type": "finding.opened",
  "timestamp": "2026-09-19T14:03:30+00:00",
  "data": {
    "subject": {
      "kind": "finding",
      "id": "01a0b65b-…",
      "label": "checkout",
      "severity": "critical",
      "opened_at": "2026-09-19T14:02:00+00:00",
      "resolved_at": null
    },
    "rule": { "name": "Critical problems" },
    "summary": "[critical] checkout has been up to 4× slower than normal since 14:02 UTC",
    "details": [
      "checkout has been up to 4× slower than normal since 14:02 UTC.",
      "Slower: 1,200 ms at worst, against 300 ms normally (Fridays around 14:00, last 3 weeks).",
      "Started: 2026-09-19 14:02 UTC.",
      "Rule: Critical problems."
    ],
    "link": "https://app.nexovue.com/findings/01a0b65b-…"
  }
}
```

`type` is the subject's kind, a dot, and what happened. `data.subject.kind` is the
same kind, and `data.subject.id` is the same for everything about one subject — key
your own record on the pair.

**An incident** is what an alert rule opens about a check or a service: a check down,
an error rate or latency over its threshold, a service gone silent, a new error.
Its events are `incident.opened`, `incident.escalated` (it got worse),
`incident.repeated` (still open, if the rule repeats), `incident.flapping`,
`incident.resolved` (it recovered) and `incident.closed` (it had not recovered, but
nothing watches it any more — its rule or check was removed, or the rule stopped
covering it; close your own record too).

**A reliability target's burn** is an incident too, opened when a target's allowed
failures run out too fast. `data.subject.label` is nexovue's own description of the
target, such as `checkout availability, 99.9% over 28 days`, never the name a person
gave it. `details` carries its current figures: how many times faster than allowed,
how long what is left lasts, what is still allowed, and the target. Its
`incident.closed` also comes when its source sent nothing for 24 hours, so it can no
longer be judged; close your own record too. A target that runs out entirely, and an
exclusion, each arrive as an `incident.opened` that is already resolved. When a
project's targets stop being judged at all, one incident says so, resolved when
judging returns.

A webhook receives every event, always. A channel people read may hold back a message
that repeats news it already had, and a service's snooze may hold back a burn's
messages to people ([Alert channels](channels.md#keeping-it-quiet)); neither ever
touches a webhook.

**A finding** is what anomaly detection opens when a service, or a whole project,
stops behaving as it normally does. Its events are `finding.opened`,
`finding.escalated` (it became critical), `finding.joined` (another signal or
endpoint joined it), `finding.flapping` (findings on this service keep opening;
nothing more is sent about it until it has stayed normal for 30 minutes) and
`finding.resolved` (it closed; `details` says how — recovered, marked a false alarm,
a new normal set by a person or adopted after 7 days with no answer, or the service
gone). A channel hears `finding.resolved` only for a finding it was told about.

A webhook never receives the follow-up a channel people read can get when a signed
deploy is a finding's likely place to look (*explained*; see [Alert channels](channels.md#the-follow-up-naming-a-likely-place-to-look)).
There is no `finding.explained` event: the likely cause is on the finding's page, and the
link in `finding.opened` leads there. PagerDuty does not receive it either.

The *Send test* button sends `test.opened`, with `data.subject.kind` `test`.

`data.rule` is the rule that routed the message, or `null` when none did.

### Treat names as untrusted text

`data.subject.label`, `summary` and `details` can contain a service or endpoint
name, and those names come from the telemetry your applications send — which
anyone holding one of your browser keys can send too. Treat them as untrusted
text: escape them before showing them in HTML or chat markup, and never follow a
link or run a command found in them. nexovue flattens each name to one line of
at most 200 characters before it is used. The other fields are written by nexovue.

`version` changes only when a field changes meaning or goes away. New fields may
appear in version 2; ignore the ones you do not read. Version 1 put the incident
under `data.incident` and had no findings; nothing sends it any more.

## Answering

Answer with any `2xx` within ten seconds. A timeout, a network error, `429` or a
`5xx` is retried with backoff for up to an hour, with the same `webhook-id`. Any
other status is final and recorded, with the first 200 characters of your
response body, in the channel's delivery log. Redirects are not followed.
