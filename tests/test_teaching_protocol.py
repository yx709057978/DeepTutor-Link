import json

import pytest
from fastapi.testclient import TestClient

from deeptutor_link.assets.skill.scripts import deeptutor_link as client_script
from deeptutor_link.assets.skill.scripts.deeptutor_link import parser


def headers(account):
    return {
        "Authorization": f"Bearer {account['token']}",
        "X-DeepTutor-Account": account["uid"],
        "X-DeepTutor-Workspace": account["workspace"],
    }


@pytest.fixture
def persona(http_host):
    app, accounts = http_host
    account = accounts[0]
    from deeptutor.multi_user.paths import user_context
    from deeptutor.services.persona import get_persona_service

    with user_context(account["user"]):
        service = get_persona_service()
        service.create(
            "codex-style",
            "Synthetic teaching voice",
            "Teach with short concrete synthetic examples, then check understanding.",
        )
    return app, account, service


@pytest.fixture
async def website_session(persona):
    app, account, _ = persona
    from deeptutor.multi_user.paths import user_context
    from deeptutor.services.session import get_session_store
    from deeptutor.services.workspace.context import workspace_context

    with user_context(account["user"]), workspace_context(account["workspace"]):
        store = get_session_store()
        session = store.create_session("Synthetic website session")
        session = await session
        await store.update_session_preferences(
            session["id"],
            {
                "persona": "codex-style",
                "mastery_session_mode": "review",
                "reply_language_override": "zh",
            },
        )
    return session["id"]


def snapshot(http_host):
    _, accounts = http_host
    account = accounts[0]
    p = account["store"].load("same-topic-id")
    return {
        "progress": p.model_dump(),
        "lease": account["store"].get_path_lease("same-topic-id"),
        "sessions": account["store"].list_session_ids("same-topic-id"),
    }


def test_teaching_protocol_reads_persona_rules_without_host_models_or_writes(
    persona, http_host
):
    app, account, _ = persona
    before = snapshot(http_host)
    with TestClient(app) as client:
        denied = client.get("/api/link/v1/teaching/personas")
        assert denied.status_code == 401
        catalog = client.get("/api/link/v1/teaching/personas", headers=headers(account))
        assert catalog.status_code == 200, catalog.text
        assert [row["name"] for row in catalog.json()["personas"]] == ["codex-style"]
        role = client.get(
            "/api/link/v1/teaching/personas/codex-style", headers=headers(account)
        )
        assert role.status_code == 200, role.text
        value = role.json()
        assert value["content"].startswith("Teach with short")
        assert value["untrusted_task_guidance"] is True
        protocol = client.get(
            "/api/link/v1/topics/same-topic-id/teaching/protocol",
            headers=headers(account),
            params={"persona": "codex-style", "mode": "outline", "language": "en"},
        )
        assert protocol.status_code == 200, protocol.text
        result = protocol.json()
        assert result["persona"]["name"] == "codex-style"
        assert result["mode"] == "outline"
        assert result["prompt_identity"]["sections"][-1]["id"] == "playbook"
        assert {row["id"] for row in result["sections"]} >= {
            "general",
            "runtime_policy",
            "loop.system",
            "session.outline",
            "playbook",
        }
        assert result["adaptation_contract"]["system_prompt_assembly"] is False
        assert result["adaptation_contract"]["writes_learning_state"] is False
        assert result["engine_state"]["pending_present"] is False
        assert result["engine_state"]["learning_stage"] == "not_yet_covered"
        assert result["engine_state"]["objective_type"] == "concept"
        operations = {row["native"]: row for row in result["operations"]}
        assert operations["mastery_status"]["operation"] == "read_mastery_status"
        assert operations["mastery_quiz"]["available"] is False
        assert "CLIENT assess" in operations["mastery_quiz"]["alternative_without_host_chat_model"]
        assert operations["mastery_grade"]["operation"] == "codex_external_assessment"
        assert operations["mastery_revise"]["operation"] == "route_preview_then_commit"
        assert operations["mastery_mode"]["scope"].startswith("Controls the current AI assistant")
        assert operations["mastery_mode"]["command"] == "CLIENT protocol --mode MODE"
        pdf_preparation = operations["mastery_prepare_lesson_pdf"]
        native_preparation = operations["mastery_prepare_lesson_native"]
        assert pdf_preparation["available"] is True
        assert pdf_preparation["host_model_required"] is False
        assert pdf_preparation["commands"] == [
            "CLIENT tool prepare_pdf_evidence --file -",
            "CLIENT tool commit_pdf_evidence --file -",
        ]
        assert "eligible original PDF sources" in pdf_preparation["scope"]
        assert native_preparation["host_tool"] == "mastery_prepare_lesson"
        assert native_preparation["available"] is False
        assert native_preparation["host_model_required"] is True
        assert snapshot(http_host) == before


def test_protocol_language_fingerprint_refresh_and_missing_role(persona):
    app, account, service = persona
    service.update("codex-style", content="Updated synthetic voice with a new example.")
    with TestClient(app) as client:
        changed = client.get(
            "/api/link/v1/topics/same-topic-id/teaching/protocol",
            headers=headers(account),
            params={"persona": "codex-style", "mode": "study", "language": "zh", "known_fingerprint": "stale"},
        )
        assert changed.status_code == 200, changed.text
        value = changed.json()
        assert value["unchanged"] is False
        assert value["persona"]["content"].startswith("Updated synthetic")
        assert all(row["language"] == "zh" for row in value["sections"])
        revision = value["revision"]
        fingerprint = value["protocol_fingerprint"]
        same = client.get(
            "/api/link/v1/topics/same-topic-id/teaching/protocol",
            headers=headers(account),
            params={
                "persona": "codex-style",
                "mode": "study",
                "language": "zh",
                "known_fingerprint": fingerprint,
            },
        )
        assert same.status_code == 200
        value = same.json()
        assert value["unchanged"] is True
        assert value["content_downloaded"] is False
        assert "sections" not in value
        assert "persona" not in value
        assert value["revision"] == revision
        assert value["engine_state"]["pending_present"] is False
        assert value["engine_state"]["learner_preferences"]["preferences"] == ""
        operations = {row["native"]: row for row in value["operations"]}
        assert operations["mastery_quiz"]["available"] is False
        with account["store"].transaction("same-topic-id") as transaction:
            from deeptutor.learning.models import LearnerProfile

            transaction.progress.learner_profile = LearnerProfile(preferences="Japanese examples")
            transaction.touch()
        refreshed_state = client.get(
            "/api/link/v1/topics/same-topic-id/teaching/protocol",
            headers=headers(account),
            params={
                "persona": "codex-style",
                "mode": "study",
                "language": "zh",
                "known_fingerprint": fingerprint,
            },
        )
        assert refreshed_state.status_code == 200
        value = refreshed_state.json()
        assert value["unchanged"] is True
        assert value["revision"] == revision + 1
        assert value["engine_state"]["learner_preferences"]["preferences"] == "Japanese examples"
        operations = {row["native"]: row for row in value["operations"]}
        assert operations["mastery_quiz"]["available"] is False
        missing = client.get(
            "/api/link/v1/topics/same-topic-id/teaching/protocol",
            headers=headers(account),
            params={"persona": "deleted-role", "mode": "study", "language": "en"},
        )
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "persona_missing"
        assert "no different prompt pack" not in missing.text.lower()


def test_protocol_response_language_is_separate_from_resource_language(persona):
    app, account, _ = persona
    with TestClient(app) as client:
        french = client.get(
            "/api/link/v1/topics/same-topic-id/teaching/protocol",
            headers=headers(account),
            params={"persona": "codex-style", "mode": "study", "language": "fr"},
        )
        assert french.status_code == 200, french.text
        french_value = french.json()
        assert french_value["requested_language"] == "fr"
        assert french_value["response_language"] == "fr"
        assert french_value["resource_language"] == "en"
        assert all(row["language"] == "en" for row in french_value["sections"])
        assert "response_language" in french_value["language_contract"]["reply_instruction"]
        fingerprint = french_value["protocol_fingerprint"]
        english = client.get(
            "/api/link/v1/topics/same-topic-id/teaching/protocol",
            headers=headers(account),
            params={
                "persona": "codex-style",
                "mode": "study",
                "language": "en",
                "known_fingerprint": fingerprint,
            },
        )
        assert english.status_code == 200, english.text
        english_value = english.json()
        assert english_value["unchanged"] is False
        assert english_value["response_language"] == "en"
        assert english_value["resource_language"] == "en"
        assert english_value["protocol_fingerprint"] != fingerprint


async def test_exact_website_session_restore_is_authorized_and_does_not_change_preferences(
    persona, website_session, http_host
):
    app, account, _ = persona
    from deeptutor.multi_user.paths import user_context
    from deeptutor.services.session import get_session_store
    from deeptutor.services.workspace.context import workspace_context

    with user_context(account["user"]), workspace_context(account["workspace"]):
        preferences = (await get_session_store().get_session(website_session))["preferences"]
    with TestClient(app) as client:
        restored = client.get(
            f"/api/link/v1/teaching/sessions/{website_session}/selection",
            headers=headers(account),
        )
        assert restored.status_code == 200, restored.text
        value = restored.json()
        assert value["persona"] == "codex-style"
        assert value["persona_fingerprint"]
        assert value["mastery_session_mode"] == "review"
        assert value["language"] == "zh"
        assert value["website_preferences_read_only"] is True
        wrong_account = {**headers(account), "X-DeepTutor-Account": "other"}
        assert (
            client.get(
                f"/api/link/v1/teaching/sessions/{website_session}/selection",
                headers=wrong_account,
            ).status_code
            == 403
        )
    with user_context(account["user"]), workspace_context(account["workspace"]):
        after = (await get_session_store().get_session(website_session))["preferences"]
    assert after == preferences


def test_protocol_denies_cross_account_and_missing_prompt_pack_is_honest(
    persona, http_host, monkeypatch, tmp_path
):
    app, account, _ = persona
    from deeptutor.services.prompt.manager import PromptManager

    monkeypatch.setattr(
        PromptManager,
        "_candidate_prompt_dirs",
        lambda self, module_name: [tmp_path / "missing-mastery-prompts"],
    )
    with TestClient(app) as client:
        wrong = {**headers(account), "X-DeepTutor-Account": "other"}
        assert (
            client.get(
                "/api/link/v1/topics/same-topic-id/teaching/protocol",
                headers=wrong,
                params={"persona": "codex-style", "mode": "study", "language": "en"},
            ).status_code
            == 403
        )
        unavailable = client.get(
            "/api/link/v1/topics/same-topic-id/teaching/protocol",
            headers=headers(account),
            params={"persona": "codex-style", "mode": "study", "language": "en"},
        )
        assert unavailable.status_code == 503
        assert unavailable.json()["detail"]["code"] == "mastery_prompts_unavailable"


def test_client_selection_commands_are_bounded_and_parser_contract():
    commands = parser()
    argv = ["--cache", "/tmp/cache.json", "resume", "--new-chat", "--mode", "review"]
    args = commands.parse_args(argv)
    assert args.command == "resume"
    assert args.new_chat is True
    assert args.mode == "review"
    assert args.cache.name == "cache.json"
    argv = ["persona-select", "teacher", "--mode", "study"]
    with pytest.raises(SystemExit):
        commands.parse_args(argv)
    argv = ["tool", "get_teaching_protocol", "--file", "-"]
    args = commands.parse_args(argv)
    assert args.name == "get_teaching_protocol"
    assert json.dumps({"known_fingerprint": "sha256:test"}, ensure_ascii=False)


def test_client_protocol_cache_requires_matching_language(tmp_path):
    config = {
        "url": "http://testserver",
        "account_id": "account",
        "workspace_id": "workspace",
        "topic_id": "topic",
    }
    cache = {
        "schema_version": 1,
        "identity": {
            "url": config["url"],
            "account_id": config["account_id"],
            "workspace_id": config["workspace_id"],
            "topic_id": config["topic_id"],
        },
        "persona_name": None,
        "mode": "study",
        "language": "en",
        "protocol": {"protocol_fingerprint": "sha256:en"},
    }
    assert client_script.valid_cache(cache, config, None, "study", "en")
    assert not client_script.valid_cache(cache, config, None, "study", "fr")
    assert not client_script.valid_cache(cache, config, "codex-style", "study", "en")
    assert not client_script.valid_cache(cache, config, None, "review", "en")
    cache["identity"]["topic_id"] = "other-topic"
    assert not client_script.valid_cache(cache, config, None, "study", "en")


def test_initialize_preserves_scope_fields_and_resets_cross_scope(tmp_path):
    config_path = tmp_path / "connection.json"
    client_script.save(
        config_path,
        {
            "url": "http://127.0.0.1",
            "web_url": "https://web.testserver",
            "account_id": "account",
            "workspace_id": "workspace",
            "topic_id": "original-topic",
            "token_env": "CUSTOM_TOKEN_ENV",
            "persona": "codex-style",
            "persona_source": "assistant_explicit",
            "teaching_mode": "review",
            "teaching_language": "zh",
        },
    )

    class BridgedClient:
        def __init__(self, config, credentials):
            self.config = config

        def topic_path(self, suffix):
            return "/api/link/v1/topics/" + self.config["topic_id"] + "/" + suffix

        def call(self, method, path, data=None, params=None, timeout=60):
            assert method == "GET"
            if path == "/api/link/v1/identity":
                return {
                    "schema_version": 1,
                    "host_version": "1.7.1",
                    "extension_version": "0.2.0",
                    "account_id": "account",
                    "workspace_id": self.config["workspace_id"] or "workspace",
                }
            if path == "/api/link/v1/workspaces":
                return {"workspaces": []}
            if path == "/api/link/v1/topics":
                return {"topics": []}
            if path.endswith("/context"):
                return {
                    "topic_id": "original-topic",
                    "name": "Original topic",
                    "revision": 4,
                    "objectives": [],
                }
            if path == "/api/link/v1/runtime/skills":
                return {"skills": []}
            if path == "/api/link/v1/runtime/capabilities":
                return {}
            if path == "/health/ready":
                return {"status": "ready"}
            raise AssertionError(path)

    original_client = client_script.Client
    client_script.Client = BridgedClient
    try:
        saved = client_script.execute(
            client_script.parser().parse_args(
                ["--config", str(config_path), "--credentials", str(tmp_path / "credentials.json"), "initialize", "--url", "http://127.0.0.1"]
            )
        )
        assert saved["connected"] is True
        value = client_script.load(config_path)
        assert value["topic_id"] == "original-topic"
        assert value["token_env"] == "CUSTOM_TOKEN_ENV"
        assert value["web_url"] == "https://web.testserver"
        assert value["persona"] == "codex-style"
        assert value["teaching_mode"] == "review"
        assert value["teaching_language"] == "zh"

        client_script.execute(
            client_script.parser().parse_args(
                ["--config", str(config_path), "--credentials", str(tmp_path / "credentials.json"), "initialize", "--url", "http://127.0.0.1", "--workspace", "other-workspace"]
            )
        )
        value = client_script.load(config_path)
        assert value["workspace_id"] == "other-workspace"
        assert "topic_id" not in value
        assert "persona" not in value
        assert "persona_source" not in value
        assert "teaching_mode" not in value
        assert "teaching_language" not in value
        assert value["token_env"] == "CUSTOM_TOKEN_ENV"
    finally:
        client_script.Client = original_client


def test_initialize_fresh_connection_uses_explicit_default_scope(tmp_path):
    config_path = tmp_path / "connection.json"
    client_configs = []

    class BridgedClient:
        def __init__(self, config, credentials):
            self.config = config
            client_configs.append(dict(config))

        def call(self, method, path, data=None, params=None, timeout=60):
            if path == "/api/link/v1/identity":
                return {
                    "schema_version": 1,
                    "host_version": "1.7.1",
                    "extension_version": "0.2.0",
                    "account_id": "account",
                    "workspace_id": self.config["workspace_id"] or "default-workspace",
                }
            if path in {
                "/api/link/v1/workspaces",
                "/api/link/v1/topics",
                "/api/link/v1/runtime/skills",
                "/api/link/v1/runtime/capabilities",
            }:
                return {}
            if path == "/health/ready":
                return {"status": "ready"}
            raise AssertionError(path)

    original_client = client_script.Client
    client_script.Client = BridgedClient
    try:
        saved = client_script.execute(
            client_script.parser().parse_args(
                ["--config", str(config_path), "--credentials", str(tmp_path / "credentials.json"), "initialize", "--url", "http://127.0.0.1"]
            )
        )
    finally:
        client_script.Client = original_client
    assert saved["selection_preserved"] is False
    assert saved["topic_bound"] is None
    assert client_configs[0]["account_id"] == ""
    assert client_configs[0]["workspace_id"] == ""
    assert client_configs[0]["token_env"] == "DT_LINK_TOKEN"
    value = client_script.load(config_path)
    assert value["account_id"] == "account"
    assert value["workspace_id"] == "default-workspace"
    for key in ("topic_id", "persona", "persona_source", "teaching_mode", "teaching_language"):
        assert key not in value


def test_history_commands_are_bounded_and_scoped(tmp_path):
    config_path = tmp_path / "connection.json"
    client_script.save(
        config_path,
        {
            "url": "http://127.0.0.1",
            "account_id": "account",
            "workspace_id": "workspace",
            "topic_id": "topic",
            "token_env": "TOKEN_ENV",
        },
    )
    calls = []

    class BridgedClient:
        def __init__(self, config, credentials):
            pass

        def call(self, method, path, data=None, params=None, timeout=60):
            calls.append((method, path, params))
            return (
                {"sessions": [{"session_id": "session", "answer": "secret"}]}
                if path == "/api/sessions"
                else {"messages": [{"role": "user", "content": "visible"}]}
            )

    original_client = client_script.Client
    client_script.Client = BridgedClient
    try:
        listing = client_script.execute(
            client_script.parser().parse_args(
                [
                    "--config",
                    str(config_path),
                    "--credentials",
                    str(tmp_path / "credentials.json"),
                    "history-list",
                    "--offset",
                    "4",
                    "--limit",
                    "10",
                ]
            )
        )
        history = client_script.execute(
            client_script.parser().parse_args(
                [
                    "--config",
                    str(config_path),
                    "--credentials",
                    str(tmp_path / "credentials.json"),
                    "history-show",
                    "--session",
                    "a/b",
                    "--offset",
                    "2",
                    "--limit",
                    "5",
                ]
            )
        )
    finally:
        client_script.Client = original_client
    assert listing == {"sessions": [{"session_id": "session"}]}
    assert calls[0] == ("GET", "/api/sessions", {"offset": 4, "limit": 10})
    assert history == {"messages": [{"role": "user", "content": "visible"}]}
    assert calls[1] == (
        "GET",
        "/api/link/v1/sessions/a%2Fb/history",
        {"offset": 2, "limit": 5},
    )


def test_client_explicit_persona_select_and_clear_preserve_connection(
    persona, monkeypatch, tmp_path
):
    app, account, _ = persona
    config_path = tmp_path / "connection.json"
    cache_path = tmp_path / "teaching-protocol.json"
    credentials_path = tmp_path / "credentials.json"
    config = {
        "url": "http://testserver",
        "web_url": "http://web.testserver",
        "account_id": account["uid"],
        "workspace_id": account["workspace"],
        "topic_id": "same-topic-id",
        "token_env": "SYNTHETIC_TOKEN_ENV",
    }
    client_script.save(config_path, config)

    class BridgedClient:
        def __init__(self, loaded_config, credentials):
            assert config.items() <= loaded_config.items()
            assert credentials == credentials_path

        def call(self, method, path, data=None, params=None, timeout=60):
            with TestClient(app) as test_client:
                response = test_client.request(
                    method,
                    path,
                    json=data,
                    params=params,
                    headers=headers(account),
                )
            if response.status_code >= 400:
                raise client_script.CommandError(f"DeepTutor HTTP {response.status_code}")
            return response.json()

    monkeypatch.setattr(client_script, "Client", BridgedClient)
    selected = client_script.execute(
        parser().parse_args(
            [
                    "--config",
                    str(config_path),
                    "--credentials",
                    str(credentials_path),
                    "--cache",
                    str(cache_path),
                "persona-select",
                "codex-style",
            ]
        )
    )
    assert selected["persona"]["name"] == "codex-style"
    assert selected["saved"] is True
    saved = client_script.load(config_path)
    assert saved["persona"] == "codex-style"
    assert saved["persona_source"] == "assistant_explicit"
    assert saved["token_env"] == "SYNTHETIC_TOKEN_ENV"
    assert saved["topic_id"] == "same-topic-id"
    assert not cache_path.exists()
    cleared = client_script.execute(
        parser().parse_args(
            [
                    "--config",
                    str(config_path),
                    "--credentials",
                    str(credentials_path),
                    "--cache",
                    str(cache_path),
                "persona-clear",
            ]
        )
    )
    assert cleared == {
        "persona": None,
        "saved": True,
        "teaching_baseline": "mastery_tutor",
    }
    saved = client_script.load(config_path)
    assert "persona" not in saved
    assert "persona_source" not in saved
    assert saved["token_env"] == "SYNTHETIC_TOKEN_ENV"
