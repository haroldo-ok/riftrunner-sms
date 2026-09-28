#!/usr/bin/env python3
"""Fail a playtest if PSGlib did not emit audible SFX register writes."""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("shots/audio-test.log")
    writes: list[tuple[int, int]] = []
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        print(f"audio check: cannot read {path}: {exc}", file=sys.stderr)
        return 1

    for line in lines:
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            frame = int(fields[0], 10)
            value = int(fields[1], 16)
        except ValueError:
            continue
        if 0 <= value <= 0xFF:
            writes.append((frame, value))

    values = [value for _, value in writes]
    tone = any((value & 0xF0) == 0xC0 for value in values)
    noise = any(0xE0 <= value <= 0xE7 for value in values)
    audible_volume = any(
        (value & 0xF0) in (0xD0, 0xF0) and (value & 0x0F) < 0x0F
        for value in values
    )

    if not writes or not tone or not noise or not audible_volume:
        print(
            "audio check: expected channel-2 tone, noise, and non-muted volume "
            f"writes; got {len(writes)} PSG writes (tone={tone}, noise={noise}, "
            f"audible_volume={audible_volume})",
            file=sys.stderr,
        )
        return 1

    print(
        f"audio check: {len(writes)} PSG writes; tone, noise, and non-muted "
        "volume confirmed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
