# Security policy

## Reporting a vulnerability

**Please do not open a public issue.** Report it privately through GitHub: the **Security** tab of this
repository → **Report a vulnerability**. Only the maintainers see the report.

Include what is affected (the receiver, the API, the console, the browser SDK, a deployment file), how
to reproduce it, and what an attacker gains. You will get an acknowledgement within three working days
and a plan within ten.

## Supported versions

Security fixes go into the latest release. Self-hosted installs upgrade with `./nexovue upgrade`.

## Scope

In scope: everything in this repository and the published images and SDK. Out of scope: findings that
need an already-compromised host or an operator's own credentials, and denial of service by volume
against an install sized below `docs/self-hosting/sizing.md`.
