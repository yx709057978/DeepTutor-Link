import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient


def headers(account):
    return {
        "Authorization": f"Bearer {account['token']}",
        "X-DeepTutor-Account": account["uid"],
        "X-DeepTutor-Workspace": account["workspace"],
    }


def test_http_query_validation_returns_bounds_without_submitted_content(http_host):
    app, (account, _) = http_host
    with TestClient(app) as client:
        response = client.get(
            "/api/link/v1/topics", headers=headers(account),
            params={"limit": 100, "offset": "PRIVATE"},
        )
        assert response.status_code == 422
        assert "no-store" in response.headers["cache-control"]
        assert "PRIVATE" not in response.text
        assert response.json()["detail"]["errors"] == [
            {"field": "offset", "expected": "integer"},
            {"field": "limit", "le": 50},
        ]


def test_http_route_creation_preview_commit_account_and_workspace_guards(http_host):
    app, (a, b) = http_host
    payload = {
        "creation_id": str(uuid4()),
        "name": "Codex route",
        "goal": "Synthetic learning goal",
        "modules": [{"name": "Module", "knowledge_points": [{"name": "First objective"}]}],
    }
    other_before = b["store"].load("same-topic-id").model_dump()
    with TestClient(app) as client:
        assert client.post("/api/link/v1/topics", json=payload).status_code == 401
        wrong = {**headers(a), "X-DeepTutor-Account": b["uid"]}
        assert client.post("/api/link/v1/topics", headers=wrong, json=payload).status_code == 403
        made = client.post("/api/link/v1/topics", headers=headers(a), json=payload)
        assert made.status_code == 200, made.text
        topic_id = made.json()["topic_id"]
        route_path = f"/api/link/v1/topics/{topic_id}/route"
        assert client.get(route_path, headers=headers(b)).status_code == 404
        route = client.get(route_path, headers=headers(a))
        assert route.status_code == 200, route.text
        assert route.json()["modules"][0]["knowledge_points"][0]["id"]
        modules = route.json()["modules"]
        modules[0]["name"] = "Updated module"
        preview_payload = {
            "request_id": str(uuid4()),
            "expected_revision": route.json()["revision"],
            "modules": modules,
        }
        preview = client.post(route_path + "/previews", headers=headers(a), json=preview_payload)
        assert preview.status_code == 200, preview.text
        assert "no-store" in preview.headers["cache-control"]
        assert a["store"].load(topic_id).version == 1
        commit_payload = {"request_id": str(uuid4()), "preview_id": preview.json()["preview_id"]}
        assert (
            client.post(
                route_path + "/commits", headers=headers(b), json=commit_payload
            ).status_code
            == 404
        )
        committed = client.post(route_path + "/commits", headers=headers(a), json=commit_payload)
        assert committed.status_code == 200, committed.text
        assert committed.json()["revision"] == 2
        assert client.post(route_path + "/commits", headers=headers(a), json=commit_payload).json()[
            "replayed"
        ]
        assert a["store"].load(topic_id).modules[0].name == "Updated module"
        assert b["store"].load("same-topic-id").model_dump() == other_before
        invalid = client.post(
            "/api/link/v1/topics",
            headers=headers(a),
            json={**payload, "profile": {"SECRET_INPUT": "PRIVATE"}},
        )
        assert invalid.status_code == 422
        assert "SECRET_INPUT" not in invalid.text and "PRIVATE" not in invalid.text
        # Same creation token remains scoped to each account's own learning store.
        assert (
            client.post("/api/link/v1/topics", headers=headers(b), json=payload).status_code == 200
        )


def test_http_route_sources_use_real_host_visibility_and_file_containment(http_host):
    from deeptutor.multi_user.paths import user_context
    from deeptutor.services.path_service import get_path_service
    from deeptutor.services.workspace.context import workspace_context

    app, (a, b) = http_host
    with user_context(a["user"]), workspace_context(a["workspace"]):
        root = get_path_service().get_knowledge_bases_root()
        documents = root / "original-kb/raw"
        documents.mkdir(parents=True, exist_ok=True)
        (documents / "chapter.txt").write_text("Synthetic material, never a user document.")
        (root / "private.txt").write_text("Outside the selected knowledge base")
        (documents / "escape.txt").symlink_to(root / "private.txt")
        (root / "kb_config.json").write_text(
            json.dumps({"knowledge_bases": {"original-kb": {"rag_provider": "llamaindex"}}})
        )
    payload = {
        "creation_id": str(uuid4()),
        "name": "Source route",
        "goal": "Use original material",
        "sources": [
            {
                "client_ref": "chapter",
                "kind": "file",
                "source_id": "chapter.txt",
                "kb_name": f"workspace:{a['workspace']}:kb:original-kb",
                "label": "Chapter",
            }
        ],
        "modules": [
            {
                "name": "Module",
                "knowledge_points": [{"name": "Source objective", "topic_source_ids": ["chapter"]}],
            }
        ],
    }
    with TestClient(app) as client:
        denied = client.post("/api/link/v1/topics", headers=headers(b), json=payload)
        assert denied.status_code in {403, 404}, denied.text
        assert "original-kb" not in denied.text
        assert not b["store"].exists("topic_" + payload["creation_id"].replace("-", ""))
        for path in ("../../private.txt", "escape.txt", "missing.txt"):
            bad = {
                **payload,
                "creation_id": str(uuid4()),
                "sources": [{**payload["sources"][0], "source_id": path}],
            }
            response = client.post("/api/link/v1/topics", headers=headers(a), json=bad)
            assert response.status_code == 409, response.text
            assert response.json()["detail"]["code"] == "source_unavailable"
            assert not a["store"].exists("topic_" + bad["creation_id"].replace("-", ""))
        made = client.post("/api/link/v1/topics", headers=headers(a), json=payload)
        assert made.status_code == 200, made.text
        p = a["store"].load(made.json()["topic_id"])
        topic = a["store"].get_topic(p.book_id)
        assert p.modules[0].knowledge_points[0].topic_source_ids == [topic.sources[0].id]
        assert not p.source_grounded
        assert topic.sources[0].metadata == {"kb_name": payload["sources"][0]["kb_name"]}


def test_http_real_auth_account_workspace_and_activity_lease(http_host):
    from deeptutor.multi_user.paths import user_context
    from deeptutor.services.workspace.activity import data_activity

    app, (a, b) = http_host
    with TestClient(app) as client:
        assert client.get("/api/link/v1/identity").status_code == 401
        assert (
            client.get(
                "/api/link/v1/identity", headers={"Authorization": "Bearer invalid"}
            ).status_code
            == 401
        )
        result = client.get("/api/link/v1/identity", headers=headers(a))
        assert result.status_code == 200, result.text
        assert result.json()["account_id"] == a["uid"]
        assert result.json()["workspace_id"] == a["workspace"]
        capabilities = result.json()["capabilities"]
        assert capabilities["assessment_confidence_threshold"] == 0.8
        assert capabilities["qualitative_assessment_repair"] is True
        assert capabilities["qualitative_repair_behavior"] == (
            "exact_preview_confirmed_commit_source_validated"
        )
        assert capabilities["direct_capability_turn"] is True
        result = client.get("/api/link/v1/topics/same-topic-id/context", headers=headers(a))
        assert result.status_code == 200, result.text
        assert result.json()["name"] == "alpha original topic"
        assert "no-store" in result.headers["cache-control"]
        wrong_account = {**headers(b), "X-DeepTutor-Account": a["uid"]}
        assert client.get("/api/link/v1/topics", headers=wrong_account).status_code == 403
        foreign_ws = {**headers(a), "X-DeepTutor-Workspace": b["workspace"]}
        assert client.get("/api/link/v1/topics", headers=foreign_ws).status_code == 404
        conflict = client.get(
            "/api/link/v1/topics", headers=headers(a), params={"dt_workspace": b["workspace"]}
        )
        assert conflict.status_code == 400
        # Actual auth dependency acquires activity; middleware releases it after each request.
        with user_context(a["user"]), data_activity(exclusive=True):
            assert client.get("/api/link/v1/topics", headers=headers(a)).status_code == 404
        assert client.get("/api/link/v1/topics", headers=headers(a)).status_code == 200


def test_native_mastery_schema_catalog_discloses_bridge_and_turn_scope(http_host):
    app, (a, b) = http_host
    with TestClient(app) as client:
        denied = client.get("/api/link/v1/mastery/tools")
        assert denied.status_code == 401

        catalog = client.get("/api/link/v1/mastery/tools", headers=headers(a))
        assert catalog.status_code == 200, catalog.text
        value = catalog.json()
        names = {row["name"] for row in value["tools"]}
        assert {"mastery_status", "mastery_quiz", "mastery_switch", "mastery_leave"} <= names
        assert value["invocation_available"] is True
        assert value["invocation"]["operation"] == "call_mastery_tutor"
        assert value["read_only_status_bridge"]["available"] is True
        assert value["read_only_status_bridge"]["operation"] == "read_mastery_status"
        assert value["model_behavior"]["mastery_assess"] == "semantic_learner_explanation_assessment"
        assert value["model_behavior"]["mastery_quiz"] == "host_model_may_bind_source_grounded_questions"
        assert value["model_behavior"]["mastery_profile"] == "no_model_call_by_tool_contract"

        wrong_account = {**headers(b), "X-DeepTutor-Account": a["uid"]}
        assert client.get("/api/link/v1/mastery/tools", headers=wrong_account).status_code == 403
        wrong_workspace = {**headers(a), "X-DeepTutor-Workspace": b["workspace"]}
        # The host hides a workspace that this account cannot select.
        assert client.get("/api/link/v1/mastery/tools", headers=wrong_workspace).status_code == 404


def test_runtime_skill_catalog_does_not_claim_shell_or_unavailable_tools_executable(
    http_host, monkeypatch
):
    from types import SimpleNamespace

    import deeptutor.app
    import deeptutor.services.skill.runtime

    import deeptutor_link.server as server_module

    app, (account, _) = http_host

    class FakeSkillService:
        def __init__(self, name, content, requires):
            self.name, self.content, self.requires = name, content, requires

        def list_skills(self):
            return [SimpleNamespace(name=self.name, description=self.name, source="synthetic")]

        def summary_entries(self):
            return [SimpleNamespace(name=self.name, available=True, missing=[])]

        def get_detail(self, name):
            return SimpleNamespace(content=self.content)

        def _parse_frontmatter(self, content):
            return {"requires": self.requires}, content

        def _requires_from_meta(self, metadata):
            return metadata["requires"]

        def list_skill_files(self, name):
            return ["scripts/run.py"] if name == "pdf-work" else []

    skills = {
        "pdf-work": FakeSkillService(
            "pdf-work",
            "Use `exec` with Python to read the file.",
            {"sandbox": "shell", "tools": ["exec", "read_skill"]},
        ),
        "unavailable-tool": FakeSkillService(
            "unavailable-tool", "A legacy course tool.", {"tools": ["list_courses"]}
        ),
    }

    class FakeDeepTutorApp:
        def __init__(self, *, workspace_id):
            pass

        def get_capability_contracts(self):
            return []

    monkeypatch.setattr(deeptutor.services.skill.runtime, "runtime_skills", lambda: skills)
    monkeypatch.setattr(deeptutor.app, "DeepTutorApp", FakeDeepTutorApp)
    monkeypatch.setattr(server_module, "mastery_tool_catalog", lambda: {"tools": []})

    with TestClient(app) as client:
        response = client.get("/api/link/v1/runtime/skills", headers=headers(account))
    assert response.status_code == 200, response.text
    rows = {row["name"]: row for row in response.json()["skills"]}
    pdf = rows["pdf-work"]
    assert pdf["host_available"] is True
    assert pdf["available"] is False
    assert pdf["sandbox_execution"]["available"] is False
    assert pdf["sandbox_execution"]["required"] == "shell"
    assert pdf["sandbox_execution"]["script_files_not_executed"] == ["scripts/run.py"]
    assert "SANDBOX: shell" in pdf["missing_requirements"]
    assert "TOOL: exec" in pdf["missing_requirements"]
    unavailable = rows["unavailable-tool"]
    assert unavailable["host_available"] is True
    assert unavailable["available"] is False
    assert "TOOL: list_courses" in unavailable["missing_requirements"]
    assert unavailable["tool_mappings"][0]["available"] is False


def test_native_mastery_tool_executes_host_tool_and_replays_receipt(http_host):
    app, (account, _) = http_host
    body = {
        "client_id": "codex-test-client",
        "session_id": "mastery-session-1",
        "turn_id": "learner-turn-1",
        "request_id": "12345678-1234-4234-8234-123456789abc",
        "tool": "mastery_profile",
        "arguments": {"preferences": "Use concise Chinese examples."},
        "user_message": "请用简洁的中文例子。",
        "confirm": True,
    }
    path = "/api/link/v1/topics/same-topic-id/mastery/tool"
    with TestClient(app) as client:
        result = client.post(path, headers=headers(account), json=body)
        assert result.status_code == 200, result.text
        value = result.json()
        assert value["success"] is True
        assert value["tool"] == "mastery_profile"
        assert value["session_id"] == body["session_id"]
        assert value["client_session_id"] == body["session_id"]
        assert value["host_session_id"] != value["client_session_id"]
        assert value["model_behavior"]["host_model_may_be_invoked"] is False
        revision = account["store"].load("same-topic-id").version
        assert account["store"].load("same-topic-id").learner_profile.preferences == (
            "Use concise Chinese examples."
        )
        with account["store"]._connect() as conn:
            receipt_rows = conn.execute(
                "SELECT status, response_json FROM codex_native_tool_requests"
            ).fetchall()
        assert receipt_rows and receipt_rows[0]["status"] == "completed", receipt_rows

        replay = client.post(path, headers=headers(account), json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert account["store"].load("same-topic-id").version == revision

        changed = {**body, "arguments": {"preferences": "A different preference."}}
        conflict = client.post(path, headers=headers(account), json=changed)
        assert conflict.status_code == 409, conflict.text

        from deeptutor.multi_user.paths import user_context
        from deeptutor.services.session import get_session_store
        from deeptutor.services.workspace.context import workspace_context

        with user_context(account["user"]), workspace_context(account["workspace"]):
            messages = asyncio.run(get_session_store().get_messages(value["host_session_id"]))
        assert any(
            row["role"] == "user" and row["content"] == body["user_message"]
            for row in messages
        )


def test_native_quiz_answer_grade_and_record_turn_keep_client_session_stable(http_host):
    from deeptutor.learning.models import KnowledgeType

    app, (account, _) = http_host
    progress = account["store"].load("same-topic-id")
    progress.modules[0].knowledge_points[0].type = KnowledgeType.MEMORY
    progress.knowledge_types["original-kp"] = KnowledgeType.MEMORY
    account["store"].save(progress)
    path = "/api/link/v1/topics/same-topic-id/mastery/tool"
    client_session_id = "codex-client-session-stable"
    headers_for_account = headers(account)

    quiz = {
        "client_id": "codex-test-client",
        "client_session_id": client_session_id,
        "turn_id": "quiz-turn",
        "request_id": str(uuid4()),
        "tool": "mastery_quiz",
        "arguments": {
            "knowledge_point_id": "original-kp",
            "question": "What is two plus two?",
            "expected_answer": "4",
            "question_type": "short",
        },
        "confirm": True,
    }
    with TestClient(app) as client:
        quiz_result = client.post(path, headers=headers_for_account, json=quiz)
        assert quiz_result.status_code == 200, quiz_result.text
        quiz_value = quiz_result.json()
        assert quiz_value["success"] is True
        assert quiz_value["client_session_id"] == client_session_id
        assert quiz_value["session_id"] == client_session_id
        assert quiz_value["host_session_id"]
        assert quiz_value["model_behavior"]["host_model_may_be_invoked"] is True
        question_id = quiz_value["metadata"]["mastery_question"]["question_id"]

        before_missing = account["store"].load("same-topic-id").model_dump()
        missing_answer = {
            "client_id": quiz["client_id"],
            "client_session_id": client_session_id,
            "turn_id": "grade-turn-missing-answer",
            "request_id": str(uuid4()),
            "tool": "mastery_grade",
            "arguments": {"question_id": question_id},
            "confirm": True,
        }
        rejected = client.post(path, headers=headers_for_account, json=missing_answer)
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["detail"]["code"] == "missing_learner_answer"
        assert account["store"].load("same-topic-id").model_dump() == before_missing

        grade = {
            **missing_answer,
            "turn_id": "grade-turn-real-answer",
            "request_id": str(uuid4()),
            "arguments": {"question_id": question_id, "answer": "4"},
        }
        graded = client.post(path, headers=headers_for_account, json=grade)
        assert graded.status_code == 200, graded.text
        grade_value = graded.json()
        assert grade_value["success"] is True
        assert grade_value["content"]["result"]["result"] == "correct"
        assert grade_value["content"]["result"]["final"] is True
        interaction = account["store"].get_interaction("same-topic-id", question_id)
        assert interaction.user_answer == "4"
        assert interaction.result["is_correct"] is True
        saved = account["store"].load("same-topic-id")
        assert saved.learning_evidence[-1].learner_answer == "4"
        assert saved.mastery_levels["original-kp"] > 0

        record_turn = {
            "client_id": quiz["client_id"],
            "client_session_id": grade_value["client_session_id"],
            "turn_id": grade["turn_id"],
            "request_id": str(uuid4()),
            "tool": "record_turn",
            "arguments": {},
            "assistant_message": "The answer is correct; 2 + 2 = 4.",
            "confirm": True,
        }
        recorded = client.post(path, headers=headers_for_account, json=record_turn)
        assert recorded.status_code == 200, recorded.text
        recorded_value = recorded.json()
        assert recorded_value["status"] == "saved"
        assert recorded_value["client_session_id"] == client_session_id
        assert recorded_value["host_session_id"] == quiz_value["host_session_id"]


def test_native_mastery_status_invokes_host_tool_for_existing_topic(http_host):
    app, (a, b) = http_host
    path = "/api/link/v1/topics/same-topic-id/mastery/status"
    with TestClient(app) as client:
        result = client.get(path, headers=headers(a))
        assert result.status_code == 200, result.text
        value = result.json()
        assert value["native_tool"] == "mastery_status"
        assert value["path_id"] == "same-topic-id"
        assert value["map"]["counts"]["total"] == 1
        assert value["map"]["modules"][0]["knowledge_points"][0]["id"] == "original-kp"
        assert "no-store" in result.headers["cache-control"]

        beta_result = client.get(path, headers=headers(b))
        assert beta_result.status_code == 200, beta_result.text
        assert beta_result.json()["path_name"] == "beta original topic"
        assert beta_result.json()["path_id"] == "same-topic-id"
        assert beta_result.json()["path_name"] != value["path_name"]
        assert client.get(
            "/api/link/v1/topics/missing/mastery/status", headers=headers(a)
        ).status_code == 404
        assert client.get(
            "/api/link/v1/topics/same-topic-id/mastery/switch",
            headers=headers(a),
        ).status_code == 404


def test_native_mastery_status_removes_turn_answers_and_private_notes(http_host, monkeypatch):
    from deeptutor.capabilities.mastery.tools import MasteryStatusTool
    from deeptutor.core.tool_protocol import ToolResult

    app, (a, _) = http_host
    sentinel = "synthetic-private-answer-7c91"
    payload = {
        "status": "active",
        "path_revision": 3,
        "path_name": "Safe title",
        "goal": "Safe goal",
        "sources": [],
        "mode": "study",
        "next": {
            "action": "answer_pending",
            "knowledge_point_id": "original-kp",
            "pending_prompt": sentinel,
        },
        "map": {
            "counts": {"total": 1, "new": 1, "learning": 0, "mastered": 0},
            "modules": [
                {
                    "id": "m",
                    "name": "Module",
                    "order": 0,
                    "knowledge_points": [
                        {"id": "original-kp", "name": "Concept", "override_note": sentinel}
                    ],
                }
            ],
        },
        "learner_profile": {"preferences": "brief", "notes": sentinel},
        "intake_needed": False,
        "pending_interaction": {"learner_answer": sentinel, "answers": {"q": sentinel}},
    }

    async def fake_execute(self, **kwargs):
        assert kwargs == {
            "_mastery_path_id": "same-topic-id",
            "include_teaching_history": False,
            "_working_brief": True,
        }
        return ToolResult(content=json.dumps(payload))

    monkeypatch.setattr(MasteryStatusTool, "execute", fake_execute)
    with TestClient(app) as client:
        result = client.get(
            "/api/link/v1/topics/same-topic-id/mastery/status",
            headers=headers(a),
        )
        assert result.status_code == 200, result.text
        assert sentinel not in result.text
        assert result.json()["pending"] == {"present": True, "action": "finish_in_web"}
        assert result.json()["next"] == {
            "action": "answer_pending",
            "knowledge_point_id": "original-kp",
        }


def test_direct_mastery_turn_uses_host_session_hooks_and_redacts_tool_arguments(
    http_host, monkeypatch
):
    from types import SimpleNamespace

    import deeptutor.app

    app, (a, _) = http_host
    captured = {}
    hidden_answer = "private-answer-key-31"

    class FakeDeepTutorApp:
        def __init__(self, *, workspace_id):
            captured["workspace_id"] = workspace_id

        def get_capability_availability(self, capability):
            assert capability == "mastery_path"
            return SimpleNamespace(available=True)

        def get_capability_contract(self, capability):
            assert capability == "mastery_path"
            return {"name": capability, "kind": "turn"}

        async def start_turn(self, payload):
            captured["payload"] = payload
            return {"id": "session-native"}, {"id": "turn-native"}

        async def stream_turn(self, turn_id, after_seq=0):
            assert turn_id == "turn-native"
            yield {"type": "content", "content": "Native tutor response."}
            yield {
                "type": "tool_call",
                "content": "mastery_quiz",
                "metadata": {"args": {"expected_answer": hidden_answer}},
            }
            yield {
                "type": "tool_result",
                "content": "Question registered.",
                "metadata": {
                    "tool_metadata": {
                        "mastery_question": {
                            "question_id": "q-native",
                            "prompt": "Explain the concept.",
                            "question_type": "open",
                            "options": [],
                            "expected_answer": hidden_answer,
                        }
                    }
                },
            }
            yield {"type": "done", "metadata": {"status": "completed"}}

    monkeypatch.setattr(deeptutor.app, "DeepTutorApp", FakeDeepTutorApp)
    body = {
        "capability": "mastery_path",
        "message": "Use the original mastery tutor to quiz me.",
        "request_id": "12345678-1234-4234-8234-123456789abc",
        "direct_dt_conversation": True,
        "confirm": True,
    }
    with TestClient(app) as client:
        result = client.post(
            "/api/link/v1/topics/same-topic-id/capability/turn",
            headers=headers(a),
            json=body,
        )
    assert result.status_code == 200, result.text
    value = result.json()
    assert value["session_id"] == "session-native"
    assert value["turn_id"] == "turn-native"
    assert value["mastery_tools_used"] == ["mastery_quiz"]
    assert value["question_cards"][0]["prompt"] == "Explain the concept."
    assert hidden_answer not in result.text
    assert captured["workspace_id"] == a["workspace"]
    assert captured["payload"]["client_submission_id"] == body["request_id"]
    assert captured["payload"]["mastery_path_id"] == "same-topic-id"
    assert captured["payload"]["capability_once"] is True
    assert captured["payload"]["persist_user_message"] is True


def test_direct_mastery_turn_refuses_to_replace_an_active_session_turn(http_host, monkeypatch):
    from types import SimpleNamespace

    import deeptutor.app

    app, (a, _) = http_host

    class FakeDeepTutorApp:
        def __init__(self, *, workspace_id):
            pass

        async def get_session(self, session_id):
            return {"id": session_id}

        async def get_active_turn(self, session_id):
            return {"turn_id": "existing-turn", "status": "running"}

        def get_capability_availability(self, capability):
            return SimpleNamespace(available=True)

        def get_capability_contract(self, capability):
            return {"name": capability, "kind": "turn"}

        async def start_turn(self, payload):
            raise AssertionError("must not cancel or replace an active turn")

    monkeypatch.setattr(deeptutor.app, "DeepTutorApp", FakeDeepTutorApp)
    body = {
        "capability": "mastery_path",
        "message": "Continue through the native tutor.",
        "request_id": "12345678-1234-4234-8234-123456789abc",
        "session_id": "session-existing",
        "direct_dt_conversation": True,
        "confirm": True,
    }
    with TestClient(app) as client:
        result = client.post(
            "/api/link/v1/topics/same-topic-id/capability/turn",
            headers=headers(a),
            json=body,
        )
    assert result.status_code == 409, result.text
    assert result.json()["detail"]["code"] == "active_turn"


def test_direct_turn_reply_requires_waiting_input(http_host, monkeypatch):
    import deeptutor.app

    app, (a, _) = http_host

    class FakeDeepTutorApp:
        def __init__(self, *, workspace_id):
            pass

        async def get_session(self, session_id):
            return {"id": session_id}

        async def get_active_turn(self, session_id):
            return {"turn_id": "turn-running", "status": "running"}

    monkeypatch.setattr(deeptutor.app, "DeepTutorApp", FakeDeepTutorApp)
    with TestClient(app) as client:
        result = client.post(
            "/api/link/v1/capability/turn/reply",
            headers=headers(a),
            json={
                "session_id": "session-a",
                "turn_id": "turn-running",
                "request_id": "22345678-1234-4234-8234-123456789abc",
                "message": "Actual user reply",
                "confirm": True,
            },
        )
    assert result.status_code == 409, result.text
    assert result.json()["detail"]["code"] == "turn_not_waiting"


def test_explicit_session_history_is_paged_and_projects_only_visible_text(http_host, monkeypatch):
    from datetime import datetime, timezone

    import deeptutor.app

    app, (a, b) = http_host
    hidden = "hidden-answer-or-tool-argument-56"
    rows = [
        {"id": str(i), "role": "user" if i % 2 == 0 else "assistant", "content": f"message-{i}", "created_at": i,
         "metadata": {"expected_answer": hidden}, "tool_arguments": hidden}
        for i in range(4)
    ]

    class FakeDeepTutorApp:
        def __init__(self, *, workspace_id):
            assert workspace_id in {a["workspace"], b["workspace"]}

        async def get_session(self, session_id):
            return {
                "id": session_id,
                "title": "Synthetic session",
                "capability": "mastery_path",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "messages": rows,
            }

    monkeypatch.setattr(deeptutor.app, "DeepTutorApp", FakeDeepTutorApp)
    with TestClient(app) as client:
        result = client.get(
            "/api/link/v1/sessions/session-a/history?offset=1&limit=2",
            headers=headers(a),
        )
        missing = client.get(
            "/api/link/v1/sessions/session-a/history?limit=51",
            headers=headers(a),
        )
    assert result.status_code == 200, result.text
    value = result.json()
    assert [row["content"] for row in value["messages"]] == ["message-1", "message-2"]
    assert value["total_messages"] == 4 and value["next_offset"] == 3
    assert hidden not in result.text
    assert "metadata" not in value["messages"][0]
    assert missing.status_code == 422


async def test_simultaneous_requests_keep_contextvars_separate(http_host):
    app, accounts = http_host
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        results = await asyncio.gather(
            *[
                client.get("/api/link/v1/topics/same-topic-id/context", headers=headers(account))
                for account in accounts * 5
            ]
        )
    assert [r.json()["name"] for r in results] == [
        "alpha original topic",
        "beta original topic",
    ] * 5


def test_http_assessment_writes_original_topic_only_and_hides_validation_input(http_host):
    app, (a, b) = http_host
    payload = {
        "assessment_id": str(uuid4()),
        "expected_revision": a["store"].load("same-topic-id").version,
        "objective_id": "original-kp",
        "question": "Explain the original concept.",
        "learner_answer": "Synthetic learner answer",
        "result": "correct",
        "confidence": 0.9,
        "feedback": "Demonstrates the expected reasoning.",
    }
    path = "/api/link/v1/topics/same-topic-id/assessments"
    with TestClient(app) as client:
        before = a["store"].load("same-topic-id").model_dump()
        uncertain = client.post(path, headers=headers(a), json={**payload, "confidence": 0.1})
        assert uncertain.status_code == 200, uncertain.text
        assert uncertain.json()["result"] == "ungraded"
        assert uncertain.json()["saved"] is False
        assert uncertain.json()["final"] is False
        assert a["store"].load("same-topic-id").model_dump() == before
        result = client.post(path, headers=headers(a), json=payload)
        assert result.status_code == 200, result.text
        retry = client.post(path, headers=headers(a), json=payload)
        assert retry.json()["deduplicated"]
        assert len(a["store"].load("same-topic-id").learning_evidence) == 1
        assert not b["store"].load("same-topic-id").learning_evidence
        stale = client.post(
            path, headers=headers(a), json={**payload, "assessment_id": str(uuid4())}
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "stale_revision"
        malformed = client.post(
            path, headers=headers(a), json={**payload, "confidence": "SECRET_BAD_INPUT"}
        )
        assert malformed.status_code == 422
        assert "SECRET_BAD_INPUT" not in malformed.text
        assert "Synthetic learner answer" not in result.text


def test_http_assessment_verifies_only_cited_file_quotes_inside_selected_kb(http_host):
    from deeptutor.learning.models import TopicMetadata, TopicSource
    from deeptutor.multi_user.paths import user_context
    from deeptutor.services.path_service import get_path_service
    from deeptutor.services.workspace.context import workspace_context

    app, (account, _) = http_host
    kb_ref = f"workspace:{account['workspace']}:kb:original-kb"
    original_text = "A verified passage from the selected synthetic source document."
    with user_context(account["user"]), workspace_context(account["workspace"]):
        root = get_path_service().get_knowledge_bases_root()
        documents = root / "original-kb/raw"
        documents.mkdir(parents=True, exist_ok=True)
        (documents / "chapter.txt").write_text(original_text)
        (root / "kb_config.json").write_text(
            json.dumps({"knowledge_bases": {"original-kb": {"rag_provider": "llamaindex"}}})
        )
        from deeptutor.multi_user.knowledge_access import resolve_kb_document_path
        from deeptutor.services.workspace.knowledge import learning_source_access

        with learning_source_access([kb_ref]):
            assert resolve_kb_document_path(kb_ref, "chapter.txt") == documents / "chapter.txt"
        progress = account["store"].load("same-topic-id")
        progress.modules[0].knowledge_points[0].topic_source_ids = ["source-file"]
        account["store"].save(progress)
        account["store"].put_topic(
            TopicMetadata(path_id="same-topic-id", goal="Use the selected material"),
            [
                TopicSource(
                    id="source-file",
                    kind="file",
                    source_id="chapter.txt",
                    label="Selected chapter",
                    metadata={"kb_name": kb_ref, "path": "chapter.txt"},
                ),
                TopicSource(id="not-cited", kind="file", source_id="other.txt", label="Other"),
            ],
        )

    base = {
        "assessment_id": str(uuid4()),
        "expected_revision": account["store"].load("same-topic-id").version,
        "objective_id": "original-kp",
        "question": "Explain the selected source concept.",
        "learner_answer": "Synthetic learner answer grounded in the passage.",
        "result": "correct",
        "confidence": 0.9,
        "feedback": "The answer accurately explains the cited passage.",
        "source_ids": ["source-file"],
    }
    path = "/api/link/v1/topics/same-topic-id/assessments"
    with TestClient(app) as client:
        missing = client.post(path, headers=headers(account), json=base)
        assert missing.status_code == 422
        assert missing.json()["detail"]["code"] == "source_evidence_required"

        mismatch_payload = {
            **base,
            "assessment_id": str(uuid4()),
            "evidence_quotes": [{
                "source_id": "source-file",
                "filename": "chapter.txt",
                "quote": "This fabricated passage does not exist in the file.",
            }],
        }
        mismatch = client.post(path, headers=headers(account), json=mismatch_payload)
        assert mismatch.status_code == 422
        assert mismatch.json()["detail"]["code"] == "evidence_quote_not_found"

        escaped_payload = {
            **base,
            "assessment_id": str(uuid4()),
            "evidence_quotes": [{
                "source_id": "source-file",
                "filename": "../private.txt",
                "quote": "A verified passage from somewhere else outside.",
            }],
        }
        escaped = client.post(path, headers=headers(account), json=escaped_payload)
        assert escaped.status_code == 422
        assert escaped.json()["detail"]["code"] == "invalid_evidence_quote"

        valid_payload = {
            **base,
            "evidence_quotes": [{
                "source_id": "source-file",
                "filename": "chapter.txt",
                "quote": original_text,
            }],
        }
        valid = client.post(path, headers=headers(account), json=valid_payload)
        assert valid.status_code == 200, valid.text
        assert valid.json()["saved"] is True
        assert len(account["store"].load("same-topic-id").learning_evidence) == 1


@pytest.mark.parametrize(
    ("chat_configured", "embedding_configured"),
    [
        (False, False),
        (False, True),
        (True, True),
    ],
)
def test_runtime_capabilities_report_model_configuration_not_index_health(
    http_host, monkeypatch, chat_configured, embedding_configured
):
    from types import SimpleNamespace

    app, (account, _) = http_host

    def model_loader(configured):
        if configured:
            return lambda: SimpleNamespace(model="synthetic-model")
        return lambda: (_ for _ in ()).throw(ValueError("not configured"))

    monkeypatch.setattr(
        "deeptutor.services.llm.config.get_llm_config", model_loader(chat_configured)
    )
    monkeypatch.setattr(
        "deeptutor.services.embedding.config.get_embedding_config",
        model_loader(embedding_configured),
    )
    with TestClient(app) as client:
        response = client.get("/api/link/v1/runtime/capabilities", headers=headers(account))
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["host_models"]["chat"]["configured"] is chat_configured
    assert value["host_models"]["embedding"]["configured"] is embedding_configured
    assert value["model_configurations"]["neither_configured"]["ready_for_assistant_teaching_and_supported_assessment"]
    assert value["model_configurations"]["embedding_only"]["ready_for_assistant_teaching_and_supported_assessment"]
    assert value["model_configurations"]["chat_and_embedding"]["ready_for_assistant_teaching_and_supported_assessment"]
    expected_embedding_state = "not_probed; verify with a successful retrieve request"
    assert value["model_configurations"]["embedding_only"]["vector_retrieval"] == expected_embedding_state
    assert value["retrieval"]["vector_search_health"] == "not_probed"
    assert "configuration alone does not establish index health" in value["retrieval"]["vector_search_verification"]


def test_no_model_file_read_quote_assessment_and_context_roundtrip(http_host, monkeypatch):
    from urllib.parse import quote

    from deeptutor.learning.models import TopicMetadata, TopicSource
    from deeptutor.multi_user.paths import user_context
    from deeptutor.services.path_service import get_path_service
    from deeptutor.services.workspace.context import workspace_context

    app, (account, _) = http_host
    kb_ref = f"workspace:{account['workspace']}:kb:original-kb"
    text = "Synthetic verified source passage supporting the original concept."
    with user_context(account["user"]), workspace_context(account["workspace"]):
        root = get_path_service().get_knowledge_bases_root()
        documents = root / "original-kb/raw"
        documents.mkdir(parents=True, exist_ok=True)
        (documents / "chapter.txt").write_text(text)
        (root / "kb_config.json").write_text(
            json.dumps({"knowledge_bases": {"original-kb": {"rag_provider": "llamaindex"}}})
        )
        progress = account["store"].load("same-topic-id")
        progress.modules[0].knowledge_points[0].topic_source_ids = ["source-file"]
        account["store"].save(progress)
        account["store"].put_topic(
            TopicMetadata(path_id="same-topic-id", goal="Use the selected material"),
            [TopicSource(
                id="source-file",
                kind="file",
                source_id="chapter.txt",
                label="Selected chapter",
                metadata={"kb_name": kb_ref, "path": "chapter.txt"},
            )],
        )

    monkeypatch.setattr(
        "deeptutor.services.llm.config.get_llm_config",
        lambda: (_ for _ in ()).throw(ValueError("not configured")),
    )
    monkeypatch.setattr(
        "deeptutor.services.embedding.config.get_embedding_config",
        lambda: (_ for _ in ()).throw(ValueError("not configured")),
    )

    with TestClient(app) as client:
        capability = client.get("/api/link/v1/runtime/capabilities", headers=headers(account))
        assert capability.status_code == 200
        assert capability.json()["host_models"]["chat"]["configured"] is False
        assert capability.json()["host_models"]["embedding"]["configured"] is False

        context = client.get(
            "/api/link/v1/topics/same-topic-id/context", headers=headers(account)
        )
        assert context.status_code == 200, context.text
        source = context.json()["sources"][0]
        assert source["id"] == "source-file"
        assert source["locator"] == {"kb_name": kb_ref, "filename": "chapter.txt"}
        assert source["direct_read_available"] is True
        assert source["retrieval_status"] == "vector_index_and_query_embedding_not_verified"
        assert str(documents.resolve()) not in context.text
        assert "source_id" not in source
        listed = client.get(
            f"/api/link/v1/knowledge-bases/{quote(source['locator']['kb_name'], safe='')}/files",
            headers=headers(account),
            params={"suffix": ".txt"},
        )
        assert listed.status_code == 200, listed.text
        assert [row["name"] for row in listed.json()["files"]] == ["chapter.txt"]

        read = client.get(
            f"/api/link/v1/knowledge-bases/{quote(source['locator']['kb_name'], safe='')}/file-slice",
            headers=headers(account),
            params={"filename": source["locator"]["filename"]},
        )
        assert read.status_code == 200, read.text
        assert text in read.json()["text"]
        search = client.get(
            f"/api/link/v1/knowledge-bases/{quote(source['locator']['kb_name'], safe='')}/file-search-literal",
            headers=headers(account),
            params={"filename": source["locator"]["filename"], "query": "verified source passage"},
        )
        assert search.status_code == 200, search.text
        assert search.json()["matches"]
        assert "verified source passage" in search.json()["matches"][0]["excerpt"]

        assessment = {
            "assessment_id": str(uuid4()),
            "expected_revision": account["store"].load("same-topic-id").version,
            "objective_id": "original-kp",
            "question": "Explain the selected source concept.",
            "learner_answer": "The verified passage supports the concept because it states the key relationship.",
            "result": "correct",
            "confidence": 0.95,
            "feedback": "The explanation accurately connects the source statement to the concept.",
            "independent": True,
            "source_ids": [source["id"]],
            "evidence_quotes": [{
                "source_id": source["id"],
                "filename": source["locator"]["filename"],
                "quote": text,
            }],
        }
        saved = client.post(
            "/api/link/v1/topics/same-topic-id/assessments",
            headers=headers(account),
            json=assessment,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["saved"] is True
        resumed = client.get(
            "/api/link/v1/topics/same-topic-id/context", headers=headers(account)
        )
        assert resumed.status_code == 200, resumed.text
        objective = next(row for row in resumed.json()["objectives"] if row["id"] == "original-kp")
        assert objective["assessed_mastered"] is True
        assert account["store"].load("same-topic-id").learning_evidence[-1].source == (
            "codex_external_assessment"
        )


def test_kb_file_listing_rejects_hidden_and_truncated_manifests(http_host, monkeypatch):
    from types import SimpleNamespace
    from urllib.parse import quote

    import deeptutor.multi_user.knowledge_access as knowledge_access

    app, (account, _) = http_host
    kb_ref = f"workspace:{account['workspace']}:kb:original-kb"
    manifests = []

    def manifest(_kb, *, limit, pattern):
        manifests.append((limit, pattern))
        rows = tuple(SimpleNamespace(name=f"f{i}.txt", size=10) for i in range(1001))
        return SimpleNamespace(enumerable=True, total_is_lower_bound=False, matched=1001, documents=rows)

    monkeypatch.setattr(knowledge_access, "resolve_kb_manifest", manifest)
    path = f"/api/link/v1/knowledge-bases/{quote(kb_ref, safe='')}/files"
    with TestClient(app) as client:
        hidden = client.get(path, headers=headers(account), params={"include_hidden": "true"})
        assert hidden.status_code == 422
        assert hidden.json()["detail"]["code"] == "hidden_files_unsupported"

        oversized = client.get(path, headers=headers(account))
        assert oversized.status_code == 422
        assert oversized.json()["detail"]["code"] == "file_listing_too_large"
        assert "1000" in oversized.json()["detail"]["message"]
    assert manifests == [(1001, "")]


def test_records_omit_conversation_summary_and_original_answers(http_host):
    from deeptutor.multi_user.paths import user_context
    from deeptutor.services.learning_journal import get_learning_journal_store
    from deeptutor.services.workspace.context import workspace_context

    app, (a, _) = http_host
    with user_context(a["user"]), workspace_context(a["workspace"]):
        path = get_learning_journal_store().journal_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "mission": {"topic": "Original mission"},
                    "last_session": {"summary": "SECRET_CHAT_SUMMARY"},
                    "records": [
                        {
                            "id": "insight-1",
                            "title": "Original insight",
                            "insight": "A durable insight",
                        }
                    ],
                }
            )
        )
    with TestClient(app) as client:
        result = client.get("/api/link/v1/records", headers=headers(a))
        assert result.status_code == 200, result.text
        assert result.json()["journal"]["mission"]["topic"] == "Original mission"
        assert "SECRET_CHAT_SUMMARY" not in result.text
        assert result.json()["journal"]["insights"][0]["id"] == "insight-1"


def test_recovery_uses_original_auth_account_workspace_scope(http_host):
    from deeptutor_link.leases import mutation_lock_path
    from deeptutor_link.locking import advisory_lock

    app, (a, b) = http_host
    for account in (a, b):
        store = account["store"]
        with advisory_lock(mutation_lock_path(store, "same-topic-id")) as token:
            store.acquire_path_lease(
                "same-topic-id",
                "__path_api__",
                f"codex-lock-v1:{token}:{uuid4()}",
                bind_session=False,
            )
    path = "/api/link/v1/topics/same-topic-id/recover"
    with TestClient(app) as client:
        assert client.post(path).status_code == 401
        assert (
            client.post(path, headers={**headers(b), "X-DeepTutor-Account": a["uid"]}).status_code
            == 403
        )
        assert (
            client.post(
                path, headers={**headers(a), "X-DeepTutor-Workspace": b["workspace"]}
            ).status_code
            == 404
        )
        assert a["store"].get_path_lease("same-topic-id") is not None
        assert b["store"].get_path_lease("same-topic-id") is not None
        assert client.post(path, headers=headers(a)).json() == {
            "status": "clear",
            "recovered": True,
        }
        assert a["store"].get_path_lease("same-topic-id") is None
        assert b["store"].get_path_lease("same-topic-id") is not None
