"""Which repository files may enter a source pack, and which a suggestion may change
(design, *Lists*; spec FR12, FR13, K2, BR7, D9).

Constants, published under the AGPL so a customer can read exactly what is kept back.
**These lists are guesses at what matters, and they should grow.** `CHECKS_VERSION`
changes whenever they or the rules in `checks.py` do, so a proposal made under older
rules reads "Out of date" rather than being judged by newer ones (spec FR21).

**Matching** is on the full repository path, case-insensitive, at any depth: a name
pattern (no `/`) is tried against every segment of the path, folders included, and a
folder pattern (`.github/**`) against every folder. So `services/a/.github/x`, `.ENV`
and a file under a folder named `secrets` all match. Matching every segment rather than
the file name alone is this module's reading of "at any depth", and errs toward
keeping a file back.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Literal

# Bumped whenever a list here or a rule in `checks.py` changes (design, *Lists*).
CHECKS_VERSION: Final = "2026-11-11.1"

# The allow-list (a guess). Nothing else may enter a pack, so `application-prod.yml`,
# `appsettings.json` and key files cannot, even when a stack or a person names them.
SOURCE_EXTENSIONS: Final = frozenset(
    {
        ".py",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".go",
        ".java",
        ".kt",
        ".scala",
        ".rb",
        ".php",
        ".cs",
        ".rs",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".hpp",
        ".swift",
        ".ex",
        ".exs",
    }
)

# Never sent, and no owner may allow them: secrets and keys, CI, agent instructions.
NEVER_SENT_SECRETS: Final = (
    ".env*",
    "*.env",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "*.ppk",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    "id_dsa*",
    "*credentials*",
    "*secret*",
    "*.tfvars",
    "*.tfstate",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    ".htpasswd",
    ".docker/config.json",
    "kubeconfig*",
    "wp-config.php",
    "*service-account*",
)
NEVER_SENT_CI: Final = (
    ".github/**",
    ".gitlab-ci.yml",
    ".gitlab/**",
    ".circleci/**",
    "Jenkinsfile",
    "azure-pipelines.yml",
    ".buildkite/**",
    ".travis.yml",
    ".drone.yml",
    "bitbucket-pipelines.yml",
)
NEVER_SENT_AGENT_INSTRUCTIONS: Final = (
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    ".cursorrules",
    ".cursor/**",
    ".claude/**",
    ".windsurfrules",
    ".clinerules",
    ".gemini/**",
    ".mcp.json",
)
NEVER_SENT_HARD: Final = NEVER_SENT_SECRETS + NEVER_SENT_CI + NEVER_SENT_AGENT_INSTRUCTIONS

# Source files that often hold settings. The only list an owner may loosen, one named
# path at a time, on the consent row (D9).
NEVER_SENT_CONFIGURATION: Final = ("config/**", "*settings*", "*config*", "prod", "production")

# Never changed: everything never sent, plus files that run at install, build or test,
# hooks and editors, and review rules (K2).
NEVER_CHANGED_BUILD: Final = (
    "package.json",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    "poetry.lock",
    "uv.lock",
    "Pipfile",
    "Pipfile.lock",
    "Cargo.lock",
    "Gemfile.lock",
    "composer.json",
    "composer.lock",
    "requirements*.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "go.mod",
    "go.sum",
    "pom.xml",
    "build.gradle*",
    "settings.gradle*",
    "gradlew*",
    ".mvn/**",
    "build.rs",
    "Cargo.toml",
    "Gemfile",
    "Rakefile",
    "Makefile",
    "justfile",
    "Taskfile.yml",
    "Dockerfile*",
    "compose*.y*ml",
    "docker-compose*.y*ml",
)
NEVER_CHANGED_RUNS_AT_BUILD_OR_TEST: Final = (
    "tox.ini",
    "noxfile.py",
    "conftest.py",
    "webpack.config.*",
    "vite.config.*",
    "jest.config.*",
    "eslint.config.*",
)
NEVER_CHANGED_HOOKS_AND_EDITORS: Final = (
    ".husky/**",
    ".pre-commit-config.yaml",
    ".vscode/**",
    ".devcontainer/**",
    ".envrc",
    ".gitmodules",
    ".gitattributes",
)
NEVER_CHANGED_REVIEW: Final = ("CODEOWNERS",)
NEVER_CHANGED_ONLY: Final = (
    NEVER_CHANGED_BUILD
    + NEVER_CHANGED_RUNS_AT_BUILD_OR_TEST
    + NEVER_CHANGED_HOOKS_AND_EDITORS
    + NEVER_CHANGED_REVIEW
)

NeverSentReason = Literal[
    "not_source", "secret_or_key", "ci", "agent_instructions", "configuration", "excluded_by_owner"
]

_FOLDER_SUFFIX: Final = "/**"


@dataclass(frozen=True, slots=True)
class OwnerLists:
    """What an owner set on the consent row: extra paths kept back, which add to both
    hard lists, and configuration-shaped paths allowed by name (D9)."""

    extra_excluded_paths: tuple[str, ...] = ()
    allowed_config_paths: tuple[str, ...] = ()


NO_OWNER_LISTS: Final = OwnerLists()


def _segments(path: str) -> list[str]:
    return [segment for segment in path.casefold().split("/") if segment]


def _matches(pattern: str, path: str) -> bool:
    """A pattern against a repository path, case-insensitive, at any depth."""
    segments = _segments(path)
    wanted = pattern.casefold()
    if wanted.endswith(_FOLDER_SUFFIX):
        folder = _segments(wanted.removesuffix(_FOLDER_SUFFIX))
        folders = segments[:-1]
        return any(folders[start : start + len(folder)] == folder for start in range(len(folders)))
    if "/" in wanted:
        # A fixed path such as `.docker/config.json`, matched as consecutive segments.
        fixed = _segments(wanted)
        return any(
            all(
                fnmatch.fnmatchcase(segments[start + offset], part)
                for offset, part in enumerate(fixed)
            )
            for start in range(len(segments) - len(fixed) + 1)
        )
    return any(fnmatch.fnmatchcase(segment, wanted) for segment in segments)


def matches_any(patterns: Iterable[str], path: str) -> bool:
    return any(_matches(pattern, path) for pattern in patterns)


def normal_path(path: str) -> str:
    """A path as the owner's lists compare it: no leading `/`, case folded."""
    return path.strip("/").casefold()


def _excluded_by_owner(path: str, owner: OwnerLists) -> bool:
    return normal_path(path) in {normal_path(each) for each in owner.extra_excluded_paths}


def has_source_extension(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].casefold()
    return any(name.endswith(extension) for extension in SOURCE_EXTENSIONS) and not any(
        name == extension for extension in SOURCE_EXTENSIONS
    )


def never_sent_reason(path: str, owner: OwnerLists = NO_OWNER_LISTS) -> NeverSentReason | None:
    """Why `path` may not enter a pack, or None when it may (FR12, D9).

    The hard lists are tried before the extension, so a file kept back for being a key
    says so even when its extension would also keep it out.
    """
    if matches_any(NEVER_SENT_SECRETS, path):
        return "secret_or_key"
    if matches_any(NEVER_SENT_CI, path):
        return "ci"
    if matches_any(NEVER_SENT_AGENT_INSTRUCTIONS, path):
        return "agent_instructions"
    if _excluded_by_owner(path, owner):
        return "excluded_by_owner"
    if not has_source_extension(path):
        return "not_source"
    allowed = {normal_path(each) for each in owner.allowed_config_paths}
    if matches_any(NEVER_SENT_CONFIGURATION, path) and normal_path(path) not in allowed:
        return "configuration"
    return None


def is_never_changed(path: str, owner: OwnerLists = NO_OWNER_LISTS) -> bool:
    """K2: the never-changed list is everything never sent, hard or configuration-shaped,
    plus build, test-harness, hook, editor and review files. An owner's allowance lets a
    configuration file be read, never changed."""
    return (
        matches_any(NEVER_SENT_HARD, path)
        or matches_any(NEVER_SENT_CONFIGURATION, path)
        or matches_any(NEVER_CHANGED_ONLY, path)
        or _excluded_by_owner(path, owner)
    )
