# Appended to locking.py and the exact patch manifest by the installer.
# This file and the generated script use only Python's standard library.
# ruff: noqa: F821
import argparse
import base64
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path


def recover(host, dry_run=False):
    require_lock_support()
    package = Path(host).expanduser().absolute()
    if (package / "deeptutor/api/main.py").is_file():
        package /= "deeptutor"
    if package.is_symlink() or any(p.is_symlink() for p in package.parents):
        raise ValueError("Resolve host aliases first.")
    if (package / ".deeptutor-link-install.lock").exists():
        raise ValueError("Legacy installer lock exists; stop old installers before removing it.")
    with advisory_lock(package / ".deeptutor-link-install.flock"):
        originals, record_files = {}, {}
        for name, expected in MANIFEST["contract"]["sha256"].items():
            target = package / name
            if target.is_symlink() or any(p.is_symlink() for p in target.parents):
                raise ValueError(f"Redirected host file: {name}")
            current = target.read_bytes()
            original = current
            pairs = [
                (base64.b64decode(m), base64.b64decode(a))
                for m, a in MANIFEST["patches"].get(name, [])
            ]
            for marker, _ in pairs:
                if original.count(marker) > 1:
                    raise ValueError(f"Duplicate marker: {name}")
                original = original.replace(marker, b"")
            if hashlib.sha256(original).hexdigest() != expected:
                raise ValueError(f"Unsupported or drifted host file: {name}")
            if pairs:
                patched = original
                for marker, anchor in pairs:
                    if patched.count(anchor) != 1:
                        raise ValueError(f"Invalid anchor: {name}")
                    patched = patched.replace(anchor, marker + anchor)
                if current not in (original, patched):
                    raise ValueError(f"Incomplete or misplaced patch: {name}")
                originals[name] = (target, original, current)
                record_files[name] = {
                    "original_sha256": expected,
                    "patched_sha256": hashlib.sha256(patched).hexdigest(),
                }
        receipt = package / ".deeptutor-link-install.json"
        if receipt.is_symlink():
            raise ValueError("Redirected installation receipt.")
        if receipt.exists() and json.loads(receipt.read_text()) != {
            "schema": 2,
            "version": "0.1.0",
            "files": record_files,
        }:
            raise ValueError("Installation receipt drifted.")
        # Complete every validation before the first write; main is restored first.
        if not dry_run:
            for target, original, current in originals.values():
                if original == current:
                    continue
                fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(original)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.chmod(temporary, stat.S_IMODE(target.stat().st_mode))
                    os.replace(temporary, target)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            receipt.unlink(missing_ok=True)
        return {"action": "uninstall", "dry_run": dry_run, "package": str(package)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Exact offline DeepTutor-Link patch recovery.")
    parser.add_argument("--host", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(recover(args.host, args.dry_run)))
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        raise SystemExit(1)
