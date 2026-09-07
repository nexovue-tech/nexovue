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
}


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
            decorator.func.attr.lower()
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and isinstance(decorator.func.value, ast.Name)
            and decorator.func.value.id.endswith("router")
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
