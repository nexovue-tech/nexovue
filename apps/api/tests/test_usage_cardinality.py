"""What the cardinality detector accuses, and — mostly — what it does not.

The billing unit is distinct service names, so a name carrying a pod id bills
once per pod. Saying so is useful; saying so wrongly is worse than saying
nothing, because a customer who is told their naming is broken when it is not
stops reading the warning. Most of this file is therefore negative: honest
names, one at a time and in families, that must produce silence.

Nothing here caps anything. The product refuses to drop telemetry to hold a
bill down.
"""

from __future__ import annotations

import uuid
from typing import Final

from nexovue_api.usage import cardinality
from nexovue_api.usage.cardinality import ProjectServiceCounts

PROJECT: Final = uuid.uuid4()


def _counts(recent: int, previous: int) -> ProjectServiceCounts:
    return ProjectServiceCounts(project_id=PROJECT, recent=recent, previous=previous)


def _stems(names: list[str]) -> list[tuple[str, str, int]]:
    """The families found, as (stem, kind, size) — the shape assertions read."""
    return [
        (family.stem, family.kind, len(family.members))
        for family in cardinality.identifier_families(names)
    ]


# ------------------------------------------------------------------- the jump


def test_a_doubled_count_inside_a_week_is_a_jump() -> None:
    assert cardinality.has_jumped(_counts(recent=12, previous=3))


def test_ordinary_growth_is_not_a_jump() -> None:
    """Two services added to nine is a team shipping, not a bill inflating."""
    assert not cardinality.has_jumped(_counts(recent=11, previous=9))


def test_a_small_project_never_jumps_however_it_moved() -> None:
    """One service to four is a customer starting, and the factor is meaningless."""
    assert not cardinality.has_jumped(_counts(recent=4, previous=1))


def test_a_project_with_no_previous_week_has_not_jumped() -> None:
    """ "Ten, up from none" is onboarding. Pod ids there are caught by the names."""
    assert not cardinality.has_jumped(_counts(recent=10, previous=0))


def test_only_projects_big_enough_to_be_the_cause_have_their_names_read() -> None:
    assert cardinality.worth_examining(_counts(recent=8, previous=8))
    assert not cardinality.worth_examining(_counts(recent=7, previous=1))


# ------------------------------------------------------- what is a family, and what is not


def test_kubernetes_pod_names_are_one_family_under_the_deployment() -> None:
    """`<deployment>-<replicaset hash>-<pod suffix>`, the shape kubectl shows."""
    pods = [
        "checkout-7d9f8b6c4d-x2k9p",
        "checkout-7d9f8b6c4d-m4rt7",
        "checkout-7d9f8b6c4d-bn6qw",
    ]

    assert _stems(pods) == [("checkout", cardinality.POD_SUFFIX, 3)]


def test_pods_from_two_replica_sets_are_still_one_deployment() -> None:
    """A redeploy changes the hash; the customer still runs one service."""
    pods = [
        "checkout-7d9f8b6c4d-x2k9p",
        "checkout-7d9f8b6c4d-m4rt7",
        "checkout-59fbc7d4kk-bn6qw",
    ]

    assert _stems(pods) == [("checkout", cardinality.POD_SUFFIX, 3)]


def test_a_trailing_uuid_is_a_family() -> None:
    """Matched whole: its own hyphens would otherwise split every name apart."""
    instances = [
        "worker-3f2a1b4c-5d6e-4f80-91a2-b3c4d5e6f708",
        "worker-a1b2c3d4-e5f6-4a8b-9c0d-1e2f3a4b5c6d",
        "worker-0f1e2d3c-4b5a-4968-8776-655443322110",
    ]

    assert _stems(instances) == [("worker", cardinality.UUID_SUFFIX, 3)]


def test_a_trailing_hex_run_is_a_family() -> None:
    """A container id or an abbreviated commit sha, appended to a real name."""
    builds = ["api-9f8e7d6c5b4a", "api-1122334455aa", "api-ffeeddccbbaa"]

    assert _stems(builds) == [("api", cardinality.HEX_SUFFIX, 3)]


def test_stateful_set_ordinals_are_a_family() -> None:
    ordinals = ["kafka-0", "kafka-1", "kafka-2"]

    assert _stems(ordinals) == [("kafka", cardinality.ORDINAL_SUFFIX, 3)]


def test_an_underscore_or_a_dot_joins_an_id_just_as_a_hyphen_does() -> None:
    joined = ["ingest_9f8e7d6c5b4a", "ingest_1122334455aa", "ingest_ffeeddccbbaa"]

    assert _stems(joined) == [("ingest", cardinality.HEX_SUFFIX, 3)]


# ------------------------------------------------------------ what must stay silent


def test_two_names_are_never_a_family() -> None:
    """Three, because two sharing a stem happens for honest reasons."""
    assert _stems(["kafka-0", "kafka-1"]) == []


def test_one_odd_name_is_never_accused() -> None:
    """A service actually called `checkout-2` is a service called `checkout-2`."""
    assert _stems(["checkout-2", "payments", "search"]) == []


def test_ordinary_service_names_produce_nothing() -> None:
    honest = [
        "checkout",
        "payments",
        "api-gateway",
        "web-frontend",
        "auth-service",
        "email-worker",
        "recommendation-engine",
        "order-processor",
        "inventory-sync",
        "search-indexer",
    ]

    assert _stems(honest) == []


def test_versioned_names_are_not_ordinals() -> None:
    """`v2` is not a number, and these three are three real services."""
    assert _stems(["api-v1", "api-v2", "api-v3"]) == []


def test_a_five_letter_word_is_not_a_pod_suffix() -> None:
    """The generator's alphabet has no vowels, so a word cannot be spelled in it."""
    words = ["order-queue-cache", "order-queue-store", "order-queue-index"]

    assert _stems(words) == []


def test_a_short_hex_looking_word_is_not_an_identifier() -> None:
    """Seven characters is under the floor, and `decade` is a word."""
    assert _stems(["build-face", "build-cafe", "build-decade"]) == []


def test_a_year_in_a_name_is_not_an_ordinal_family() -> None:
    """Three different years under one stem would be, and these are not."""
    assert _stems(["report-2024", "report-2025"]) == []


def test_a_name_that_is_only_an_identifier_names_no_service() -> None:
    """There is no stem to tell the customer to go and fix."""
    assert _stems(["9f8e7d6c5b4a", "1122334455aa", "ffeeddccbbaa"]) == []


def test_a_family_needs_the_same_stem() -> None:
    """Three pod names from three deployments are three services, correctly."""
    unrelated = [
        "checkout-7d9f8b6c4d-x2k9p",
        "payments-59fbc7d4kk-m4rt7",
        "search-6b8c9d2f4g-bn6qw",
    ]

    assert _stems(unrelated) == []


def test_nothing_at_all_is_the_usual_answer() -> None:
    assert cardinality.identifier_families([]) == []


# ------------------------------------------------------------------- the report


def test_the_largest_family_is_reported_first() -> None:
    """A screen shows the first one, so it has to be the one that costs most."""
    names = [
        *[f"checkout-7d9f8b6c4d-{suffix}" for suffix in ("x2k9p", "m4rt7", "bn6qw", "kt9zx")],
        *[f"payments-59fbc7d4kk-{suffix}" for suffix in ("vd4sr", "hj7bk", "cz2mn")],
    ]

    assert [stem for stem, _, _ in _stems(names)] == ["checkout", "payments"]


def test_only_a_few_names_are_quoted_back() -> None:
    """Enough to recognise the pattern; not the whole explosion."""
    names = [f"checkout-7d9f8b6c4d-{suffix}" for suffix in ("x2k9p", "m4rt7", "bn6qw", "kt9zx")]
    family = cardinality.identifier_families(names)[0]

    assert len(cardinality.examples_of(family)) == cardinality.MAX_EXAMPLES
    assert set(cardinality.examples_of(family)) <= set(names)
