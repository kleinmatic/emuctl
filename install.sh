#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
#
# Install emuctl without pip. emuctl needs only Python 3.10+ and its standard
# library, so this copies the package and writes a launcher.
#
#   sudo ./install.sh              install under /usr/local
#   sudo PREFIX=/opt/emuctl ./install.sh
#   sudo ./install.sh uninstall    remove the program (guest confs are kept)
#   sudo ./install.sh web          install, then set up the web interface too
#
# It also installs the service file for this host (a systemd template unit, or
# the FreeBSD rc.d script) unless one is already there, and creates the guest
# conf directory. It does not create a user or start anything. `web` also
# creates the emuctl-web account and installs its unit and polkit rule
# (docs/web.md); it enables nothing.
set -eu

PREFIX=${PREFIX:-/usr/local}
PYTHON=${PYTHON:-python3}
here=$(cd "$(dirname "$0")" && pwd)
lib="$PREFIX/lib/emuctl"
bin="$PREFIX/bin/emuctl"

case "$(uname -s)" in
    FreeBSD) etc=/usr/local/etc/emuctl; svc_src="$here/rc.d/emuctl"
             svc_dst=/usr/local/etc/rc.d/emuctl; svc_mode=555 ;;
    *)       etc=/etc/emuctl; svc_src="$here/systemd/emuctl-guest@.service"
             svc_dst=/etc/systemd/system/emuctl-guest@.service; svc_mode=644 ;;
esac

want_web=no
if [ "${1:-}" = web ]; then
    want_web=yes
    { [ "$(uname -s)" = Linux ] && [ -d /run/systemd/system ]; } ||
        { echo "install.sh: the web interface needs Linux with systemd" >&2; exit 1; }
fi

if [ "${1:-}" = uninstall ]; then
    rm -rf "$lib"
    rm -f "$bin"
    echo "removed $lib and $bin"
    echo "left in place: $svc_dst and $etc"
    exit 0
fi

command -v "$PYTHON" >/dev/null || { echo "install.sh: $PYTHON not found" >&2; exit 1; }
# The launcher names the interpreter by full path: service managers run with a
# short PATH that may not include it.
PYTHON=$(command -v "$PYTHON")
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))' ||
    { echo "install.sh: Python 3.10 or newer is required" >&2; exit 1; }

rm -rf "$lib"
mkdir -p "$lib" "$PREFIX/bin" "$etc/guests"
cp -R "$here/src/emuctl" "$lib/"
find "$lib" -name __pycache__ -type d -prune -exec rm -rf {} +
rm -f "$lib/emuctl/BUILD"
# Record the commit when installing from a git checkout, so `emuctl --version`
# says which build this is. safe.directory: under sudo, git refuses a checkout
# owned by another user.
if commit=$(git -c safe.directory="$here" -C "$here" rev-parse --short HEAD 2>/dev/null); then
    git -c safe.directory="$here" -C "$here" diff --quiet HEAD 2>/dev/null || commit="$commit-dirty"
    echo "$commit" > "$lib/emuctl/BUILD"
fi
rm -f "$bin"
cat > "$bin" <<LAUNCHER
#!/bin/sh
PYTHONPATH="$lib\${PYTHONPATH:+:\$PYTHONPATH}" exec $PYTHON -m emuctl.cli "\$@"
LAUNCHER
chmod 755 "$bin"
echo "installed $("$bin" --version) to $bin"

if [ -e "$svc_dst" ]; then
    echo "kept existing $svc_dst"
else
    sed "s|/usr/local/bin/emuctl|$bin|g" "$svc_src" > "$svc_dst"
    chmod "$svc_mode" "$svc_dst"
    echo "installed $svc_dst"
    [ -d /run/systemd/system ] && systemctl daemon-reload || true
fi
echo "guest confs go in $etc/guests; see docs/quickstart.md"

[ "$want_web" = yes ] || exit 0

"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))' ||
    echo "install.sh: warning: the web interface needs Python 3.11 or newer"
"$PYTHON" -c 'import aiohttp' 2>/dev/null ||
    echo "install.sh: warning: aiohttp is missing; install it: apt install python3-aiohttp"
command -v openssl >/dev/null ||
    echo "install.sh: warning: openssl is missing; it makes the self-signed certificate"
if ! getent passwd emuctl-web >/dev/null; then
    useradd --system --home-dir /var/lib/emuctl-web --no-create-home \
        --shell /usr/sbin/nologin --user-group emuctl-web
    echo "created the emuctl-web account"
fi
web_dst=/etc/systemd/system/emuctl-web.service
if [ -e "$web_dst" ]; then
    echo "kept existing $web_dst"
else
    sed "s|/usr/local/bin/emuctl|$bin|g" "$here/systemd/emuctl-web.service" > "$web_dst"
    chmod 644 "$web_dst"
    systemctl daemon-reload
    echo "installed $web_dst"
fi
rule=/etc/polkit-1/rules.d/50-emuctl-web.rules
if [ -e "$rule" ]; then
    echo "kept existing $rule"
elif [ -d /etc/polkit-1/rules.d ]; then
    install -m 644 "$here/examples/polkit/50-emuctl-web.rules" "$rule"
    echo "installed $rule"
else
    echo "install.sh: warning: no /etc/polkit-1/rules.d; install polkitd, then copy"
    echo "  examples/polkit/50-emuctl-web.rules there, or the page cannot start guests"
fi
echo "next steps (docs/web.md):"
echo "  1. let it read guest workdirs:  usermod -aG <the guests' group> emuctl-web"
echo "  2. start it:                    systemctl enable --now emuctl-web"
echo "  3. add a user:                  emuctl web passwd <name>"
echo "  4. open https://$(hostname):8600/ and compare the certificate's fingerprint with"
echo "     the one in: journalctl -u emuctl-web"
