import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_crash_recovery import paused_child

from deeptutor_link.installer import (
    MARKER,
    PATCHES,
    RECOVERY,
    inspect_host,
    patch_host,
    setup_skill,
)


@pytest.fixture
def host_copy(tmp_path):
    from conftest import SOURCE
    if SOURCE is None:
        pytest.skip("Set DEEPTUTOR_SOURCE to the pinned host checkout.")

    contract = json.loads(
        (Path(__file__).parents[1] / "src/deeptutor_link/assets/host-contract.json").read_text()
    )
    target = tmp_path / "host/deeptutor"
    for name in contract["sha256"]:
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / "deeptutor" / name, path)
    return target


def test_dry_run_idempotence_and_exact_uninstall(host_copy):
    main = host_copy / "api/main.py"
    before = {name: (host_copy / name).read_bytes() for name in PATCHES}
    assert not patch_host(host_copy, dry_run=True)["patched"]
    assert all((host_copy / name).read_bytes() == data for name, data in before.items())
    assert not (host_copy / ".deeptutor-link-install.json").exists()
    assert patch_host(host_copy)["patched"]
    assert patch_host(host_copy)["patched"]
    assert main.read_bytes().count(MARKER) == 1
    for name, patches in PATCHES.items():
        data = (host_copy / name).read_bytes()
        assert all(data.count(marker) == 1 for marker, _ in patches)
        compile(data, str(host_copy / name), "exec")
    assert not patch_host(host_copy, uninstall=True)["patched"]
    patch_host(host_copy, uninstall=True)
    assert all((host_copy / name).read_bytes() == data for name, data in before.items())


def test_reinstall_preserves_exact_recovery_and_refuses_local_changes(host_copy):
    from deeptutor_link.installer import recovery_script

    current = recovery_script()
    patch_host(host_copy)
    recovery = host_copy / RECOVERY
    assert patch_host(host_copy)["patched"]
    assert recovery.read_bytes() == current
    recovery.write_bytes(current + b"# local customization\n")
    with pytest.raises(ValueError, match="Recovery script drifted"):
        patch_host(host_copy)


@pytest.mark.parametrize("uninstall", [False, True])
def test_sigkill_multifile_installer_recovers(host_copy, uninstall):
    before = {name: (host_copy / name).read_bytes() for name in PATCHES}
    if uninstall:
        patch_host(host_copy)
    source = """
import sys, threading
from pathlib import Path
import deeptutor_link.installer as installer
package = Path(sys.argv[1])
uninstall = sys.argv[2] == 'True'
write = installer.atomic_write
def paused(path, data, mode=0o600):
    target = 'learning/service.py' if uninstall else 'api/main.py'
    if path == package / target:
        print('READY', flush=True)
        threading.Event().wait()
    write(path, data, mode)
installer.atomic_write = paused
installer.patch_host(package, uninstall=uninstall)
"""
    with paused_child(source, host_copy, uninstall) as child:
        with pytest.raises(ValueError, match="installer|lock"):
            patch_host(host_copy)
        child.kill()
        child.wait(timeout=10)
        assert patch_host(host_copy, uninstall=uninstall)["patched"] is not uninstall
    patch_host(host_copy, uninstall=True)
    assert all((host_copy / name).read_bytes() == data for name, data in before.items())


@pytest.mark.parametrize("uninstall", [False, True])
def test_interrupted_multifile_patch_recovers(host_copy, monkeypatch, uninstall):
    import deeptutor_link.installer as installer

    before = {name: (host_copy / name).read_bytes() for name in PATCHES}
    if uninstall:
        patch_host(host_copy)
    original_write = installer.atomic_write

    def interrupted(path, data, mode=0o600):
        if path == host_copy / "learning/service.py" and uninstall:
            raise OSError("Synthetic interrupted uninstall")
        if path == host_copy / "api/main.py" and not uninstall:
            raise OSError("Synthetic interrupted install")
        original_write(path, data, mode)

    monkeypatch.setattr(installer, "atomic_write", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        patch_host(host_copy, uninstall=uninstall)
    assert inspect_host(host_copy)["patched_files"] == ["learning/service.py"]
    assert not (host_copy / ".deeptutor-link-install.lock").exists()
    monkeypatch.setattr(installer, "atomic_write", original_write)
    assert patch_host(host_copy, uninstall=uninstall)["patched"] is not uninstall
    patch_host(host_copy, uninstall=True)
    assert all((host_copy / name).read_bytes() == data for name, data in before.items())


@pytest.mark.parametrize("state", ["complete", "service_only", "main_only", "original"])
def test_offline_recovery_without_package_or_site_imports(host_copy, tmp_path, state):
    before = {name: (host_copy / name).read_bytes() for name in PATCHES}
    patch_host(host_copy)
    restore = {
        "service_only": ["api/main.py"],
        "main_only": ["learning/service.py"],
        "original": list(PATCHES),
        "complete": [],
    }
    for name in restore[state]:
        (host_copy / name).write_bytes(before[name])
    inode = (host_copy / ".deeptutor-link-install.flock").stat().st_ino
    for args in (["--dry-run"], [], []):
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                str(host_copy / RECOVERY),
                "--host",
                str(host_copy),
                *args,
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
    assert all((host_copy / name).read_bytes() == data for name, data in before.items())
    assert (host_copy / ".deeptutor-link-install.flock").stat().st_ino == inode


def test_offline_recovery_refuses_drift_before_any_write(host_copy, tmp_path):
    patch_host(host_copy)
    target = host_copy / "learning/service.py"
    target.write_bytes(target.read_bytes() + b"# user change\n")
    before = {name: (host_copy / name).read_bytes() for name in PATCHES}
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(host_copy / RECOVERY), "--host", str(host_copy)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1
    assert "drifted" in result.stderr
    assert all((host_copy / name).read_bytes() == data for name, data in before.items())


def test_offline_recovery_after_sigkill_during_uninstall(host_copy, tmp_path):
    before = {name: (host_copy / name).read_bytes() for name in PATCHES}
    patch_host(host_copy)
    source = """
import os, runpy, sys, threading
from pathlib import Path
package = Path(sys.argv[1])
replace = os.replace
def paused(src, dst):
    if Path(dst) == package / 'learning/service.py':
        print('READY', flush=True)
        threading.Event().wait()
    replace(src, dst)
os.replace = paused
sys.argv = [str(package / '.deeptutor-link-recover.py'), '--host', str(package)]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    with paused_child(source, host_copy) as child:
        assert inspect_host(host_copy)["patched_files"] == ["learning/service.py"]
        child.kill()
        child.wait(timeout=10)
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(host_copy / RECOVERY), "--host", str(host_copy)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert all((host_copy / name).read_bytes() == data for name, data in before.items())


def test_unsupported_platform_refused_before_writes(host_copy, monkeypatch):
    monkeypatch.setattr("deeptutor_link.locking.sys.platform", "win32")
    with pytest.raises(ValueError, match="macOS/Linux"):
        patch_host(host_copy)
    assert not (host_copy / ".deeptutor-link-install.flock").exists()


def test_legacy_directory_lock_is_never_guessed_stale(host_copy):
    lock = host_copy / ".deeptutor-link-install.lock"
    lock.mkdir()
    with pytest.raises(ValueError, match="Legacy installer lock"):
        patch_host(host_copy)
    assert lock.is_dir()


def test_mount_requires_complete_host_guards_and_is_idempotent(host_copy, monkeypatch):
    import deeptutor
    from fastapi import APIRouter, FastAPI

    from deeptutor_link import server

    monkeypatch.setattr(deeptutor, "__file__", str(host_copy / "__init__.py"))
    monkeypatch.setattr(server, "build_router", APIRouter)
    app = FastAPI()
    with pytest.raises(ValueError, match="repair guards are required"):
        server.mount(app)
    patch_host(host_copy)
    server.mount(app)
    count = len(app.routes)
    server.mount(app)
    assert len(app.routes) == count
    assert app.state.link_extension_mounted


def test_drift_is_refused_without_writes(host_copy):
    service = host_copy / "learning/service.py"
    service.write_bytes(service.read_bytes() + b"# modified")
    with pytest.raises(ValueError, match="drifted"):
        patch_host(host_copy)
    assert not (host_copy / ".deeptutor-link-install.json").exists()


def test_interrupted_install_recovers_and_receipt_tamper_refused(host_copy):
    patch_host(host_copy)
    receipt = host_copy / ".deeptutor-link-install.json"
    receipt.unlink()
    assert patch_host(host_copy)["patched"]
    receipt.write_text("{}")
    with pytest.raises(ValueError, match="receipt drifted"):
        patch_host(host_copy, uninstall=True)


def test_skill_uses_temporary_agents_directory_and_preserves_modified_file(tmp_path):
    target = tmp_path / ".agents/skills"
    setup_skill(target, dry_run=True)
    assert not target.exists()
    setup_skill(target)
    setup_skill(target)
    path = target / "deeptutor-link/SKILL.md"
    assert path.exists()
    assert (target / "deeptutor-link/scripts/deeptutor_link.py").is_file()
    guide = target / "deeptutor-link/references/setup.md"
    assert guide.is_file()
    setup_skill(target, uninstall=True)
    assert not path.parent.exists()
    setup_skill(target, uninstall=True)
    setup_skill(target)
    path.write_text("user edited")
    guide.write_text("user setup notes")
    (path.parent / "custom-notes.txt").write_text("user custom content")
    with pytest.raises(ValueError, match="changed locally"):
        setup_skill(target)
    removed = setup_skill(target, uninstall=True)
    assert removed["preserved_modified_files"] == ["SKILL.md", "references/setup.md"]
    assert path.read_text() == "user edited"
    assert guide.read_text() == "user setup notes"
    assert (path.parent / "custom-notes.txt").read_text() == "user custom content"
