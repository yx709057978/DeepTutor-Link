import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from deeptutor_link.assets.skill.scripts.deeptutor_link import http_error, load, origin, save
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


@pytest.mark.parametrize(
    ("status", "detail", "expected"),
    [
        (422, {"code": "invalid_request", "errors": [
            {"field": "limit", "le": 50, "input": "PRIVATE"},
            {"field": "SECRET_INPUT", "le": 1},
            {"field": [], "le": 1},
        ], "message": "PRIVATE"}, "limit maximum is 50"),
        (422, [{"input": "PRIVATE"}], "documented argument bounds"),
        (401, {"code": "not_authenticated", "message": "PRIVATE"}, "log in again"),
    ],
)
def test_http_diagnostics_use_safe_constraints_without_private_input(status, detail, expected):
    response = BytesIO(json.dumps({"detail": detail}).encode())
    error = HTTPError("http://127.0.0.1", status, "PRIVATE", {}, response)
    message = str(http_error(error))
    assert expected in message
    assert "PRIVATE" not in message and "SECRET_INPUT" not in message


def test_old_server_pending_endpoint_has_actionable_update_hint():
    error = HTTPError(
        "http://127.0.0.1/api/link/v1/topics/topic/pending-question", 404,
        "Not Found", {}, BytesIO(b'{"detail":"Not Found"}'),
    )
    message = str(http_error(error))
    assert "pending_continuation_unavailable" in message
    assert "0.2.1" in message and "preserve" in message
