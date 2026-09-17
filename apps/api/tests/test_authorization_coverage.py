"""Every authenticated route that changes something names a policy.

`auth/policy.py` exists so authorization is not retrofitted into 47 routes after
a phase of feature work. That only holds if a route added next month is covered
the day it is written, which is what this checks.

Structural, in the shape `test_tenant_chokepoint_structure.py` established: it
parses every router and judges the annotation, so a new route is judged the
moment it exists rather than when someone remembers. It cannot tell a *correct*
policy from a wrong one — it catches the mistake that actually happens, which is
a mutating route taking plain `CurrentIdentity` because that is what the route
above it took.

**A POST is not automatically a mutation**, which is why `EXEMPT` exists and why
every entry in it carries its reason. Adding a name there is a decision.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

from nexovue_api import auth

API_SOURCE: Final = Path(auth.__file__).resolve().parents[1]
ROUTERS: Final = sorted(API_SOURCE.glob("*/router.py"))

MUTATING_METHODS: Final = frozenset({"post", "patch", "put", "delete"})

# The annotation that carries a policy. A route may use any policy; what is
# refused is a mutating route carrying only the identity.
UNGUARDED_IDENTITY: Final = "CurrentIdentity"

# Mutating handlers that deliberately take no policy, each with the reason it is
# safe. A name here is a decision, so adding one should be uncomfortable.
EXEMPT: Final = {
    # Unauthenticated by design: there is no identity to judge. The HMAC
    # signature over the raw body is the gate, and door 4a's whole security
    # model rests on it rather than on a session.
    "receive_deploy",
    # Unauthenticated: it is how an account comes into existence.
    "sign_up",
    # Unauthenticated: they are how a session comes into existence and ends.
    "login",
    "logout",
    # Unauthenticated for the same reason: it *is* the sign-in, the half that
    # follows the password. What stands in for a session is the challenge token,
    # which was issued only after `login` proved the password, expires in
    # minutes, and is worth five attempts. Requiring a session here would require
    # the thing this endpoint exists to issue.
    "submit_second_factor",
    # Unauthenticated by design: both are followed from a mail client, which is
    # a different browser session from the one that asked — often a different
    # device. The token in the body is the whole credential, single-use and
    # expiring (`auth/tokens.py`), and requiring a session would make the flows
    # useless to the person they exist for: somebody locked out.
    "verify_email",
    "request_password_reset",
    "confirm_password_reset",
    # Unauthenticated for the same reason, and it takes no address: the address
    # it moves the account to comes from the token, which was minted only after
    # `request_email_change` verified the current password. The link is opened
    # at the new address, which is a mailbox, not a signed-in browser.
    "confirm_email_change",
    # Authenticated, and deliberately available to *any* role. It mails a fresh
    # verification link to `identity.user.email` and to no address the caller
    # supplied, so the only account it can affect is the caller's own. Requiring
    # `owner` here would stop a member ever proving their own address.
    "resend_verification",
    # Authenticated, and deliberately available to *any* role, for the same
    # reason as `resend_verification`: all three act on `identity.user` and take
    # no user id from a path or a body, so the only account any of them can
    # affect is the caller's own. A policy here would say "only an owner may
    # change their own name", which is not a rule anybody wants — and the day
    # F26 adds a member, it would be the rule that stops them ever setting a
    # password. `change_password` carries its own gate instead, and a stronger
    # one than a role: it verifies the current password, which is the one thing
    # a stolen session cannot supply.
    "update_profile",
    "change_password",
    "sign_out_everywhere",
    # Authenticated, any role, and acts only on `identity.user`. Its gate is not
    # a role but the current password, which is stronger here than anywhere else
    # in this list: whoever holds an account's address can reset its password,
    # so this is the endpoint an account takeover reaches for first. A role
    # policy would let an owner with a stolen session through and lock a member
    # out of their own address.
    "request_email_change",
    # Authenticated, any role, and every one of them acts only on
    # `identity.user`. Three of the four carry a gate stronger than a role —
    # `begin_second_factor_enrolment` and `disable_second_factor` verify the
    # current password, and `confirm_second_factor` verifies a code against the
    # secret the caller was just offered. `end_session` deliberately has none:
    # it is reached by somebody who believes a session is not theirs, and a
    # stolen session calling it can do nothing but sign somebody out. The id it
    # takes is proved to be in the caller's own index before anything is
    # deleted, so it cannot name another account's session at all.
    "end_session",
    "begin_second_factor_enrolment",
    "confirm_second_factor",
    "disable_second_factor",
    # A POST that reads. Symbolication turns minified frames back into original
    # ones and writes nothing; it is a POST because a stack trace does not fit
    # in a query string.
    "symbolicate",
    # Guarded inside `apikeys/service.py` instead, and more finely: those checks
    # cover site keys as well as ingest keys, which is below route granularity.
    # Moving them up here would lose that.
    "create_api_key",
    "revoke_api_key",
    "create_site_key",
    "revoke_site_key",
    # Unauthenticated by design: a customer's cron job calls it and has no
    # session. The token in the path is the whole credential — shown once, kept
    # only as its hash, rotatable — and the only thing it can do is mark that one
    # heartbeat as called. It has its own rate tier.
    "receive_heartbeat",
}


def _methods_of(decorator: ast.Call) -> set[str]:
    """`@router.post(...)` names its method; `@router.api_route(..., methods=[...])`
    lists them, and a parser that read only the first let a POST through unjudged."""
    assert isinstance(decorator.func, ast.Attribute)
    if decorator.func.attr != "api_route":
        return {decorator.func.attr.lower()}
    for keyword in decorator.keywords:
        if keyword.arg == "methods" and isinstance(keyword.value, ast.List | ast.Tuple):
            return {
                element.value.lower()
                for element in keyword.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
    return set()


def _handlers(router: Path) -> list[tuple[str, str]]:
    """Every handler in a router, as (function name, its argument annotations)."""
    tree = ast.parse(router.read_text(encoding="utf-8"))
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        # Any router object, not only one called `router`. `apikeys` serves its
        # browser keys from a second `site_key_router`, and a parser that looked
        # for one name skipped that whole router without saying so — which is
        # the failure this file exists to catch, in this file.
        methods = {
            method
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and isinstance(decorator.func.value, ast.Name)
            and decorator.func.value.id.endswith("router")
            for method in _methods_of(decorator)
        }
        if not methods & MUTATING_METHODS:
            continue
        annotations = " ".join(
            ast.unparse(argument.annotation)
            for argument in node.args.args + node.args.kwonlyargs
            if argument.annotation is not None
        )
        found.append((node.name, annotations))
    return found


@pytest.mark.parametrize("router", ROUTERS, ids=lambda p: p.parent.name)
def test_a_mutating_route_names_a_policy(router: Path) -> None:
    """A route that changes something says who may change it."""
    offending = [
        name
        for name, annotations in _handlers(router)
        if name not in EXEMPT and UNGUARDED_IDENTITY in annotations
    ]
    assert not offending, (
        f"{router.parent.name}/{router.name}: {offending} change something while taking plain "
        f"`{UNGUARDED_IDENTITY}`. Use a policy from `auth/policy.py`, or add the name to EXEMPT "
        f"with the reason it is safe."
    )


def test_every_exemption_names_a_route_that_exists() -> None:
    """An exemption for a deleted route silently widens the guard.

    The list is the only place this check can be weakened, so a stale entry is
    worth catching — it is how an exemption written for one route ends up
    covering a different one that reuses the name.
    """
    declared = {name for router in ROUTERS for name, _ in _handlers(router)}
    stale = sorted(EXEMPT - declared)
    assert not stale, f"EXEMPT names handlers that no longer exist: {stale}"


def test_the_policy_refuses_an_unknown_role() -> None:
    """A typo in a policy is a gate that admits nobody.

    Checked when the module is imported rather than when a request arrives, so
    it fails at start-up instead of in front of a customer.
    """
    from nexovue_api.auth.policy import require_role

    with pytest.raises(ValueError, match="unknown role"):
        require_role("owner", "adminstrator")
