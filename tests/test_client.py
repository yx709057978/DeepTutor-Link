import json

import pytest

from deeptutor_link.assets.skill.scripts.deeptutor_link import load, origin, save
from deeptutor_link.cli import main


@pytest.mark.parametrize(
    "url",
    [
        "http://server.example",
        "https://user:password@example.com",
        "https://example.com/api",
        "file:///etc/passwd",
        "https://example.com?token=secret",
    ],
)
def test_rejects_credential_leaking_or_insecure_origins(url):
    with pytest.raises(ValueError):
        origin(url)


def test_private_config_permissions_and_symlink_protection(tmp_path):
    path = tmp_path / "settings/connection.json"
    save(path, {"account_id": "original"})
    assert load(path) == {"account_id": "original"}
    path.chmod(0o644)
    with pytest.raises(ValueError, match="chmod"):
        load(path)
    alias = tmp_path / "alias"
    alias.symlink_to(path)
    with pytest.raises(ValueError):
        save(alias, {})


def test_cli_skill_target_uses_explicit_temporary_directory(tmp_path, capsys):
    target = tmp_path / ".agents/skills"
    assert main(["skill-install", "--target", str(target), "--dry-run"]) == 0
    assert not target.exists()
    assert json.loads(capsys.readouterr().out)["target"] == str(target / "deeptutor-link")
