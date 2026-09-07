"""The console CSP names a hash of an inline script that lives in another repo tree.

Nothing else links the two files, so a one-byte edit to `apps/web/index.html` would
silently stop the theme bootstrap from running and leave every light-mode user with a
dark first paint. These tests are that link.

They read files only — no Caddy, no network.
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CADDYFILE = REPO_ROOT / "deploy" / "caddy" / "Caddyfile"
WEB_INDEX_HTML = REPO_ROOT / "apps" / "web" / "index.html"

# `<script>` with no `src` attribute — the only kind a CSP hash can cover.
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.DOTALL)
CSP_DIRECTIVE = re.compile(r'^\s*Content-Security-Policy\s+"([^"]+)"', re.MULTILINE)
SCRIPT_SRC_HASHES = re.compile(r"'(sha256-[A-Za-z0-9+/=]+)'")


def _csp() -> str:
    match = CSP_DIRECTIVE.search(CADDYFILE.read_text(encoding="utf-8"))
    assert match is not None, f"no enforced Content-Security-Policy in {CADDYFILE}"
    return match.group(1)


def _inline_script_hashes() -> set[str]:
    bodies = INLINE_SCRIPT.findall(WEB_INDEX_HTML.read_text(encoding="utf-8"))
    return {
        "sha256-" + base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
        for body in bodies
    }


def test_csp_covers_every_inline_script_in_the_built_page() -> None:
    """Every inline script must be named by the policy, or the browser drops it."""
    missing = _inline_script_hashes() - set(SCRIPT_SRC_HASHES.findall(_csp()))
    assert not missing, (
        f"{WEB_INDEX_HTML} has inline script(s) the CSP does not allow: {sorted(missing)}. "
        f"Add the hash to the Content-Security-Policy in {CADDYFILE}."
    )


def test_csp_names_no_hash_that_no_longer_exists() -> None:
    """A stale hash is a policy nobody has checked; it must not linger."""
    unused = set(SCRIPT_SRC_HASHES.findall(_csp())) - _inline_script_hashes()
    assert not unused, (
        f"{CADDYFILE} allows script hashes matching nothing in {WEB_INDEX_HTML}: {sorted(unused)}."
    )


def test_console_csp_never_allows_inline_script() -> None:
    """The hash is the whole point; 'unsafe-inline' would make it decorative."""
    script_src = next(
        directive for directive in _csp().split(";") if directive.strip().startswith("script-src")
    )
    assert "'unsafe-inline'" not in script_src
