"""No vendor's vocabulary reaches a port, and no caller reaches a vendor.

`data/objectstore/store.py` holds the rule these two ports inherit — the object
store is fixed as the generic S3 API and *"nothing here may use a feature
outside that API"*. An interface is only worth having if that rule is checked,
because the failure is silent: the abstraction keeps compiling while a
provider's noun spreads through the code that was supposed to be free of it.

Structural, like `test_tenant_chokepoint_structure.py`. It cannot prove an
implementation is correct; it proves the seam has not been quietly crossed,
which is the mistake that actually happens.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

from nexovue_api import billing, notifications

# The ports themselves. Everything in these packages is provider-agnostic by
# definition; an implementation lives in a submodule named for its vendor.
PORT_MODULES: Final = (
    Path(notifications.__file__).parent / "sender.py",
    Path(billing.__file__).parent / "processor.py",
)

# Names no port may contain. Not an exhaustive list of vendors — a list of the
# ones actually under consideration, so a hasty import shows up. A vendor added
# later must be added here in the same change, and one dropped **stays**: this
# is a list of forbidden words, not of supported providers, and shortening it
# when an adapter is deleted only removes a guard.
VENDOR_NAMES: Final = (
    "postmark",
    "resend",
    "sendgrid",
    "mailgun",
    "brevo",
    "mailersend",
    "boto3",  # SES
    "stripe",
    "paddle",
    "lemonsqueezy",
    "cashfree",
    "razorpay",
    "payu",
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", PORT_MODULES, ids=lambda p: p.name)
def test_a_port_names_no_vendor(path: Path) -> None:
    """The interface speaks our domain, never a provider's.

    A `message_stream`, a `cf_order_token`, a Paddle price id — the first one to
    reach a port is what makes the second implementation a rewrite, and nothing
    fails at the time it is added.
    """
    # The module docstrings name candidates deliberately, to record why the port
    # exists at all. Only code is judged, so the docstring is removed first.
    source = _source(path)
    docstring = ast.get_docstring(ast.parse(source)) or ""
    code_only = source.replace(docstring, "").lower()
    named = [vendor for vendor in VENDOR_NAMES if vendor in code_only]

    assert not named, (
        f"{path.name} names {named} outside its docstring. A port speaks this "
        f"product's vocabulary; a vendor's belongs in that vendor's implementation."
    )


@pytest.mark.parametrize("path", PORT_MODULES, ids=lambda p: p.name)
def test_a_port_imports_no_provider_sdk(path: Path) -> None:
    """A port declares an interface and talks to nothing."""
    tree = ast.parse(_source(path))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    offending = [
        name
        for name in imported
        if any(vendor in name.lower() for vendor in VENDOR_NAMES) or name in {"httpx", "requests"}
    ]
    assert not offending, (
        f"{path.name} imports {offending}. A port is an interface; the client "
        f"that speaks HTTP belongs in an implementation."
    )


def test_every_port_is_a_protocol() -> None:
    """Both ports are `Protocol`s rather than base classes.

    Structural typing, so an implementation does not inherit from us — which is
    what lets one live in another package, or be a test double with no import at
    all.
    """
    from nexovue_api.billing.processor import PaymentProcessor
    from nexovue_api.notifications.sender import EmailSender

    for port in (EmailSender, PaymentProcessor):
        assert getattr(port, "_is_protocol", False), f"{port.__name__} must be a Protocol"
