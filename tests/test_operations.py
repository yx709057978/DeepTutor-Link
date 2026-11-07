import pytest

from deeptutor_link.assets.skill.scripts.deeptutor_link import (
    OPS,
    operation_call,
    operation_catalog,
    scrub_answers,
)


class FakeClient:
    def __init__(self):
        self.calls = []

    def topic_path(self, suffix):
        return "/api/link/v1/topics/original-topic/" + suffix

    def call(self, method, path, data=None, params=None, timeout=60):
        self.calls.append((method, path, data, params))
        if path.endswith("/mastery/tools"):
            return {"tools": [{"function": {"name": "mastery_status"}}]}
        return {"ok": True}


def test_catalog_counts_only_wired_or_explicitly_unavailable_operations():
    catalog = operation_catalog()
    assert catalog["count"] == len(OPS)
    assert catalog["available_count"] == sum(spec["available"] for spec in OPS.values())
    assert catalog["unavailable_count"] == catalog["count"] - catalog["available_count"]
    assert catalog["operations"]["get_mastery_tutor_tools"]["available"]
    assert catalog["operations"]["call_mastery_tutor"]["available"]
    assert catalog["operations"]["read_mastery_paths"]["available"]
    assert catalog["operations"]["send_message_to_deeptutor"]["available"]
    assert catalog["operations"]["read_mastery_status"]["available"]
    assert catalog["operations"]["list_mastery_paths"]["available"]
    assert catalog["operations"]["get_deeptutor_session"]["available"]
    assert catalog["operations"]["get_deeptutor_session"]["optional"] == ["offset", "limit"]
    assert not catalog["operations"]["list_courses"]["available"]


def test_mastery_and_knowledge_operations_use_extension_contract():
    client = FakeClient()
    operation_call(client, "get_mastery_review_settings", {"path_id": "topic/1"})
    assert client.calls[-1][1] == "/api/link/v1/topics/topic%2F1/review-settings"

    operation_call(
        client,
        "get_mastery_objective",
        {"path_id": "topic/1", "knowledge_point_id": "kp/1"},
    )
    assert client.calls[-1][1] == "/api/link/v1/topics/topic%2F1/objectives/kp%2F1"

    operation_call(
        client,
        "search_vector_knowledge",
        {"objective_id": "kp", "source_id": "source", "query": "bounded"},
    )
    assert client.calls[-1] == (
        "POST",
        "/api/link/v1/topics/original-topic/retrieve",
        {"objective_id": "kp", "source_id": "source", "query": "bounded", "top_k": 5},
        None,
    )

    operation_call(
        client,
        "read_knowledge_file",
        {"kb_name": "kb", "filename": "folder/chapter.txt", "offset": 4},
    )
    assert client.calls[-1][1] == "/api/link/v1/knowledge-bases/kb/file-slice"
    assert client.calls[-1][3] == {
        "filename": "folder/chapter.txt",
        "offset": 4,
    }

    operation_call(
        client,
        "list_knowledge_files",
        {"kb_name": "kb", "suffix": ".md", "limit": 10, "offset": 20},
    )
    assert client.calls[-1] == (
        "GET",
        "/api/link/v1/knowledge-bases/kb/files",
        None,
        {"suffix": ".md", "limit": 10, "offset": 20},
    )


def test_native_tool_schemas_and_direct_turns_are_explicit_operations():
    client = FakeClient()
    operation_call(client, "get_mastery_tutor_tools", {})
    assert client.calls[-1][:2] == ("GET", "/api/link/v1/mastery/tools")
    result = operation_call(client, "read_mastery_status", {})
    assert client.calls[-1] == (
        "GET",
        "/api/link/v1/topics/original-topic/mastery/status",
        None,
        None,
    )
    assert result["ok"] is True
    native = {
        "path_id": "original-topic",
        "session_id": "codex-learning-session",
        "turn_id": "learner-turn-1",
        "request_id": "12345678-1234-4234-8234-123456789abc",
        "tool": "mastery_profile",
        "arguments": {"preferences": "Use concrete examples."},
        "user_message": "Please use concrete examples.",
        "confirm": True,
    }
    operation_call(client, "call_mastery_tutor", native)
    assert client.calls[-1][:3] == (
        "POST",
        "/api/link/v1/topics/original-topic/mastery/tool",
        {key: value for key, value in native.items() if key != "path_id"},
    )

    args = {
        "capability": "chat",
        "message": "Ask DeepTutor directly about this topic.",
        "request_id": "22345678-1234-4234-8234-123456789abc",
        "direct_dt_conversation": True,
        "confirm": True,
    }
    operation_call(client, "send_message_to_deeptutor", args)
    assert client.calls[-1][1] == "/api/link/v1/capability/turn"
    assert client.calls[-1][2] == args
    operation_call(client, "send_message_to_deeptutor", {**args, "capability": "mastery_path"})
    assert client.calls[-1][1] == "/api/link/v1/topics/original-topic/capability/turn"
    assert client.calls[-1][2]["capability"] == "mastery_path"


def test_direct_turns_require_explicit_confirmation_and_reply_identity():
    client = FakeClient()
    requests = {
        "call_mastery_tutor": {
            "path_id": "original-topic",
            "session_id": "codex-learning-session",
            "turn_id": "learner-turn-1",
            "request_id": "12345678-1234-4234-8234-123456789abc",
            "tool": "mastery_profile",
            "arguments": {"preferences": "Use concrete examples."},
        },
        "send_message_to_deeptutor": {
            "capability": "chat",
            "message": "Call DeepTutor explicitly.",
            "request_id": "22345678-1234-4234-8234-123456789abc",
            "direct_dt_conversation": True,
        },
    }
    for operation, args in requests.items():
        with pytest.raises(ValueError):
            operation_call(client, operation, args)
        with pytest.raises(ValueError, match="confirm=true"):
            operation_call(client, operation, {**args, "confirm": False})
    assert client.calls == []
    reply = {
        "session_id": "session-1",
        "turn_id": "turn-1",
        "request_id": "22345678-1234-4234-8234-123456789abc",
        "message": "My actual reply",
        "confirm": True,
    }
    operation_call(client, "reply_to_deeptutor_turn", reply)
    assert client.calls[-1][1] == "/api/link/v1/capability/turn/reply"
    assert client.calls[-1][2]["request_id"] == reply["request_id"]


def test_read_mastery_paths_uses_scoped_host_tool_bridge():
    client = FakeClient()
    operation_call(client, "read_mastery_paths", {"active_path_id": "original-topic"})
    assert client.calls[-1] == (
        "GET",
        "/api/link/v1/mastery/paths",
        None,
        {"active_path_id": "original-topic"},
    )


def test_read_only_mastery_status_uses_bound_topic_and_rejects_other_paths():
    client = FakeClient()
    result = operation_call(client, "read_mastery_status", {})
    assert result["ok"] is True
    assert client.calls[-1][1] == "/api/link/v1/topics/original-topic/mastery/status"
    calls_before = len(client.calls)
    try:
        operation_call(client, "read_mastery_status", {"path_id": "other-topic"})
    except ValueError as exc:
        assert "Unknown operation argument" in str(exc)
    else:
        raise AssertionError("status bridge must remain bound to configured topic")
    assert len(client.calls) == calls_before


def test_mastery_path_listing_uses_compact_host_index():
    client = FakeClient()
    operation_call(client, "list_mastery_paths", {"limit": 10, "offset": 20})
    assert client.calls[-1][:2] == ("GET", "/api/mastery-paths/topics/index")
    assert client.calls[-1][2:] == (None, None)


def test_mastery_path_listing_paginates_the_host_index():
    class TopicClient(FakeClient):
        def call(self, method, path, data=None, params=None):
            self.calls.append((method, path, data, params))
            return {"topics": [{"path_id": str(i)} for i in range(27)]}

    client = TopicClient()
    result = operation_call(client, "list_mastery_paths", {"limit": 10, "offset": 20})
    assert [row["path_id"] for row in result["topics"]] == [str(i) for i in range(20, 27)]
    assert result["total"] == 27
    assert result["next_offset"] is None


def test_custom_operation_handlers_enforce_declared_arguments():
    client = FakeClient()
    for args in ({}, {"path_id": "topic", "unexpected": True}):
        try:
            operation_call(client, "get_mastery_path", args)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid operation arguments must be rejected")
    assert client.calls == []


def test_learner_override_supplies_host_required_note():
    client = FakeClient()
    operation_call(
        client,
        "set_mastery_learner_override",
        {
            "path_id": "topic",
            "knowledge_point_id": "objective",
            "mastered": True,
            "expected_revision": 2,
            "confirm": True,
        },
    )
    assert client.calls[-1][2] == {
        "mastered": True,
        "expected_revision": 2,
        "note": "",
    }


def test_learning_note_and_practice_review_map_explicit_user_data_only():
    client = FakeClient()
    operation_call(
        client,
        "add_notebook_record",
        {
            "notebook_id": "notes-1",
            "title": "Learning note",
            "note": "Only this requested note is saved.",
            "summary": "A short explicit summary.",
            "confirm": True,
        },
    )
    method, path, body, _ = client.calls[-1]
    assert (method, path) == ("POST", "/api/notebooks/actions/add-record")
    assert body["notebook_ids"] == ["notes-1"]
    assert body["output"] == "Only this requested note is saved."
    assert body["user_query"] == "User-requested learning note"
    assert "whole AI assistant" not in body["output"]

    operation_call(
        client,
        "record_practice_review",
        {
            "entry_id": 12,
            "request_id": "practice-review-000001",
            "version": 3,
            "rating": "good",
            "answer": "The learner's actual submitted answer.",
            "confirm": True,
        },
    )
    method, path, body, _ = client.calls[-1]
    assert (method, path) == (
        "POST",
        "/api/question-notebook/practice/questions/12/review",
    )
    assert body == {
        "request_id": "practice-review-000001",
        "version": 3,
        "rating": "good",
        "answer": "The learner's actual submitted answer.",
    }


def test_practice_reads_strip_answer_keys_and_explanations():
    assert scrub_answers({
        "question": "A prompt",
        "correct_answer": "hidden",
        "explanation": "also hidden",
        "nested": {"answer_key": "secret", "label": "A"},
    }) == {"question": "A prompt", "nested": {"label": "A"}}


def test_question_upsert_preserves_choice_options():
    client = FakeClient()
    options = {"A": "First choice", "B": "Second choice"}
    operation_call(
        client,
        "upsert_question_entry",
        {
            "question_id": "choice-question-1",
            "question": "Choose the correct answer.",
            "origin_ref": "codex-test",
            "question_type": "choice",
            "options": options,
            "correct_answer": "A",
            "confirm": True,
        },
    )
    assert client.calls[-1][2]["options"] == options
    assert "options" in OPS["upsert_question_entry"]["optional"]
    assert "options" in OPS["upsert_question_entry"]["body"]
