# Releasing

## A nexovue release

Versions are tagged by hand (founder, 2026-10-28). From an up-to-date `main` whose CI has passed:

```bash
git tag v0.2.0          # or v0.2.0-rc.1 for a candidate
git push origin v0.2.0
```

`.github/workflows/release.yml` then:

1. **waits for that commit's CI** and stops if it failed or never ran — nothing untested is published;
2. builds both images natively on amd64 and arm64, pushes them by digest, and joins them under
   `X.Y.Z`, `X.Y` (not for a candidate) and the commit;
3. signs each image by digest with cosign (keyless), with provenance and an SBOM; GitHub's attestation
   is added once the repository is public;
4. builds the install kit, `install.sh` (the one-line install, its version written in) and an offline
   bundle per architecture (`deploy/release/kit.sh`), refuses any file over GitHub's 2 GiB, and signs
   each with `cosign sign-blob`;
5. opens the GitHub release with generated notes and attaches the kit, both bundles, their signatures
   and `SHA256SUMS`. A hyphenated tag is a pre-release.

Before tagging, write in the release's notes anything an operator must know: a migration that changes
the schema (rollback then means a restore), one that runs long on a large install, a setting added.

## Checking a release

```bash
cosign verify ghcr.io/nexovue-tech/nexovue:0.2.0 \
  --certificate-identity-regexp '^https://github.com/nexovue-tech/nexovue/.github/workflows/release.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

and the same for `nexovue-pipeline`; `cosign verify-blob` for the kit ([install](../self-hosting/install.md#checking-a-download)).

## The browser SDK

A separate tag, `sdk-vX.Y.Z`, matching `packages/browser-sdk/package.json`, runs
`.github/workflows/release-sdk.yml`: it tests, builds and measures the SDK, then **stages** the version on
npm through trusted publishing — no npm token exists, and the version carries provenance. **A staged
version is not published.** It goes live when the founder approves it, with his second factor:

- on npmjs.com, the package's page lists it for approval; or
- `npm stage list @nexovue/browser-sdk`, then `npm stage approve <stage-id>`.

Why staged (founder, 2026-10-30): the SDK runs inside customers' web pages. A stolen workflow or a
poisoned dependency in it can stage a version but never publish one; only a person can. npm's trusted
publisher for `@nexovue/browser-sdk` allows staging only — `npm publish` from the workflow is refused.

Set up 2026-10-29/30: the `nexovue` organization on npm; `0.1.0` published by hand (the one version
without provenance — npm offers a package's settings only once it exists); the trusted publisher
`nexovue-tech/nexovue`, `release-sdk.yml`, stage only; publishing access requiring two-factor
authentication with no bypass tokens. Staging starts once the repository variable `SDK_PUBLISH` is
`true`.

A new version: raise `version` in `package.json` in a pull request, merge it, then tag `sdk-vX.Y.Z` on
the merge commit. npm never takes the same version twice.

## Not yet established

- A release has not yet carried the kit or `install.sh`: the first `v*` tag after this change is their
  proof.
- Release notes are GitHub's generated list; nothing checks that a schema change is called out.
