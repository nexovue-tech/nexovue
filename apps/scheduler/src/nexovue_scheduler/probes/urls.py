"""What a probe is allowed to record about a URL it visited.

The browser SDK trims a page URL to its path by default, because the visitor
never chose to send us their query string. **A probe is the other
case**: the URL is one the customer configured us to call, and a run's evidence
reports where the request actually ended up — a redirect to a login page is
exactly the thing worth seeing. Trimming that to a path deletes the diagnosis
the record exists for, so the query and the fragment stay.

Two rules stay unconditional, and neither was applied before:

- **Credentials in the URL are removed.** The conventions make this a MUST for
  `url.full`, and a probe target is typed by hand into a form, which is exactly
  where `https://user:password@host/` gets typed.
- **The conventions' sensitive parameters are redacted**, key kept, value
  replaced — the presigned-URL signatures, matched case-sensitively as specified.

`SENSITIVE_QUERY_PARAMETERS` is restated from the browser SDK's
`pagecontext.ts`, because TypeScript and Python cannot import each other, and
`test_cross_service_contracts.py` asserts the two lists equal.
"""

from __future__ import annotations

from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE_QUERY_PARAMETERS: Final = (
    "X-Amz-Credential",
    "X-Amz-Security-Token",
    "X-Amz-Signature",
    "X-Goog-Signature",
    "sig",
)

REDACTED: Final = "REDACTED"


def reportable_url(url: str | None) -> str | None:
    """The URL with its credentials removed and its signatures redacted.

    Returns the input unchanged when it cannot be parsed: a probe records where
    it was, and a URL we failed to understand is still the most useful thing we
    know. `None` stays `None` — absent is not the empty string, which would read
    as "the request went nowhere".
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url

    netloc = parts.netloc.rsplit("@", 1)[-1] if "@" in parts.netloc else parts.netloc
    return urlunsplit(
        (parts.scheme, netloc, parts.path, _redacted_query(parts.query), parts.fragment)
    )


def _redacted_query(query: str) -> str:
    """Replaces the value of every sensitive parameter, and leaves a query that
    needs no change exactly as it arrived — re-encoding one for nothing would
    make a customer's URL unrecognisable to them."""
    if not query:
        return query
    pairs = parse_qsl(query, keep_blank_values=True)
    if not any(name in SENSITIVE_QUERY_PARAMETERS for name, _ in pairs):
        return query
    return urlencode(
        [(name, REDACTED if name in SENSITIVE_QUERY_PARAMETERS else value) for name, value in pairs]
    )
