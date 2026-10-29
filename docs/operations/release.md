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
`.github/workflows/release-sdk.yml`: tests, builds, measures the bundle and shows what npm would receive.
It **publishes only when the repository variable `SDK_PUBLISH` is `true`**, through npm's trusted
publishing — no npm token exists, and the package carries provenance. The repository is public and the
package publishable (2026-10-29). What remains is the founder's, once, on npmjs.com: the `nexovue`
organization (so `@nexovue/…` is ours), then `@nexovue/browser-sdk`'s trusted publisher — repository
`nexovue-tech/nexovue`, workflow `release-sdk.yml` — and the variable set. npm's documentation does not
say whether a trusted publisher can be named before the package's first version exists; if npmjs.com
offers no settings page for it yet, that first version is published once by hand, then never again.

## Not yet established

- A release has not yet carried the kit or `install.sh`: the first `v*` tag after this change is their
  proof.
- Release notes are GitHub's generated list; nothing checks that a schema change is called out.
