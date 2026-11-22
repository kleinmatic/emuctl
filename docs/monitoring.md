# Monitoring: what the numbers mean

`emuctl list --usage`, the `--json` output of `list` and `status`, and (later)
the web page all show the same numbers. This page says where each one comes
from and how far to trust it.

## Resource use

```
$ emuctl list --usage
GUEST       STATE  UPTIME   CPU%   MEM     DETAIL
vms-alpha   ready  9:12:03  7.4    1.1G
ultrix-vax  ready  2d04h    3.1    61.0M
tru64       down   -        -      -
```

`emuctl list` and `emuctl list --usage` fit in 80 columns, cutting DETAIL
short if they have to. `--node`, `--os` and `--emulator` add those labels;
with any of them the lines may run longer. `--json` always has every field.

| Field (`--json`) | Column | Meaning | Source |
|---|---|---|---|
| `cpu_seconds` | | CPU time used since the guest started, in seconds | `cpu.stat` in the guest's cgroup |
| `cpu_pct` | CPU% | CPU used over a one-second sample, as a percentage of **one** CPU. 100 means a whole core; a guest can go above 100 if its emulator uses several threads | two `cpu_seconds` readings (only with `--usage`) |
| `mem_bytes` | MEM | memory in use now, including the emulator's file cache | `memory.current` |
| `mem_peak_bytes` | | the most memory used since the guest started | `memory.peak` (kernel 5.19 or newer) |
| `tasks` | | processes and threads in the guest's cgroup | `pids.current` |
| `io_read_bytes`, `io_write_bytes` | | bytes read from and written to block devices since start; reads served from the host's cache are not counted. Summed over every device in `io.stat`, so on stacked storage (LVM, LUKS, md) the same I/O can be counted at each layer | `io.stat` |
| `disk_bytes` | | space the guest's disk images really take on the host | `stat()`: blocks × 512 |
| `disk_nominal_bytes` | | the images' full size; larger than `disk_bytes` when an image is sparse | `stat()`: size |
| `net_rx_bytes`, `net_tx_bytes` | | bytes the guest has received and sent over all its host interfaces, since each interface was created | `/sys/class/net/<if>/statistics` |

The cgroup numbers cover everything in the guest's systemd unit: the
supervisor, the emulator, and anything they start, such as a `stop_command`.
The supervisor's own share is small.

## When a number is missing

A number is shown as `-` (or `null` in JSON) when it cannot be read:

- The guest is not running. Disk numbers are still shown.
- The host does not use systemd, or the guest was not started through it.
  See [platforms.md](platforms.md).
- The unit does not have accounting turned on. The unit file shipped with
  emuctl turns on CPU, memory, task and I/O accounting. If you installed an
  older unit file, add `IOAccounting=yes` and the others in a drop-in.
- The user running `emuctl` cannot read a disk image's directory.

Network detail per interface, and the bridges, are in `emuctl net`; see
[networking.md](networking.md).

## Cost

`emuctl list` makes one `systemctl show` call for all running guests and
reads a few small files per guest. `--usage` adds a one-second wait so it can
work out CPU%.
