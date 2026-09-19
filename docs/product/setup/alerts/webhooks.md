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
  "version": 1,
  "type": "incident.opened",
  "timestamp": "2026-09-19T14:03:30+00:00",
  "data": {
    "incident": {
      "id": "01a0b65b-…",
      "subject": "Checkout API",
      "severity": "critical",
      "opened_at": "2026-09-19T14:03:00+00:00",
      "resolved_at": null
    },
    "rule": { "name": "Any check down" },
    "summary": "[critical] Checkout API is down",
    "details": ["Checkout API is down.", "Why: expected status code less than 400, got 503."],
    "link": "https://app.nexovue.com/synthetics/…"
  }
}
```

`type` is one of `incident.opened`, `incident.escalated` (it got worse),
`incident.repeated` (still open, if the rule repeats), `incident.flapping`,
`incident.resolved` (it recovered) and `incident.closed` (it had not recovered, but
nothing watches it any more — its rule or check was removed, or the rule stopped
covering it; close your own record too). `data.incident.id` is the same for everything about one
incident — key your own record on it.

`version` changes only when a field changes meaning or goes away. New fields may
appear in version 1; ignore the ones you do not read.

## Answering

Answer with any `2xx` within ten seconds. A timeout, a network error, `429` or a
`5xx` is retried with backoff for up to an hour, with the same `webhook-id`. Any
other status is final and recorded, with the first 200 characters of your
response body, in the channel's delivery log. Redirects are not followed.
