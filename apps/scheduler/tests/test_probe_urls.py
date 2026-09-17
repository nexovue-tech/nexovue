"""What a probe records about a URL.

The probe keeps the query and the fragment where the browser SDK trims them,
because a run's evidence reports where the request *ended up* and a redirect to
a login page is the diagnosis. The two rules that hold unconditionally are the
ones asserted here: credentials never leave, and the conventions' sensitive
parameters are redacted.
"""

from __future__ import annotations

import pytest

from nexovue_scheduler.probes.urls import SENSITIVE_QUERY_PARAMETERS, reportable_url


def test_credentials_in_a_url_are_removed() -> None:
    """A MUST in the conventions, and a probe target is typed into a form — which
    is exactly where `https://user:password@host/` gets typed."""
    recorded = reportable_url("https://admin:hunter2@shop.test/orders?page=2")

    assert recorded == "https://shop.test/orders?page=2"
    assert "hunter2" not in (recorded or "")


def test_the_redirect_that_a_check_failed_on_is_kept_whole() -> None:
    """Trimming this to a path would delete the reason the record exists."""
    assert (
        reportable_url("https://shop.test/login?next=%2Fcheckout&reason=expired#form")
        == "https://shop.test/login?next=%2Fcheckout&reason=expired#form"
    )


@pytest.mark.parametrize("name", SENSITIVE_QUERY_PARAMETERS)
def test_every_sensitive_parameter_keeps_its_key_and_loses_its_value(name: str) -> None:
    recorded = reportable_url(f"https://shop.test/d?file=a.pdf&{name}=deadbeef") or ""

    assert f"{name}=REDACTED" in recorded
    assert "deadbeef" not in recorded
    assert "file=a.pdf" in recorded


def test_the_names_are_matched_case_sensitively_as_the_conventions_specify() -> None:
    assert reportable_url("https://shop.test/d?SIG=abc123") == "https://shop.test/d?SIG=abc123"


def test_a_url_needing_no_change_is_returned_exactly_as_it_arrived() -> None:
    """Re-encoding a customer's own URL makes it unrecognisable to them for no gain."""
    original = "https://shop.test/search?q=running%20shoes&page=2"

    assert reportable_url(original) == original


def test_an_absent_url_stays_absent_rather_than_becoming_an_empty_string() -> None:
    """An empty `url.full` reads as "the request went nowhere", which is a
    different claim from not knowing."""
    assert reportable_url(None) is None
    assert reportable_url("") == ""
