#!/usr/bin/env python3
"""Fail when the active interpreter does not match nexus_backend/requirements.lock."""
from __future__ import annotations

import importlib.metadata as metadata
import sys
from pathlib import Path

LOCK = Path(__file__).resolve().parents[1] / "nexus_backend" / "requirements.lock"


def parse_lock(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-r ", "--")):
            continue
        if "==" not in line:
            raise SystemExit(f"unsupported lock entry: {line}")
        name, version = line.split("==", 1)
        pins[name.strip().lower().replace("_", "-")] = version.strip()
    return pins


def main() -> int:
    pins = parse_lock(LOCK)
    differences = []
    for name, expected in sorted(pins.items()):
        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError:
            actual = "<missing>"
        if actual != expected:
            differences.append(f"{name}: expected {expected}, actual {actual}")
    print(f"python {sys.version.split()[0]}")
    print(f"lock {LOCK} ({len(pins)} packages)")
    if differences:
        print("environment mismatch:")
        for line in differences:
            print(f"  {line}")
        return 1
    print("environment matches lock")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
