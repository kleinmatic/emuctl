#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
#
# Copy the web page's third-party files into src/emuctl/web/static/vendor/.
# Each package is fetched from the npm registry at a fixed version, checked
# against a SHA-256 written here, and copied with its licence. The results are
# committed, so the page loads nothing from the internet and building emuctl
# needs no network. Run this only to change a version.
#
#   tools/vendor.sh
#
# To move to a new version: change the version, run once to see the new sum,
# check the package's changelog, then write the new sum here.
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
out="$here/src/emuctl/web/static/vendor"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

fetch() {   # name version sha256
    url="https://registry.npmjs.org/$1/-/$(basename "$1")-$2.tgz"
    curl -sfL -o "$tmp/pkg.tgz" "$url" || { echo "vendor.sh: cannot fetch $url" >&2; exit 1; }
    got=$(sha256sum "$tmp/pkg.tgz" | cut -d' ' -f1)
    if [ "$got" != "$3" ]; then
        echo "vendor.sh: $1 $2: sha256 is $got, expected $3" >&2
        exit 1
    fi
    rm -rf "$tmp/package"
    tar -xzf "$tmp/pkg.tgz" -C "$tmp"
}

rm -rf "$out"
mkdir -p "$out/fonts" "$out/xterm"

fetch @fontsource/ibm-plex-sans 5.3.0 232592af20ae413026cc705fc9c104135fb760432fd710f06a02c39e062d3213
for w in 400 500 600; do cp "$tmp/package/files/ibm-plex-sans-latin-$w-normal.woff2" "$out/fonts/"; done
cp "$tmp/package/LICENSE" "$out/fonts/LICENSE-ibm-plex.txt"

fetch @fontsource/ibm-plex-mono 5.3.0 60d3c0cfa549cb06fcb8867ee83f994d78e212abfac3bef3275436acb5c4d029
for w in 400 500; do cp "$tmp/package/files/ibm-plex-mono-latin-$w-normal.woff2" "$out/fonts/"; done

fetch @xterm/xterm 6.0.0 908e66e04af6c8dc6b00dd3b54de088e2e81e5ed866284fd6c2fb3c2d1c7a3f6
cp "$tmp/package/lib/xterm.mjs" "$tmp/package/css/xterm.css" "$out/xterm/"
cp "$tmp/package/LICENSE" "$out/xterm/LICENSE-xterm.txt"

fetch @xterm/addon-fit 0.11.0 26003b4517a132b64e4ff228fd88a5fda3fff5e606c76093f6dcff772e9ecec0
cp "$tmp/package/lib/addon-fit.mjs" "$out/xterm/"
cp "$tmp/package/LICENSE" "$out/xterm/LICENSE-addon-fit.txt"

cat > "$out/README.md" <<'EOF'
# Third-party files

Copied by `tools/vendor.sh`; do not edit. Each keeps its own licence.

| Files | From | Licence |
|---|---|---|
| `fonts/ibm-plex-*.woff2` | IBM Plex Sans and Mono, via @fontsource 5.3.0 | SIL Open Font License 1.1 (`fonts/LICENSE-ibm-plex.txt`) |
| `xterm/xterm.mjs`, `xterm/xterm.css` | @xterm/xterm 6.0.0 | MIT (`xterm/LICENSE-xterm.txt`) |
| `xterm/addon-fit.mjs` | @xterm/addon-fit 0.11.0 | MIT (`xterm/LICENSE-addon-fit.txt`) |
EOF
echo "vendored into $out"
