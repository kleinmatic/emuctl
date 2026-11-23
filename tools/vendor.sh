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
mkdir -p "$out/fonts" "$out/xterm" "$out/novnc"

fetch @fontsource/ibm-plex-sans 5.3.0 232592af20ae413026cc705fc9c104135fb760432fd710f06a02c39e062d3213
for w in 400 500 600; do cp "$tmp/package/files/ibm-plex-sans-latin-$w-normal.woff2" "$out/fonts/"; done
cp "$tmp/package/LICENSE" "$out/fonts/LICENSE-ibm-plex.txt"

fetch @fontsource/ibm-plex-mono 5.3.0 60d3c0cfa549cb06fcb8867ee83f994d78e212abfac3bef3275436acb5c4d029
for w in 400 500; do cp "$tmp/package/files/ibm-plex-mono-latin-$w-normal.woff2" "$out/fonts/"; done

# The console font: VT323 from Google Fonts at a fixed commit, with the
# line-drawing characters added by tools/mkfont.py (needs fontTools and
# brotli; set PYTHON to an interpreter that has them).
gf=https://raw.githubusercontent.com/google/fonts/eb8781e516576b414603df8cd267c0f21c9b4ee2/ofl/vt323
fetch_file() {  # url sha256 destination
    curl -sfL -o "$3" "$1" || { echo "vendor.sh: cannot fetch $1" >&2; exit 1; }
    got=$(sha256sum "$3" | cut -d' ' -f1)
    if [ "$got" != "$2" ]; then
        echo "vendor.sh: $1: sha256 is $got, expected $2" >&2
        exit 1
    fi
}
fetch_file "$gf/VT323-Regular.ttf" cf4de751ada78ceac033dbe16a687742939995b77bc2a052ae17a4957958594d "$tmp/VT323-Regular.ttf"
fetch_file "$gf/OFL.txt" 27d9af34210253e7ca1251fbace86c6f65b40031d6ce1a75493a1b2093631298 "$out/fonts/LICENSE-vt323.txt"
"${PYTHON:-python3}" "$here/tools/mkfont.py" "$tmp/VT323-Regular.ttf" "$out/fonts/emuctl-vt.woff2"

fetch @xterm/xterm 6.0.0 908e66e04af6c8dc6b00dd3b54de088e2e81e5ed866284fd6c2fb3c2d1c7a3f6
cp "$tmp/package/lib/xterm.mjs" "$tmp/package/css/xterm.css" "$out/xterm/"
cp "$tmp/package/LICENSE" "$out/xterm/LICENSE-xterm.txt"

fetch @xterm/addon-fit 0.11.0 26003b4517a132b64e4ff228fd88a5fda3fff5e606c76093f6dcff772e9ecec0
cp "$tmp/package/lib/addon-fit.mjs" "$out/xterm/"
cp "$tmp/package/LICENSE" "$out/xterm/LICENSE-addon-fit.txt"

fetch @novnc/novnc 1.7.0 32689f18d6abe96bc6530828a6bd0b9ae33bda07c083a6575ed255b5a8f2e903
cp -R "$tmp/package/core" "$tmp/package/vendor" "$out/novnc/"
cp "$tmp/package/LICENSE.txt" "$out/novnc/LICENSE-novnc.txt"
find "$out/novnc" -name '*.md' -delete

cat > "$out/README.md" <<'EOF'
# Third-party files

Copied by `tools/vendor.sh`; do not edit. Each keeps its own licence.

| Files | From | Licence |
|---|---|---|
| `fonts/ibm-plex-*.woff2` | IBM Plex Sans and Mono, via @fontsource 5.3.0 | SIL Open Font License 1.1 (`fonts/LICENSE-ibm-plex.txt`) |
| `fonts/emuctl-vt.woff2` | VT323 (google/fonts `eb8781e`) with line-drawing glyphs added by `tools/mkfont.py`, renamed "emuctl VT" | SIL Open Font License 1.1 (`fonts/LICENSE-vt323.txt`) |
| `xterm/xterm.mjs`, `xterm/xterm.css` | @xterm/xterm 6.0.0 | MIT (`xterm/LICENSE-xterm.txt`) |
| `xterm/addon-fit.mjs` | @xterm/addon-fit 0.11.0 | MIT (`xterm/LICENSE-addon-fit.txt`) |
| `novnc/core/`, `novnc/vendor/pako/` | @novnc/novnc 1.7.0 | MPL 2.0, with pako under MIT (`novnc/LICENSE-novnc.txt` lists both) |
EOF
echo "vendored into $out"
