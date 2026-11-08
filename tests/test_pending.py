import json
from uuid import uuid4

import pytest

from deeptutor_link.adapter import BridgeError, context
from deeptutor_link.models import PendingAnswer
from deeptutor_link.pending import answer_pending_question, pending_question


def register(store, *, legacy=False, question=None):
    from deeptutor.learning.models import PendingQuestion
    from deeptutor.learning.service import LearningService

    question = question or PendingQuestion(
        question_id="original-web-question",
        knowledge_point_id="memory-original",
        module_id="module-original",
        prompt="Explain the original memory objective.",
        expected_answer="PRIVATE_KEY",
        explanation="PRIVATE_RUBRIC",
    )
    if legacy:
        progress = store.load("original-topic")
        progress.pending_question = question
        store.save(progress)
    else:
        LearningService(store).register_question(
            "original-topic", question, session_id="original-web-session", turn_id="original-web-turn"
        )
    return question


def request_for(store, **updates):
    state = pending_question(store, "original-topic", "ws")
    return PendingAnswer(**{
        "request_id": str(uuid4()),
        "expected_revision": state["revision"],
        "question_id": state["question_id"],
        "state_fingerprint": state["state_fingerprint"],
        "learner_answer": "A real synthetic answer to the original question.",
        "result": "correct",
        "confidence": 0.95,
        "feedback": "The original objective is demonstrated by this learner answer.",
        **updates,
    })


@pytest.mark.parametrize("legacy", [False, True])
def test_original_pending_uses_host_service_without_models_and_replays(host_store, monkeypatch, legacy):
    from deeptutor.learning.service import LearningService
    from deeptutor.services.llm import config

    def unavailable(*args, **kwargs):
        raise AssertionError("No host LLM is allowed")

    monkeypatch.setattr(config, "get_llm_config", unavailable)
    monkeypatch.setattr(LearningService, "grade_interaction_semantic", unavailable)
    original = register(host_store, legacy=legacy)
    before = host_store.load("original-topic").model_dump(mode="json")
    state = pending_question(host_store, "original-topic", "ws")
    assert state["question"]["prompt"] == original.prompt
    assert state["mutation_lease"]["status"] == "clear"
    assert state["continuation"]["supported"] is True
    assert not any(secret in json.dumps(state) for secret in ["PRIVATE_KEY", "PRIVATE_RUBRIC"])
    assert host_store.load("original-topic").model_dump(mode="json") == before
    request = request_for(host_store)
    receipt = answer_pending_question(host_store, "original-topic", request, "ws")
    assert receipt["saved"] and receipt["progress_recorded"] and receipt["final"]
    assert receipt["revision"] == before["version"] + 1
    assert receipt["host_chat_model_used"] is False
    progress = host_store.load("original-topic")
    stored = host_store.get_interaction("original-topic", original.question_id)
    assert stored.status.value == "graded"
    assert stored.user_answer == request.learner_answer
    assert stored.question.expected_answer == original.expected_answer
    if not legacy:
        assert stored.session_id == "original-web-session" and stored.turn_id == "original-web-turn"
    assert len(progress.quiz_attempts) == len(progress.learning_evidence) == 1
    assert progress.repetition_states["memory-original"].review_count == 1
    assert progress.quiz_attempts[0].question_id == original.question_id
    assert progress.pending_question is None
    assert not pending_question(host_store, "original-topic", "ws")["pending"]["present"]
    assert answer_pending_question(host_store, "original-topic", request, "ws") == {**receipt, "deduplicated": True}
    assert len(host_store.load("original-topic").learning_evidence) == 1
    assert host_store.load("original-topic").repetition_states["memory-original"].review_count == 1
    with pytest.raises(BridgeError) as exc:
        answer_pending_question(host_store, "original-topic", request.model_copy(update={"result": "incorrect"}), "ws")
    assert exc.value.code == "request_id_conflict"


def test_pending_preserves_committed_answer_and_ungraded_state(host_store):
    from deeptutor.learning.service import LearningService

    original = register(host_store)
    LearningService(host_store).record_question_answer(
        "original-topic", "An already submitted real learner answer", interaction_id=original.question_id
    )
    state = pending_question(host_store, "original-topic", "ws")
    assert state["stored_answer"] == "An already submitted real learner answer"
    with pytest.raises(BridgeError) as exc:
        answer_pending_question(host_store, "original-topic", request_for(host_store), "ws")
    assert exc.value.code == "stored_answer_mismatch"
    request = request_for(host_store, learner_answer=state["stored_answer"], confidence=0.2)
    receipt = answer_pending_question(host_store, "original-topic", request, "ws")
    assert receipt["saved"] is True and receipt["progress_recorded"] is False
    progress = host_store.load("original-topic")
    assert progress.pending_question.question_id == original.question_id
    assert not progress.learning_evidence and not progress.quiz_attempts
    assert answer_pending_question(host_store, "original-topic", request, "ws")["deduplicated"]
    assert pending_question(host_store, "original-topic", "ws")["stored_answer"] == state["stored_answer"]


def test_pending_stale_question_and_web_lease_never_mutate(host_store):
    from deeptutor.learning.storage import LearningConflictError

    register(host_store)
    request = request_for(host_store)
    host_store.acquire_path_lease("original-topic", "other-client", "web-turn")
    assert context(host_store, "original-topic", "ws")["pending"]["blocks_maintenance_by_itself"] is False
    with pytest.raises(BridgeError) as exc:
        answer_pending_question(host_store, "original-topic", request, "ws")
    assert exc.value.code == "path_busy"
    assert host_store.get_path_lease("original-topic").turn_id == "web-turn"
    host_store.release_path_lease("original-topic", turn_id="web-turn")
    with pytest.raises(BridgeError) as exc:
        answer_pending_question(host_store, "original-topic", request.model_copy(update={"state_fingerprint": "0" * 64}), "ws")
    assert exc.value.code == "pending_question_changed"
    progress = host_store.load("original-topic")
    progress.name = "Changed from website"
    host_store.save(progress)
    with pytest.raises(LearningConflictError):
        answer_pending_question(host_store, "original-topic", request, "ws")
    assert not host_store.load("original-topic").learning_evidence


def test_pending_source_validation_is_atomic(host_store):
    from deeptutor.learning.models import PendingQuestion

    register(host_store, legacy=True, question=PendingQuestion(
        question_id="invalid-source-question", knowledge_point_id="memory-original",
        prompt="Question with obsolete source evidence", source_evidence={"status": "ready"},
    ))
    before = host_store.load("original-topic").model_dump(mode="json")
    state = pending_question(host_store, "original-topic", "ws")
    assert state["continuation"]["blocker"] == "pending_question_binding_required"
    with pytest.raises(BridgeError) as exc:
        answer_pending_question(host_store, "original-topic", request_for(host_store), "ws")
    assert exc.value.code == "pending_question_binding_required"
    assert host_store.load("original-topic").model_dump(mode="json") == before
    assert host_store.get_interaction("original-topic", "invalid-source-question") is None


def test_pending_clarified_answer_uses_original_audited_revision(host_store):
    register(host_store)
    initial = request_for(host_store, result="ungraded", confidence=0.3)
    answer_pending_question(host_store, "original-topic", initial, "ws")
    state = pending_question(host_store, "original-topic", "ws")
    assert state["answer_revision_allowed"] is True
    assert state["previous_assessment"]["final"] is False
    clarified = request_for(host_store, learner_answer="A real clarified answer from the learner.")
    with pytest.raises(BridgeError) as exc:
        answer_pending_question(host_store, "original-topic", clarified, "ws")
    assert exc.value.code == "stored_answer_mismatch"
    clarified = clarified.model_copy(update={"revise_answer": True})
    receipt = answer_pending_question(host_store, "original-topic", clarified, "ws")
    assert receipt["progress_recorded"] is True
    assert answer_pending_question(host_store, "original-topic", clarified, "ws")["deduplicated"]
    interaction = host_store.get_interaction("original-topic", initial.question_id)
    assert interaction.user_answer == clarified.learner_answer
    with host_store.transaction("original-topic") as tx:
        rows = tx._conn.execute(
            "SELECT payload_json FROM mastery_events WHERE path_id=? AND event_type=?",
            ("original-topic", "assessment.revised"),
        ).fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0]["payload_json"])["previous_answer"] == initial.learner_answer
    assert len(host_store.load("original-topic").learning_evidence) == 1


def test_pending_assistance_preserves_original_host_gate(host_store):
    register(host_store)
    answer_pending_question(host_store, "original-topic", request_for(host_store, independent=False), "ws")
    progress = host_store.load("original-topic")
    assert progress.quiz_attempts[0].independent is False
    assert progress.learning_evidence[0].quality == 0.6
    assert progress.mastery_levels["memory-original"] == 0
    assert progress.repetition_states["memory-original"].review_count == 1


@pytest.mark.parametrize("group", [False, True])
def test_pending_group_and_visual_are_preserved_with_specific_blockers(host_store, group):
    from deeptutor.learning.models import PendingQuestion

    leaf = PendingQuestion(
        question_id="protected-leaf", knowledge_point_id="memory-original",
        prompt="Original question with its own evidence contract.",
        expected_answer="Original answer key",
    )
    other = leaf.model_copy(update={"question_id": "other-leaf", "prompt": "A different original question."})
    question = PendingQuestion(
        question_id="protected-original", knowledge_point_id="memory-original",
        prompt="Original question with its own evidence contract.",
        **({"question_type": "group", "questions": [leaf, other]} if group else {"visual_context": {"required": True}}),
    )
    register(host_store, question=question)
    before = host_store.load("original-topic").model_dump(mode="json")
    state = pending_question(host_store, "original-topic", "ws")
    blocker = "pending_group_unsupported" if group else "pending_visual_review_required"
    assert state["continuation"]["supported"] is False
    assert state["continuation"]["blocker"] == blocker
    with pytest.raises(BridgeError) as exc:
        answer_pending_question(host_store, "original-topic", request_for(host_store), "ws")
    assert exc.value.code == blocker
    assert host_store.load("original-topic").model_dump(mode="json") == before


def test_original_pdf_pending_requires_binding_and_real_original_quote(http_host):
    from deeptutor.learning.models import PendingQuestion
    from deeptutor.learning.service import LearningService
    from deeptutor.multi_user.paths import user_context
    from deeptutor.services.workspace.context import workspace_context
    from fastapi.testclient import TestClient
    from test_http import headers
    from test_pdf_repair_workflows import _assessment, _make_pdf_source, _prepare

    from deeptutor_link.adapter import _bind_external_question
    from deeptutor_link.models import Assessment

    app, (account, _) = http_host
    source = _make_pdf_source(account)
    path = "/api/link/v1/topics/same-topic-id/"
    with TestClient(app) as client:
        _prepare(client, account, source)
        with user_context(account["user"]), workspace_context(account["workspace"]):
            store = account["store"]
            progress = store.load("same-topic-id")
            packet = progress.lesson_evidence["original-kp"]
            LearningService(store).register_question("same-topic-id", PendingQuestion(
                question_id="original-pdf-question", knowledge_point_id="original-kp",
                prompt="Explain permeability and diffusion from the original page.",
                expected_answer="Higher permeability increases diffusion rate.", source_evidence=packet,
            ))
        blocked = client.get(path + "pending-question", headers=headers(account)).json()
        assert blocked["continuation"]["blocker"] == "pending_question_binding_required"
        with user_context(account["user"]), workspace_context(account["workspace"]):
            assessment = Assessment(**_assessment(account, source))
            with store.transaction("same-topic-id") as tx:
                bound = _bind_external_question(
                    store, tx.progress, tx.progress.modules[0].knowledge_points[0],
                    assessment, packet, "original-pdf-question",
                )
                interaction = tx.active_interaction()
                interaction.question = bound
                tx.progress.pending_question = bound
                tx.put_interaction(interaction)
        state = client.get(path + "pending-question", headers=headers(account)).json()
        assert state["continuation"]["supported"] is True
        payload = {
            "request_id": str(uuid4()), "expected_revision": state["revision"],
            "question_id": state["question_id"], "state_fingerprint": state["state_fingerprint"],
            **{key: getattr(assessment, key) for key in ("learner_answer", "result", "confidence", "feedback", "source_ids")},
            "evidence_quotes": [{"source_id": "source-pdf", "filename": "lesson.pdf", "page_number": 1, "quote": "A fabricated quote that is not on the original page."}],
        }
        before = store.load("same-topic-id").model_dump(mode="json")
        bad = client.post(path + "answer-pending-question", headers=headers(account), json=payload)
        assert bad.status_code == 422, bad.text
        assert bad.json()["detail"]["code"] == "evidence_quote_not_found"
        assert store.load("same-topic-id").model_dump(mode="json") == before
        payload["evidence_quotes"] = [quote.model_dump() for quote in assessment.evidence_quotes]
        saved = client.post(path + "answer-pending-question", headers=headers(account), json=payload)
        assert saved.status_code == 200, saved.text
        assert saved.json()["progress_recorded"] is True
        progress = store.load("same-topic-id")
        assert len(progress.learning_evidence) == 1 and progress.pending_question is None
