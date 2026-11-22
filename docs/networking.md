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

## The health check

Set `guest_addr` (and `guest_port` if it is not 23) and `emuctl list` reports
a `ready` guest whose address does not accept a connection as `degraded`. The
check connects and closes; it never logs in.
