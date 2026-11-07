"""Small, fingerprinted patch. Installation never opens a host data store."""

import base64
import hashlib
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path

from deeptutor_link.locking import LockBusy, advisory_lock, require_lock_support

LF = bytes([10])
MARKER = (
    LF
    + LF.join(
        [
            b"# BEGIN DEEPTUTOR-LINK 0.1.0",
            b"from deeptutor_link.server import mount as _mount_link",
            b"_mount_link(app)",
            b"# END DEEPTUTOR-LINK",
        ]
    )
    + LF
)
ANCHOR = LF + b'if __name__ == "__main__":' + LF
RECEIPT = ".deeptutor-link-install.json"
RECOVERY = ".deeptutor-link-recover.py"


def repair_marker(indent: int, label: str, progress: str) -> bytes:
    from deeptutor_link.repair import guard_source

    padding = b" " * indent
    return (
        LF.join(
            padding + line
            for line in [
                f"# BEGIN DEEPTUTOR-LINK REPAIR {label}".encode(),
                *[line.encode() for line in guard_source(progress).splitlines()],
                f"# END DEEPTUTOR-LINK REPAIR {label}".encode(),
                b"",
            ]
        )
        + LF
    )


PATCHES = {
    "api/main.py": [(MARKER, ANCHOR)],
    "learning/service.py": [
        (
            repair_marker(12, "TRANSACTION", "tx.progress"),
            b"            question = interaction.question if interaction is not None else pending"
            + LF,
        ),
        (
            repair_marker(8, "SEMANTIC", "progress"),
            b"        question = interaction.question.model_copy(deep=True)" + LF,
        ),
    ],
}


def original_bytes(name: str, data: bytes) -> bytes:
    for marker, _ in PATCHES.get(name, []):
        if data.count(marker) > 1:
            raise ValueError("Duplicate patch markers. Restore the original before installing.")
        data = data.replace(marker, b"")
    return data


def patched_bytes(name: str, data: bytes) -> bytes:
    for marker, anchor in PATCHES[name]:
        if data.count(anchor) != 1:
            raise ValueError(f"Expected one version-specific patch anchor: {name}")
        data = data.replace(anchor, marker + anchor)
    return data


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def host_package(path: Path) -> Path:
    path = path.expanduser().absolute()
    if (path / "deeptutor" / "api" / "main.py").is_file():
        path = path / "deeptutor"
    if not (path / "api" / "main.py").is_file():
        raise ValueError("Point --host at the source checkout or installed deeptutor package.")
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError("Resolve host path aliases before installation.")
    return path


def inspect_host(path: Path) -> dict:
    require_lock_support()
    package = host_package(path)
    contract = json.loads(
        files("deeptutor_link").joinpath("assets/host-contract.json").read_text()
    )
    patched_files = []
    for name, expected in contract["sha256"].items():
        target = package / name
        if (
            target.is_symlink()
            or any(p.is_symlink() for p in target.parents)
            or not target.is_file()
        ):
            raise ValueError(f"Missing/redirected host contract file: {name}")
        data = target.read_bytes()
        original = original_bytes(name, data)
        if sha(original) != expected:
            raise ValueError(f"Unsupported or drifted DeepTutor source: {name}")
        if name in PATCHES and data != original:
            if data != patched_bytes(name, original):
                raise ValueError(f"Incomplete or misplaced extension patch: {name}")
            patched_files.append(name)
    return {
        "host_version": contract["version"],
        "baseline_commit": contract["commit"],
        "package": str(package),
        "patched": len(patched_files) == len(PATCHES),
        "patched_files": patched_files,
        "contract_files": len(contract["sha256"]),
    }


def atomic_write(path: Path, data: bytes, mode: int = 0o600):
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def install_lock(package: Path):
    # The previous directory mutex cannot prove a live/dead owner. Refuse it
    # until an administrator has stopped all older installers and removed it.
    if (package / ".deeptutor-link-install.lock").exists():
        raise ValueError(
            "Legacy installer lock exists; stop all old installers before removing it."
        )
    try:
        with advisory_lock(package / ".deeptutor-link-install.flock"):
            yield
    except LockBusy as exc:
        raise ValueError("Another installer holds the installation lock.") from exc


def recovery_script() -> bytes:
    root = files("deeptutor_link")
    manifest = {
        "contract": json.loads(root.joinpath("assets/host-contract.json").read_text()),
        "patches": {
            name: [[base64.b64encode(x).decode() for x in pair] for pair in pairs]
            for name, pairs in PATCHES.items()
        },
    }
    return (
        root.joinpath("locking.py").read_text()
        + "\nMANIFEST = "
        + repr(manifest)
        + "\n"
        + root.joinpath("assets/recover_host.py").read_text()
    ).encode()


def patch_host(path: Path, *, dry_run: bool = False, uninstall: bool = False) -> dict:
    package = host_package(path)
    if dry_run:
        return {
            **inspect_host(package),
            "action": "uninstall" if uninstall else "install",
            "dry_run": True,
        }
    with install_lock(package):
        inspect_host(package)
        receipt = package / RECEIPT
        if receipt.is_symlink():
            raise ValueError("Installation receipt must not be a symbolic link.")
        contents = {}
        for name in PATCHES:
            current = (package / name).read_bytes()
            original = original_bytes(name, current)
            contents[name] = (current, original, patched_bytes(name, original))
        record = {
            "schema": 2,
            "files": {
                name: {
                    "original_sha256": sha(original),
                    "patched_sha256": sha(patched),
                }
                for name, (_, original, patched) in contents.items()
            },
            "version": "0.1.0",
        }
        if receipt.exists() and json.loads(receipt.read_text()) != record:
            raise ValueError("Installation receipt drifted. Inspect before changing source.")
        if uninstall:
            for name, (current, original, _) in contents.items():
                path = package / name
                if current != original:
                    atomic_write(path, original, stat.S_IMODE(path.stat().st_mode))
            receipt.unlink(missing_ok=True)
        else:
            recovery = package / RECOVERY
            script = recovery_script()
            if recovery.is_symlink() or (
                recovery.exists()
                and recovery.read_bytes() != script
            ):
                raise ValueError("Recovery script drifted; preserve and inspect it first.")
            atomic_write(recovery, script, 0o700)
            atomic_write(receipt, json.dumps(record, indent=2).encode() + LF)
            # Guard old records before exposing writes; the receipt allows recovery
            # after interruption between the two atomic file replacements.
            for name in reversed(PATCHES):
                current, _, patched = contents[name]
                path = package / name
                if current != patched:
                    atomic_write(path, patched, stat.S_IMODE(path.stat().st_mode))
        return {
            **inspect_host(package),
            "action": "uninstall" if uninstall else "install",
            "dry_run": False,
        }


def setup_skill(target: Path, *, uninstall: bool = False, dry_run: bool = False) -> dict:
    target = target.expanduser().absolute() / "deeptutor-link"
    root = files("deeptutor_link").joinpath("assets/skill")
    owned = {
        name: root.joinpath(name).read_bytes()
        for name in (
            "SKILL.md",
            "scripts/deeptutor_link.py",
            "references/setup.md",
            "references/workflows.md",
        )
    }
    if target.is_symlink():
        raise ValueError("Skill target must not be a symbolic link.")
    preserved = []
    for name, content in owned.items():
        path = target / name
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise ValueError("Skill target must not contain symbolic links.")
        if path.exists() and path.read_bytes() != content:
            if not uninstall:
                raise ValueError("Skill was changed locally; preserve or remove it manually.")
            preserved.append(name)
    if not dry_run:
        if uninstall:
            for name in owned:
                if name not in preserved:
                    (target / name).unlink(missing_ok=True)
            for directory in (target / "scripts", target / "references", target):
                if directory.is_dir() and not any(directory.iterdir()):
                    directory.rmdir()
        else:
            target.mkdir(parents=True, exist_ok=True)
            for name, content in owned.items():
                path = target / name
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(path, content, 0o755 if name.endswith(".py") else 0o644)
    return {
        "target": str(target),
        "action": "uninstall" if uninstall else "install",
        "dry_run": dry_run,
        "files": list(owned),
        "preserved_modified_files": preserved,
    }
