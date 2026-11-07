#!/usr/bin/env python3
"""Verify a pinned host checkout or fetch the exact reviewed commit for tests."""

import argparse
import json
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

COMMIT = "f7aed0744c36c5afef16a4261c31ebee1208cac2"
ARCHIVE_URL = f"https://codeload.github.com/HKUDS/DeepTutor/tar.gz/{COMMIT}"


def contract(root: Path) -> dict:
    return json.loads(
        (Path(__file__).resolve().parents[1] / "src/deeptutor_link/assets/host-contract.json").read_text()
    )


def package_root(root: Path) -> Path:
    if (root / "deeptutor/api/main.py").is_file():
        return root / "deeptutor"
    if (root / "api/main.py").is_file():
        return root
    raise SystemExit("Point --source at a DeepTutor checkout or installed deeptutor package.")


def verify(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise SystemExit(f"Host source must be a real directory: {root}")
    value = contract(root)
    if value["version"] != "1.7.1" or value["commit"] != COMMIT:
        raise SystemExit("The bundled support fingerprint is not the reviewed host baseline.")
    for name, expected in value["sha256"].items():
        path = package_root(root) / name
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f"Missing or redirected pinned host file: {name}")
        import hashlib

        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"Pinned host source drifted: {name}")


def fetch(target: Path) -> None:
    if target.exists():
        raise SystemExit(f"Refusing to replace an existing host source: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{target.name}.", dir=target.parent) as temporary:
        temporary = Path(temporary)
        archive = temporary / "DeepTutor.tar.gz"
        with urllib.request.urlopen(ARCHIVE_URL) as response, archive.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        with tarfile.open(archive, "r:gz") as bundle:
            bundle.extractall(temporary, filter="data")
        extracted = sorted(temporary.glob("DeepTutor-*"))
        if len(extracted) != 1 or not extracted[0].is_dir():
            raise SystemExit("The pinned archive did not contain one source root.")
        extracted[0].rename(target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, help="Existing pinned checkout; skips network access")
    parser.add_argument("--target", type=Path, help="Fetch target; defaults to a temporary directory")
    args = parser.parse_args()
    if args.source:
        source = args.source.expanduser().resolve()
        verify(source)
        if (source / "learning/storage.py").is_file():
            source = source.parent
        print(json.dumps({"source": str(source), "acquired": False}))
        return 0
    target = (args.target or Path(tempfile.gettempdir()) / f"DeepTutor-{COMMIT}").expanduser().absolute()
    if target.exists():
        verify(target)
        acquired = False
    else:
        fetch(target)
        verify(target)
        acquired = True
    print(json.dumps({"source": str(target), "acquired": acquired}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
