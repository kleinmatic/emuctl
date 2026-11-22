# gxemul

These notes come from one guest: Ultrix 4.5 on an emulated DECstation
5000/200, using gxemul 1.0.13 from Rico Pajarola's fork.

## Console

gxemul puts a serial port on a fixed telnet port (4000 for the first). emuctl
can hold it and use a getty's `login:` there as the `ready` signal.

With the graphics display on, this gxemul turns serial input into keyboard
scan codes, so nothing useful can be typed on the serial console. The PROM and
the kernel also print to the screen, not the serial port. That rules out boot
and stop stages.

## Shutting the guest down

Use a `stop_command`. Ours telnets to the guest's own address, logs in, runs
`su` and `/etc/shutdown -h now`, then asks gxemul's control socket
(`--control unix:<path>` when gxemul starts, `gxemul --ctl <path> status` to
ask) for the CPU's program counter. On a DECstation the PROM runs in kseg1
(addresses starting `0xa0` or `0xbf`) and Ultrix does not, so three readings in
a row there mean the guest has halted. The script then exits 0.

The conf has no `halted` pattern, because nothing on the serial console shows
the halt. emuctl trusts the command's exit status instead.
