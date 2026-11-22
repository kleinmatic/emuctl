# Networking

emuctl does not configure networking. The emulator's own configuration names
the host interface it uses, and something else on the host has to create that
interface before the guest starts.

## What usually needs doing

For a guest to appear on your LAN, the common arrangement is a bridge that
includes the host's uplink, plus one tap per guest attached to it. The emulator
opens the tap.

Linux, once per boot (most people put this in a small systemd unit):

```sh
ip link add br0 type bridge
ip link set eth0 master br0
ip tuntap add dev tap0 mode tap user emuctl
ip link set tap0 master br0
ip link set br0 up; ip link set tap0 up
```

Order the guest after it with a drop-in:

```ini
# systemctl edit emuctl-guest@.service
[Unit]
After=my-taps.service
Wants=my-taps.service
```

FreeBSD: create the bridge and taps in `rc.conf` (`cloned_interfaces`,
`ifconfig_bridge0="addm em0 addm tap0 up"`). The tap device node must be
openable by the account the guests run as; `emuctl_prestart` in `rc.conf` names
a command that runs as root before each start, with the guest name as its
argument, for that kind of preparation.

## Things worth knowing

- A tap can be held by one process. Two guests cannot share one.
- Two guests with the same MAC address, or two images of one machine identity,
  must never run together. Name each in the other's `exclusive_with`.
- When the host is itself a virtual machine, TCP checksum offload on its
  virtual NIC can deliver frames with unfilled checksums to an emulator that
  reads raw frames (pcap). The symptom is ping working while every TCP
  connection to the guest times out. Turning off `tx` offload on the host's
  interface, or using a tap instead of pcap, fixes it.

## What emuctl reports

emuctl still changes nothing here, but it does look. `emuctl net` shows every
bridge, what is in it, and which guest is behind each tap:

```
$ emuctl net
br0  (LAN via eth0) 192.0.2.10/24
  eth0         physical  up    uplink
  tap1         tap       up    vms-alpha
  vea0         other     up    ultrix-mips
br1  (host-only)
  tap5         tap       down  aix-ppc
warning: aix-ppc: interface tap5 is down
```

`emuctl net <guest>` shows one guest from its own side:

```
$ emuctl net ultrix-mips
vea1: veth, up, in br0 (LAN) through its peer vea0
  guest MAC   02:00:00:00:00:05  (seen on the bridge)
  traffic     in 1.2M, out 310.4K, errors 0, dropped 0
```

Both take `--json`. emuctl finds a guest's interfaces in the emulator's own
config (the tap or veth named there, and the guest's MAC address). Set `nics`
in the guest's conf when it cannot.

- **Up** for a tap means a process has it open. A stopped guest's tap shows
  `down`.
- **LAN** means the bridge has a member that leads off the host: a real
  network card, a bond, or a VLAN on one. A bridge with only taps is
  **host-only**.
- **Seen on the bridge** means the bridge has learned the guest's MAC
  address, so the guest has sent at least one frame recently. It needs the
  `bridge` command (iproute2).
- **Traffic** is from the guest's side: *in* is what the guest received. The
  host's own counters for a tap count the other way round.
- A guest on one end of a veth pair (gxemul reads its interface raw) is shown
  in the bridge its peer belongs to.

### Warnings

`emuctl net` and `emuctl check` warn when:

- an interface a guest names does not exist, or is in no bridge;
- two guests use the same interface or the same MAC address and are not
  `exclusive_with` each other;
- a guest is `ready` but its MAC has never been seen on its bridge, which
  usually means the guest's network is not configured, or it is on the wrong
  bridge;
- transmit checksum offload is on for a veth a guest uses (see "Things worth
  knowing" above; needs `ethtool`).

## The health check

Set `guest_addr` (and `guest_port` if it is not 23) and `emuctl list` reports
a `ready` guest whose address does not accept a connection as `degraded`. The
check connects and closes; it never logs in.
