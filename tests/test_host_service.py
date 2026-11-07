import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from deeptutor_link.adapter import BridgeError, assess, context


def test_continuation_projects_allowlist_without_answers(host_store):
    from deeptutor.learning.models import PendingQuestion

    p = host_store.load("original-topic")
    p.pending_question = PendingQuestion(
        question_id="web-pending",
        knowledge_point_id="concept-original",
        prompt="Actual web question",
        expected_answer="SECRET_ANSWER",
        explanation="SECRET_RUBRIC",
        source_evidence={"credentials": "SECRET_TOKEN"},
    )
    host_store.save(p)
    value = context(host_store, p.book_id, "workspace-original", limit=1)
    encoded = json.dumps(value)
    assert not any(
        secret in encoded for secret in ["SECRET_ANSWER", "SECRET_RUBRIC", "SECRET_TOKEN"]
    )
    assert value["objectives"][0]["id"] == "concept-original"
    assert value["preferences"]["preferences"] == "Chinese examples"
    assert value["pending"]["action"] == "finish_in_web"
    assert value["next_offset"] == 1
    assert "dt_workspace=workspace-original" in value["web_url"]


def test_assessment_retains_original_history_and_schedules(host_store, assessment):
    from deeptutor.learning.models import LearnerMasteryOverride
    from deeptutor.learning.policy import is_assessed_mastered

    before = host_store.load("original-topic")
    before.learner_mastery_overrides["memory-original"] = LearnerMasteryOverride(
        knowledge_point_id="memory-original", note="existing learner claim"
    )
    host_store.save(before)
    assessment = assessment.model_copy(update={"expected_revision": before.version})
    receipt = assess(host_store, before.book_id, assessment, "ws")
    saved = host_store.load(before.book_id)
    assert receipt["revision"] == before.version + 1
    assert is_assessed_mastered(saved, saved.modules[0].knowledge_points[0])
    assert len(saved.learning_evidence) == 1
    assert saved.learning_evidence[0].source == "codex_external_assessment"
    assert saved.learning_evidence[0].learner_answer == assessment.learner_answer
    assert saved.repetition_states["concept-original"].review_count == 1
    assert saved.learner_mastery_overrides == before.learner_mastery_overrides
    assert host_store.get_topic(saved.book_id).sources[0].id == "goal-original"
    assert host_store.get_path_lease(saved.book_id) is None
    assert host_store.list_session_ids(saved.book_id) == []


def test_exact_retry_is_idempotent_but_changed_payload_conflicts(host_store, assessment):
    first = assess(host_store, "original-topic", assessment, "")
    again = assess(host_store, "original-topic", assessment, "")
    assert again == {**first, "deduplicated": True}
    assert len(host_store.load("original-topic").learning_evidence) == 1
    with pytest.raises(BridgeError, match="another payload"):
        assess(
            host_store, "original-topic", assessment.model_copy(update={"result": "incorrect"}), ""
        )


def test_stale_check_is_inside_mutation_transaction(host_store, assessment):
    from deeptutor.learning.storage import LearningConflictError

    p = host_store.load("original-topic")
    p.name = "web changed the topic"
    host_store.save(p)
    with pytest.raises(LearningConflictError):
        assess(host_store, p.book_id, assessment, "")
    assert not host_store.load(p.book_id).learning_evidence
    assert host_store.get_path_lease(p.book_id) is None


def test_original_web_lease_is_not_cancelled(host_store, assessment):
    host_store.acquire_path_lease("original-topic", "web-session", "web-turn")
    with pytest.raises(BridgeError) as exc:
        assess(host_store, "original-topic", assessment, "")
    assert exc.value.code == "path_busy"
    assert host_store.get_path_lease("original-topic").turn_id == "web-turn"
    assert not host_store.load("original-topic").learning_evidence


def test_original_pending_interaction_is_not_overwritten(host_store, assessment):
    from deeptutor.learning.models import MasteryInteraction, PendingQuestion

    question = PendingQuestion(
        question_id="web-question",
        knowledge_point_id="concept-original",
        prompt="web question",
        expected_answer="secret",
    )
    with host_store.transaction("original-topic") as tx:
        tx.put_interaction(
            MasteryInteraction(
                interaction_id="web-question",
                path_id="original-topic",
                question=question,
                session_id="web-session",
            )
        )
        tx.progress.pending_question = question
        tx.touch()
    assessment = assessment.model_copy(
        update={"expected_revision": host_store.load("original-topic").version}
    )
    with pytest.raises(BridgeError) as exc:
        assess(host_store, "original-topic", assessment, "")
    assert exc.value.code == "pending_interaction"
    assert host_store.get_active_interaction("original-topic").interaction_id == "web-question"
    assert not host_store.load("original-topic").learning_evidence


def test_concurrent_writers_never_lose_or_double_apply(host_store, assessment):
    def write(_):
        try:
            return assess(host_store, "original-topic", assessment, "")
        except BridgeError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(write, range(6)))
    assert any(isinstance(r, dict) for r in results)
    assert len(host_store.load("original-topic").learning_evidence) == 1
    assert assess(host_store, "original-topic", assessment, "")["deduplicated"]


def test_quantitative_uses_host_recency_gate_and_assistance(host_store, assessment):
    request = assessment.model_copy(
        update={"objective_id": "memory-original", "independent": False}
    )
    assess(host_store, "original-topic", request, "")
    p = host_store.load("original-topic")
    assert p.quiz_attempts[0].independent is False
    assert p.mastery_levels["memory-original"] == 0
    assert p.learning_evidence[0].quality == 0.6
    request = request.model_copy(
        update={"assessment_id": str(uuid4()), "expected_revision": p.version, "independent": True}
    )
    assess(host_store, p.book_id, request, "")
    p = host_store.load(p.book_id)
    assert p.mastery_levels["memory-original"] == 0.5
    assert p.repetition_states["memory-original"].review_count == 2


def test_qualitative_assisted_cannot_claim_mastery(host_store, assessment):
    with pytest.raises(BridgeError) as exc:
        assess(
            host_store, "original-topic", assessment.model_copy(update={"independent": False}), ""
        )
    assert exc.value.code == "independent_answer_required"
    assert not host_store.load("original-topic").learning_evidence
