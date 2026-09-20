"""Every route that changes something names the action it performs — the right one.

`auth/policy.py` holds one table of who may do what (F26, FR1). That only holds
if a route added next month declares its action the day it is written, and if a
route's action cannot quietly change to one more roles may perform. Both are
checked here: every mutating handler must be in `POLICY_OF` with its exact
policy, or in `EXEMPT` with its reason.

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
import re
from pathlib import Path
from typing import Final

import pytest

from nexovue_api import auth

# The source root rather than one package, so a route moving between packages
# stays judged. There is one package today; there were two, and the scanner did
# not notice the day the most powerful routes in the system left its tree.
API_SOURCE: Final = Path(auth.__file__).resolve().parents[2]
# Every module, not only files named `router.py`: `channels/test_router.py` served
# Send test for months without ever being judged, because the glob said
# `*/router.py` and that file is not one.
ROUTERS: Final = sorted(
    path for path in API_SOURCE.rglob("*.py") if "router" in path.read_text(encoding="utf-8")
)

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
    # Unauthenticated by design: both are reached from an invite email by
    # someone who is not signed in yet. The link's token in the body is the whole
    # credential — 256 bits, stored only as a hash, single-use and expiring in
    # seven days (`auth/set_password.py`) — and both sit on the AUTH rate tier.
    # Preview is a POST so the token travels in a body, not a logged URL.
    "preview_invitation",
    "accept_invitation",
    # The platform gate itself (`docs/changes/super-admin-in-the-console/`).
    # Both act only on the caller's own session and neither can reach another
    # account. `enter_admin_mode` carries a gate stronger than any role — it
    # verifies the password or a second-factor code, and refuses anyone but the
    # operator whatever they present. `leave_admin_mode` only ever takes access
    # away, so requiring a role to give it up would be a way to keep it.
    "enter_admin_mode",
    "leave_admin_mode",
    # A POST that reads. Symbolication turns minified frames back into original
    # ones and writes nothing; it is a POST because a stack trace does not fit
    # in a query string.
    "symbolicate",
    # Unauthenticated by design: the processor calls it, and the signature over
    # the raw body is the gate (`billing/`), as for `receive_deploy`.
    "receive_billing_event",
    # Unauthenticated: the browser returns here from the processor's checkout,
    # and nothing it carries is trusted — payment is confirmed only by the
    # signed webhook (ADR-008).
    "return_from_checkout",
    # Unauthenticated by design: a customer's cron job calls it and has no
    # session. The token in the path is the whole credential — shown once, kept
    # only as its hash, rotatable — and the only thing it can do is mark that one
    # heartbeat as called. It has its own rate tier.
    "receive_heartbeat",
}


# Every gated mutating handler and the policy it must carry, as
# `<module path>:<handler>`. Changing a policy here is changing who may do
# something (FR1), and it should read that way in review.
POLICY_OF: Final = {
    "nexovue_api/alerts/router.py:create_rule": "MayOperateAlerts",
    "nexovue_api/alerts/router.py:update_rule": "MayOperateAlerts",
    "nexovue_api/alerts/router.py:mute_rule": "MayOperateAlerts",
    "nexovue_api/alerts/router.py:delete_rule": "MayOperateAlerts",
    "nexovue_api/maintenance/router.py:create_window": "MayOperateAlerts",
    "nexovue_api/maintenance/router.py:update_window": "MayOperateAlerts",
    "nexovue_api/maintenance/router.py:delete_window": "MayOperateAlerts",
    "nexovue_api/checks/router.py:create_check": "MayOperateChecks",
    "nexovue_api/checks/router.py:update_check": "MayOperateChecks",
    "nexovue_api/checks/router.py:delete_check": "MayOperateChecks",
    "nexovue_api/checks/router.py:issue_heartbeat_token": "MayOperateChecks",
    "nexovue_api/checkruns/router.py:run_now": "MayOperateChecks",
    "nexovue_api/checkfiles/router.py:upload_file": "MayOperateChecks",
    "nexovue_api/checkimports/router.py:preview_import": "MayOperateChecks",
    "nexovue_api/checkimports/router.py:confirm_import": "MayOperateChecks",
    "nexovue_api/sourcemaps/router.py:upload_source_map": "MayUploadSourceMaps",
    "nexovue_api/channels/router.py:create_channel": "MayManageChannels",
    "nexovue_api/channels/router.py:update_channel": "MayManageChannels",
    "nexovue_api/channels/router.py:rotate_signing_secret": "MayManageChannels",
    "nexovue_api/channels/router.py:mute_channel": "MayManageChannels",
    "nexovue_api/channels/router.py:set_quiet_hours": "MayManageChannels",
    "nexovue_api/channels/router.py:delete_channel": "MayManageChannels",
    "nexovue_api/channels/test_router.py:send_test": "MayManageChannels",
    "nexovue_api/checksecrets/router.py:write_secret": "MayManageCheckSecrets",
    "nexovue_api/checksecrets/router.py:delete_secret": "MayManageCheckSecrets",
    "nexovue_api/checksecrets/router.py:write_variable": "MayManageCheckSecrets",
    "nexovue_api/checksecrets/router.py:delete_variable": "MayManageCheckSecrets",
    "nexovue_api/projects/router.py:create_project": "MayConfigureProjects",
    "nexovue_api/projects/router.py:update_project": "MayConfigureProjects",
    "nexovue_api/projects/router.py:delete_project": "MayConfigureProjects",
    "nexovue_api/projects/router.py:replace_sampling_config": "MayConfigureProjects",
    "nexovue_api/projects/router.py:replace_masking_config": "MayConfigureProjects",
    "nexovue_api/apikeys/router.py:create_api_key": "MayManageCredentials",
    "nexovue_api/apikeys/router.py:revoke_api_key": "MayManageCredentials",
    # The platform surface (`docs/changes/super-admin-in-the-console/`): nexovue's
    # own operator, and only with a session re-verified in the last 30 minutes.
    "nexovue_api/admin/router.py:onboard": "MayAdministerThePlatform",
    "nexovue_api/admin/router.py:resend_owner_link": "MayAdministerThePlatform",
    "nexovue_api/admin/router.py:suspend": "MayAdministerThePlatform",
    "nexovue_api/admin/router.py:resume": "MayAdministerThePlatform",
    "nexovue_api/admin/router.py:grant_plan": "MayAdministerThePlatform",
    "nexovue_api/admin/router.py:end_grant": "MayAdministerThePlatform",
    "nexovue_api/admin/router.py:view_as": "MayAdministerThePlatform",
    # F36. A read token is not an ingest key: one reads the organization's data,
    # the other writes into it, and they are withdrawn by different people's work.
    "nexovue_api/apitokens/router.py:create_api_token": "MayManageApiTokens",
    "nexovue_api/apitokens/router.py:revoke_api_token": "MayManageApiTokens",
    "nexovue_api/apikeys/router.py:create_site_key": "MayManageCredentials",
    "nexovue_api/apikeys/router.py:revoke_site_key": "MayManageCredentials",
    "nexovue_api/deploys/router.py:create_webhook_secret": "MayManageCredentials",
    "nexovue_api/deploys/router.py:revoke_webhook_secret": "MayManageCredentials",
    "nexovue_api/integrations/router.py:create_integration": "MayManageCredentials",
    "nexovue_api/integrations/router.py:delete_integration": "MayManageCredentials",
    "nexovue_api/billing/router.py:start_checkout": "MayCommitSpend",
    "nexovue_api/billing/router.py:cancel_subscription": "MayCommitSpend",
    "nexovue_api/organizations/router.py:delete_organization": "MayEndTheOrganization",
    "nexovue_api/members/router.py:invite_member": "MayManageMembers",
    "nexovue_api/members/router.py:resend_invitation": "MayManageMembers",
    "nexovue_api/members/router.py:withdraw_invitation": "MayManageMembers",
    "nexovue_api/members/router.py:change_role": "MayManageMembers",
    "nexovue_api/members/router.py:remove_member": "MayManageMembers",
    "nexovue_api/support/router.py:grant_support_access": "MayGrantSupport",
    "nexovue_api/support/router.py:withdraw_support_access": "MayGrantSupport",
}

# Reads that return a secret, and so are an action even though they change
# nothing: a viewer reading every screen must not read these.
SECRET_READS: Final = {
    "nexovue_api/deploys/router.py:read_webhook_secrets": "MayManageCredentials",
    # Not a secret, but the organization's history of who did what: an owner's
    # and an admin's to read (FR36, AC38).
    "nexovue_api/audit/router.py:read_audit_log": "MayReadAudit",
    # Every time support viewed as someone, and whether it may: the owner's.
    "nexovue_api/support/router.py:read_support_access": "MayGrantSupport",
    # Not secrets either, but reads that cross every tenant: who the customers
    # are, and everything about one of them. They change nothing, so the
    # mutating rule above would never look at them — and the day one lost its
    # gate, nothing else would notice (`docs/changes/super-admin-in-the-console/`).
    "nexovue_api/admin/router.py:search_organizations": "MayAdministerThePlatform",
    "nexovue_api/admin/router.py:organization": "MayAdministerThePlatform",
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


def _handlers(router: Path) -> list[tuple[str, set[str], str]]:
    """Every handler in a module, as (`<module>:<name>`, its methods, its annotations)."""
    tree = ast.parse(router.read_text(encoding="utf-8"))
    module = router.relative_to(API_SOURCE).as_posix()
    found: list[tuple[str, set[str], str]] = []
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
        if not methods:
            continue
        annotations = " ".join(
            ast.unparse(argument.annotation)
            for argument in node.args.args + node.args.kwonlyargs
            if argument.annotation is not None
        )
        found.append((f"{module}:{node.name}", methods, annotations))
    return found


def _all_handlers() -> list[tuple[str, set[str], str]]:
    return [handler for router in ROUTERS for handler in _handlers(router)]


def _mutating() -> list[tuple[str, str]]:
    return [
        (key, annotations)
        for key, methods, annotations in _all_handlers()
        if methods & MUTATING_METHODS
    ]


def _name(key: str) -> str:
    return key.rpartition(":")[2]


def test_every_mutating_route_is_gated_or_exempt() -> None:
    """A new mutating route must be placed: in `POLICY_OF` with its policy, or in
    `EXEMPT` with the reason it needs none."""
    unplaced = sorted(
        key for key, annotations in _mutating() if key not in POLICY_OF and _name(key) not in EXEMPT
    )
    assert not unplaced, (
        f"{unplaced} change something and are in neither POLICY_OF nor EXEMPT. Give each a "
        f"policy from `auth/policy.py` and add it to POLICY_OF, or add it to EXEMPT "
        f"with its reason."
    )


@pytest.mark.parametrize("key", sorted({**POLICY_OF, **SECRET_READS}))
def test_a_route_carries_exactly_its_policy(key: str) -> None:
    """The policy on the route is the one the table names — so widening a gate is
    a change to this file, not a quiet edit to an annotation."""
    expected = {**POLICY_OF, **SECRET_READS}[key]
    annotations = {found: ann for found, _, ann in _all_handlers()}
    assert key in annotations, f"{key} is in the table but no such handler exists"
    carried = set(re.findall(r"\bMay\w+", annotations[key]))
    assert carried == {expected}, f"{key} carries {sorted(carried)}; the table says {expected}"
    assert UNGUARDED_IDENTITY not in annotations[key]


def test_an_exempt_route_takes_no_policy_by_accident() -> None:
    """An exempt handler is exempt on purpose; one that also carries a policy is a
    stale exemption hiding a gated route from `POLICY_OF`."""
    both = sorted(key for key, ann in _mutating() if _name(key) in EXEMPT and "May" in ann)
    assert not both, f"{both} are exempt but carry a policy; move them to POLICY_OF"


def test_every_exemption_names_a_route_that_exists() -> None:
    """An exemption for a deleted route silently widens the guard.

    The list is the only place this check can be weakened, so a stale entry is
    worth catching — it is how an exemption written for one route ends up
    covering a different one that reuses the name.
    """
    declared = {_name(key) for key, _ in _mutating()}
    stale = sorted(EXEMPT - declared)
    assert not stale, f"EXEMPT names handlers that no longer exist: {stale}"


def test_every_policy_named_exists() -> None:
    from nexovue_api.auth import policy

    missing = sorted({*POLICY_OF.values(), *SECRET_READS.values()} - set(dir(policy)))
    assert not missing, f"no such policy in auth/policy.py: {missing}"
