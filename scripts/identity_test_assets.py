"""Pinned public identity test assets; never credentials or production services."""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config/test_identity_assets.json"


def asset_contract(*, system=None, machine=None):
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if value["schema"] != "identity-test-assets-v1":
        raise ValueError("unsupported asset contract")
    if system is not None and (system != "Linux" or machine.lower() not in {"amd64", "x86_64"}):
        raise ValueError("unsupported identity test host")
    for key in ("archive_sha256", "binary_sha256"):
        if not re.fullmatch("[a-f0-9]{64}", value[key]):
            raise ValueError("invalid asset digest")
    if not re.fullmatch("[a-f0-9]{40}", value["source_commit"]):
        raise ValueError("invalid source commit")
    if (
        value["archive_url"]
        != (
            f"https://github.com/supabase/auth/releases/download/{value['release']}/auth-{value['release']}-amd64.tar.xz"
        )
        or value["source_url"] != "https://github.com/supabase/auth.git"
    ):
        raise ValueError("unapproved asset origin")
    return value


def verify_payload(payload):
    if hashlib.sha256(payload).hexdigest() != asset_contract()["binary_sha256"]:
        raise ValueError("identity binary digest mismatch")
    if payload[:6] != b"\x7fELF\x02\x01" or payload[18:20] != b"\x3e\x00":
        raise ValueError("unsupported identity binary architecture")


def verify_binary(path):
    verify_payload(Path(path).read_bytes())


def extract_verified(archive, output):
    archive, output = Path(archive), Path(output)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != asset_contract()["archive_sha256"]:
        raise ValueError("identity archive digest mismatch")
    with tarfile.open(archive) as bundle:
        members = [m for m in bundle.getmembers() if m.isfile() and Path(m.name).name == "auth"]
        if len(members) != 1 or not 0 < members[0].size <= 128 * 1024 * 1024:
            raise ValueError("invalid identity archive")
        payload = bundle.extractfile(members[0]).read()
    verify_payload(payload)
    with output.open("xb") as stream:
        stream.write(payload)
    output.chmod(0o700)


def fetch(directory):
    contract = asset_contract(system=platform.system(), machine=platform.machine())
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    archive = directory / "auth.tar.xz"
    with (
        urllib.request.urlopen(contract["archive_url"], timeout=60) as response,
        archive.open("xb") as out,
    ):
        remaining = 64 * 1024 * 1024
        while block := response.read(min(1024 * 1024, remaining + 1)):
            remaining -= len(block)
            if remaining < 0:
                raise ValueError("identity archive too large")
            out.write(block)
    extract_verified(archive, directory / "auth")
    source = directory / "source"
    for command in (
        ["git", "init", str(source)],
        ["git", "-C", str(source), "remote", "add", "origin", contract["source_url"]],
        ["git", "-C", str(source), "fetch", "--depth=1", "origin", contract["source_commit"]],
        ["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"],
    ):
        subprocess.run(command, check=True, capture_output=True, timeout=120)
    shutil.copytree(source / "migrations", directory / "migrations")
    (directory / "asset-contract.json").write_text(json.dumps(contract, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["fetch", "verify"])
    parser.add_argument("--directory", required=True)
    args = parser.parse_args()
    try:
        if args.command == "fetch":
            fetch(args.directory)
        else:
            verify_binary(Path(args.directory) / "auth")
        print(json.dumps({"status": "passed", "operation": args.command}))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {"status": "failed", "operation": args.command, "exception": type(error).__name__}
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
