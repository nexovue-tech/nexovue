"""The one table of who may do what (F26, FR1), judged row by row.

The table is the specification written as code; this restates the
specification's FR1 so a change to either is visible in the other.
"""

from __future__ import annotations

from typing import Any

import pytest

from nexovue_api.auth.permissions import ALLOWED, Action, may, permissions_of
from nexovue_api.auth.policy import refusal, require
from nexovue_api.data.postgres.models import USER_ROLES
from nexovue_api.errors.exceptions import PermissionDeniedError

# FR1, one column per role, in the specification's words.
EVERYONE_WHO_WORKS = {"owner", "admin", "member"}
ADMINISTRATORS = {"owner", "admin"}
OWNERS = {"owner"}
FR1 = {
    Action.OPERATE_CHECKS: EVERYONE_WHO_WORKS,
    Action.OPERATE_ALERTS: EVERYONE_WHO_WORKS,
    Action.UPLOAD_SOURCE_MAPS: EVERYONE_WHO_WORKS,
    Action.MANAGE_CHANNELS: ADMINISTRATORS,
    Action.MANAGE_CHECK_SECRETS: ADMINISTRATORS,
    Action.CONFIGURE_PROJECTS: ADMINISTRATORS,
    Action.MANAGE_CREDENTIALS: ADMINISTRATORS,
    Action.MANAGE_MEMBERS: ADMINISTRATORS,
    Action.READ_AUDIT: ADMINISTRATORS,
    Action.MANAGE_OWNERS: OWNERS,
    Action.GRANT_SUPPORT: OWNERS,
    Action.COMMIT_SPEND: OWNERS,
    Action.END_ORGANIZATION: OWNERS,
}


@pytest.mark.parametrize("role", USER_ROLES)
@pytest.mark.parametrize("action", list(Action))
def test_each_role_may_do_exactly_what_fr1_says(action: Action, role: str) -> None:
    assert may(role, action) == (role in FR1[action])


def test_the_table_and_the_specification_name_the_same_actions() -> None:
    assert set(ALLOWED) == set(FR1) == set(Action)


def test_a_viewer_may_do_nothing() -> None:
    assert permissions_of("viewer") == []


def test_an_owner_may_do_everything() -> None:
    assert permissions_of("owner") == list(Action)


class _Person:
    def __init__(self, role: str) -> None:
        self.role = role


class _Identity:
    def __init__(self, role: str) -> None:
        self.user = _Person(role)


def test_the_gate_refuses_a_role_the_table_does_not_allow_and_names_who_may() -> None:
    guard = require(Action.MANAGE_CHANNELS)
    refused = r"^Only an admin or an owner can change this\.$"
    with pytest.raises(PermissionDeniedError, match=refused):
        guard(_Identity("member"))  # type: ignore[arg-type]


def test_the_gate_admits_a_role_the_table_allows() -> None:
    identity: Any = _Identity("member")
    assert require(Action.OPERATE_CHECKS)(identity) is identity


def test_an_owner_only_refusal_names_the_owner() -> None:
    assert refusal(Action.END_ORGANIZATION) == "Only an owner can change this."
