"""Reject private/credential-bearing paths without ever printing their names."""

import argparse
from pathlib import Path
import re
import subprocess


def check_paths(paths):
    for value in paths:
        name = str(value).replace("\\", "/")
        if (
            re.search(r"(?i)(?:^|[\s/])(?:user|password|dbname|host|sslmode|options)\s*=", name)
            or any(part in {".codex", ".env"} for part in name.split("/"))
            or name.endswith("-private.json")
        ):
            raise ValueError(
                "Private artifact path rejected; inspect locally, do not publish names"
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    try:
        if args.directory:
            check_paths(
                path.relative_to(args.directory)
                for path in args.directory.rglob("*")
                if path.is_file()
            )
        else:
            raw = subprocess.check_output(["git", "ls-files", "-z", "--cached"])
            check_paths(raw.decode("utf-8").split("\0"))
    except ValueError:
        print("Public artifact check failed: private paths present (details withheld)")
        return 1
    print("Public artifact paths verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
