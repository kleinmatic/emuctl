# The web interface

`emuctl web serve` runs a small web server that shows every guest on the
host: its state, CPU, memory, disk and network use over the last hour or
day, its emulated hardware, the host's bridges, and the logs. From it you can
start and stop guests and use their serial consoles and screens. It needs Linux with
systemd (see [platforms.md](platforms.md)), Python 3.11 or newer, and
`aiohttp`.

A tour of the screens is in [web-tour.md](web-tour.md). What the page can do
and how it is protected is in [web-security.md](web-security.md). Read that
before putting it on a network you do not control: a console on the page is a
root login on many guests.

## Install

```sh
sudo apt install python3-aiohttp openssl
sudo ./install.sh web
```

`install.sh web` installs emuctl as usual, then:

- creates a system account, `emuctl-web`, with no login shell;
- installs `/etc/systemd/system/emuctl-web.service`;
- installs the polkit rule `/etc/polkit-1/rules.d/50-emuctl-web.rules`, which
  lets that account start, stop and signal `emuctl-guest@*` units and nothing
  else.

It does not start or enable anything. Then:

1. **Let the account read the guests' files.** The page reads each guest's
   console log, emulator config and disk image sizes. Add `emuctl-web` to the
   group that owns the guests' workdirs, the group the guest unit's `Group=`
   names:

   ```sh
   sudo usermod -aG <the guests' group> emuctl-web
   ```

   Know what this gives it: anything in a workdir that the group can read,
   which may include disk images. The account is also in `systemd-journal`
   (set in the unit file), so it can read every service's log, not only the
   guests'.

2. **Start it.** `sudo systemctl enable --now emuctl-web`. Leave out
   `enable` if it should not start at boot.

3. **Add a user.** `sudo emuctl web passwd <name>` asks for a password twice
   and stores a scrypt hash in `/etc/emuctl/web-users`. That file is root's,
   mode 0640, group `emuctl-web`: the daemon can read it but cannot add users
   to it. Every user can do everything for now; roles come
   in a later release. `sudo emuctl web passwd <name> --delete` removes one,
   and ends that user's sessions within a request.

4. **Open it** at `https://<host>:8600/`. The first time, the browser warns
   that the certificate is not trusted, because the daemon made it itself.
   Compare the fingerprint the browser shows with the one in the log, and
   accept it only if they match:

   ```sh
   journalctl -u emuctl-web | grep fingerprint
   ```

## Settings: /etc/emuctl/web.conf

The file is optional; these are the defaults. Restart the service after a
change.

```
listen = 0.0.0.0        # the address to listen on; :: for IPv6 too
port = 8600
tls = on                # off serves plain HTTP; see below
tls_cert =              # empty: a self-signed certificate in state_dir
tls_key =
state_dir = /var/lib/emuctl-web
users_file = /etc/emuctl/web-users
task_log = /var/log/emuctl/web-tasks.log
server_names =          # other names the page is reached by, comma-separated
history_file =          # empty: history.json in state_dir; off: not saved
tokens_file = /etc/emuctl/web-tokens   # read-only API tokens: emuctl web token
console_look = amber    # amber, green, white or modern: the Console tab until a user picks
```

`history_file` holds the last day of the graphs, one point a minute per
guest. The daemon writes it every five minutes and when it stops, and reads
it when it starts, so a restart costs at most five minutes of the day.

### Host names

The page answers only to an IP address or to a name it knows: the host's
own name, its short name, `<short name>.local`, `localhost`, and whatever
`server_names` lists. Anything else gets "unknown host name". This stops a
web page elsewhere from pointing its own name at your host to talk to the
daemon (DNS rebinding). If you reach it by another name, a DNS alias say,
add that name to `server_names`.

### The certificate

On its first start the daemon makes a self-signed certificate and key with
the `openssl` program and keeps them in `state_dir` (`web.crt`, and `web.key`
with mode 0600). It reuses them after that. It is valid for ten years for the
host's name, its short name, `<short name>.local`, `localhost`, `127.0.0.1`
and `::1`. It is marked as a server certificate only, never a certificate
authority, so trusting it in a browser trusts this daemon and nothing else.

To use your own, for example from a homelab CA, set both `tls_cert` and
`tls_key` to files the `emuctl-web` account can read. The daemon never
replaces a certificate you named. `tls_cert` may hold the whole chain.

With an ACME CA (step-ca, or any other), certbot can get and renew one. Its
own files are readable by root only, so a deploy hook copies them where the
daemon can read them and restarts it; logins survive the restart.

```sh
sudo certbot certonly --standalone --server https://<your CA>/acme/acme/directory \
    --register-unsafely-without-email --agree-tos -d <host name>
```

`/etc/letsencrypt/renewal-hooks/deploy/emuctl-web`, mode 755:

```sh
#!/bin/sh
set -eu
live=/etc/letsencrypt/live/<host name>
install -d -m 750 -o root -g emuctl-web /etc/emuctl/tls
install -m 640 -o root -g emuctl-web "$live/fullchain.pem" /etc/emuctl/tls/web.crt
install -m 640 -o root -g emuctl-web "$live/privkey.pem" /etc/emuctl/tls/web.key
systemctl try-restart emuctl-web
```

Run the hook once by hand after the first certificate, then set
`tls_cert = /etc/emuctl/tls/web.crt`, `tls_key = /etc/emuctl/tls/web.key`
and `server_names = <host name>` in `web.conf`. `--standalone` needs port 80
free; the CA must be able to reach it. The host must trust your CA's root for
certbot to talk to it.

### Turning TLS off

`tls = off` serves plain HTTP. Then passwords, the session cookie and
everything the page shows, consoles included, cross the network in clear,
and the daemon logs a warning saying so at every start. Only do this behind
something that adds TLS for you, or on a network where nobody else can
listen.

## A guest's screen

A guest whose emulator has its own VNC server (gxemul's `--vnc`, QEMU's
`-vnc`) gets a **Display** tab. Give the guest's conf the server's address,
and if it asks for a password, a file holding it:

```
display = vnc://127.0.0.1:5906
display_password_file = /etc/emuctl/display/<guest>.pass
```

The daemon logs in to the VNC server itself and passes the picture to your
browser, which never sees the password. So the password file must be
readable by `emuctl-web`, and by nobody else who does not need it. Keep it
out of `/etc/emuctl/env/`: the daemon never reads anything there. One way:

```sh
sudo install -d -m 750 -o root -g emuctl-web /etc/emuctl/display
sudo sh -c 'umask 027; cat > /etc/emuctl/display/<guest>.pass'   # type it, then Ctrl-D
sudo chgrp emuctl-web /etc/emuctl/display/<guest>.pass
```

A VNC server that wants TLS first, such as TigerVNC's `Xvnc -SecurityTypes
X509Vnc` (the usual way to put 86Box on a network), needs
`display_auth = x509vnc` and `display_ca` naming its certificate. The daemon
then checks the server's certificate against that file before it sends the
password.

Only operators can open a display, since the keyboard and mouse go straight
to the guest. The daemon connects only while the guest is running, and asks
the VNC server to let several viewers share the screen.

## Scripts and monitoring

For a script, or Prometheus, make a read-only API token instead of a user:

```sh
sudo emuctl web token add prometheus     # prints the token, once
sudo emuctl web token list
sudo emuctl web token revoke prometheus
```

Send it as `Authorization: Bearer <token>`. It reads guest status and
numbers (part of the JSON API, and `/metrics` in Prometheus' format), not
logs or notes, and changes nothing.
[api.md](api.md#api-tokens) has the routes and a Prometheus job.

## Who can reach it

The daemon listens on every address on port 8600, as Proxmox does on 8006.
Anyone who can reach that port can try to log in; after five wrong passwords
from one address it makes that address wait, doubling the wait each time up
to five minutes. To limit who can reach it, use a firewall, or set `listen`
to one address.

## Upgrading

Run `sudo ./install.sh web` from the new version, then
`sudo systemctl restart emuctl-web`. The installer keeps an existing unit
file and polkit rule; compare them with the new ones in `systemd/` and
`examples/polkit/` in case they changed. A restart keeps everyone logged in.

## What it costs

The daemon looks at every guest every 5 seconds, whether or not a page is
open: one `systemctl show` for all running guests and a few small file reads
each. It connects to a guest's `guest_addr` at most every 30 seconds. The
host's network report, which runs `ip`, `bridge` and `ethtool`, is made only
while a page shows it. It keeps the last hour of samples in memory, and the
last day as one-minute averages, written to `history_file` every five
minutes (about 100 KB a guest). A guest's screen thumbnail is one short VNC
connection a minute, only while someone has that guest's summary open.

## When something is wrong

- **The browser cannot connect.** `systemctl status emuctl-web` and
  `journalctl -u emuctl-web`. A port already in use, or a certificate it
  cannot read, stops it at start with a message saying so.
- **Numbers show as "—".** See [monitoring.md](monitoring.md). Disk sizes
  need the account to be able to see into the workdir (step 1 above).
- **"No users" in the log.** Run step 3.
- **"unknown host name".** You reached it by a name it does not know; add
  the name to `server_names`.
- **The page logs you out.** A session ends after 12 hours without you doing
  anything (the page's own timed refreshes do not count), a day after
  logging in, or when the daemon restarts.
