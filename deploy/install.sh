#!/usr/bin/env bash
# Installs, or upgrades, nexovue from its GitHub release (docs/self-hosting/install.md).
#
#   curl -fsSLO https://github.com/nexovue-tech/nexovue/releases/latest/download/install.sh
#   less install.sh
#   sudo bash install.sh
#
# Every release attaches its own copy, with its version written in below; `latest/download` is
# always the newest release's. It downloads that release's kit over HTTPS, checks it against the
# release's SHA256SUMS, and runs `nexovue install` — or `nexovue upgrade` where nexovue is
# already installed. Arguments are passed to that command.
set -euo pipefail

VERSION="@NEXOVUE_VERSION@"
RELEASES="${NEXOVUE_RELEASES:-https://github.com/nexovue-tech/nexovue/releases/download}"
NEXOVUE_HOME="${NEXOVUE_HOME:-/opt/nexovue}"

die() { echo "install.sh: $*" >&2; exit 1; }

case "$VERSION" in
    @*) die "this is the repository's copy; download install.sh from a release." ;;
esac
for tool in curl tar sha256sum; do
    command -v "$tool" >/dev/null || die "$tool is needed and not installed."
done

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
kit="nexovue-$VERSION.tar.gz"

echo "Downloading nexovue $VERSION"
curl -fsSL --proto '=https' --tlsv1.2 -o "$work/$kit" "$RELEASES/v$VERSION/$kit" \
    || die "could not download $kit."
curl -fsSL --proto '=https' --tlsv1.2 -o "$work/SHA256SUMS" "$RELEASES/v$VERSION/SHA256SUMS" \
    || die "could not download the release's SHA256SUMS."

# Only the kit's line: the release's SHA256SUMS also lists the offline bundles, not downloaded here.
( cd "$work" && grep -E "  \*?$kit\$" SHA256SUMS | sha256sum -c - >/dev/null ) \
    || die "$kit does not match the release's SHA256SUMS; nothing was installed."
echo "Checked against the release's SHA256SUMS"

tar -xzf "$work/$kit" -C "$work"
if [ -f "$NEXOVUE_HOME/nexovue.env" ]; then
    "$work/nexovue-$VERSION/nexovue" upgrade "$@"
else
    "$work/nexovue-$VERSION/nexovue" install "$@"
fi
