"""Which of a node's series may be added together.

A roll-up that sums every series of a metric is only right when the series
describe **disjoint** things. Three of these metrics split on an attribute whose
values overlap, and adding them across that attribute counts the same bytes
twice:

* `system.disk.io` splits on `device`, and `/proc/diskstats` lists a partition
  beside the disk that contains it — `vda` and `vda1` carry identical counters
  on a single-partition disk.
* `system.network.io` splits on the interface, and loopback is the same bytes
  transmitted and received on one interface, none of which left the machine.
* `system.filesystem.usage` splits on the mountpoint, and one device bind-mounted
  at two paths is one filesystem's bytes reported twice.

Nothing in the semantic conventions marks any of this: `system.device` is only
"the device identifier" and `lo` appears as an ordinary example of an interface
name, so the convention does not settle it and these rules are read off the
producers instead. Each is a **name** test, which is why they live here as small
pure functions with the cases they get wrong written down beside them.

**Which way the rules err.** All three only ever remove a duplicate they can
recognise, so what they cannot recognise stays double-counted: an LVM volume
over its own partition, an md RAID array over its members, and macOS or Windows
device spellings are all left summed as they are today. They over-report rather
than under-report, and the only way they can under-report is a false positive —
a genuinely separate device named exactly as a partition of another.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

_DIGITS: Final = frozenset("0123456789")

# The Linux kernel inserts this before the partition number when a disk's own
# name ends in a digit, so nvme0n1's first partition is `nvme0n1p1` and the
# ambiguous `nvme0n11` never occurs.
_PARTITION_SEPARATOR: Final = "p"

# Loopback under the two spellings a producer sends: `lo` on Linux, which is the
# semantic conventions' own example of an interface name, and `lo0` on macOS and
# the BSDs. Windows reports a friendly name we have never received and therefore
# do not match — its loopback is still double-counted.
_LOOPBACK_INTERFACES: Final = frozenset({"lo", "lo0"})


def whole_disks(device_names: Iterable[str]) -> frozenset[str]:
    """The reported disks that are not a partition of another reported disk.

    A partition's bytes are its disk's bytes, so a sum over both counts them
    twice. A partition whose disk was *not* reported is kept: its traffic is
    real and nothing here duplicates it.
    """
    named = {name for name in device_names if name}
    return frozenset(
        name for name in named if not any(_is_partition_of(name, disk) for disk in named)
    )


def is_loopback(interface_name: str) -> bool:
    """Whether an interface's traffic never left the machine."""
    return interface_name.strip().lower() in _LOOPBACK_INTERFACES


def one_mountpoint_per_device(
    device_mountpoints: Iterable[tuple[str, str]],
) -> frozenset[tuple[str, str]]:
    """One mountpoint per device, so a filesystem mounted twice is counted once.

    A pair naming no device is kept as it stands: two tmpfs mounts are genuinely
    two filesystems, and nothing in a blank device says otherwise.
    """
    representative: dict[str, str] = {}
    unattributable: set[tuple[str, str]] = set()

    for device, mountpoint in device_mountpoints:
        if not device:
            unattributable.add((device, mountpoint))
        elif device not in representative or _nearer_the_root(mountpoint, representative[device]):
            representative[device] = mountpoint

    return frozenset(representative.items()) | unattributable


def _nearer_the_root(mountpoint: str, than: str) -> bool:
    """Which of two mounts of one device to keep — shortest path, then by name.

    Both describe the same bytes, so any choice is as correct as any other; this
    one is deterministic and picks `/` over `/var`.
    """
    return (len(mountpoint), mountpoint) < (len(than), than)


def _is_partition_of(device: str, disk: str) -> bool:
    """Whether the kernel's partition naming says `device` sits inside `disk`.

    `sda` + digits, and `nvme0n1` + `p` + digits when the disk's name already
    ends in a digit. The `p` is what keeps `md11` a separate array rather than a
    partition of `md1`, and `sdaa` a separate disk rather than a partition of
    `sda`.
    """
    if device == disk or not disk or not device.startswith(disk):
        return False

    suffix = device[len(disk) :]
    if disk[-1] in _DIGITS:
        return suffix.startswith(_PARTITION_SEPARATOR) and _is_number(suffix[1:])
    return _is_number(suffix)


def _is_number(text: str) -> bool:
    """ASCII digits only — `str.isdigit` accepts superscripts and other scripts."""
    return bool(text) and all(character in _DIGITS for character in text)
