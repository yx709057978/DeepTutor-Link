import asyncio
import builtins
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from deeptutor_link.adapter import assess
from deeptutor_link.installer import patched_bytes


@pytest.mark.parametrize("objective", ["concept-original", "memory-original"])
@pytest.mark.parametrize("result", ["correct", "partial", "incorrect"])
@pytest.mark.parametrize("seeded", [False, True])
def test_nonfinal_verdict_never_changes_learning_state(
    host_store, assessment, objective, result, seeded
):
    if seeded:
        assess(
            host_store,
            "original-topic",
            assessment.model_copy(update={"objective_id": objective}),
            "",
        )
    before = host_store.load("original-topic")
    request = assessment.model_copy(
        update={
            "assessment_id": str(uuid4()),
            "expected_revision": before.version,
            "objective_id": objective,
            "result": result,
            "confidence": 0.1,
        }
    )
    for _ in range(2):
        receipt = assess(host_store, before.book_id, request, "")
        assert receipt["result"] == "ungraded"
        assert receipt["final"] is False
        assert receipt["saved"] is False
        assert receipt["needs_clarification"] is True
        assert receipt["revision"] == before.version
        assert host_store.load(before.book_id).model_dump() == before.model_dump()
        assert host_store.get_interaction(before.book_id, f"codex-{request.assessment_id}") is None
        assert host_store.get_path_lease(before.book_id) is None


def test_confidence_boundary_reuses_original_verdict(host_store, assessment):
    from deeptutor.learning.semantic_assessment import Verdict

    request = assessment.model_copy(update={"confidence": 0.8})
    receipt = assess(host_store, "original-topic", request, "")
    expected = Verdict(
        result=request.result, confidence=request.confidence, feedback=request.feedback
    ).receipt()
    assert {key: receipt[key] for key in expected} == expected
    assert receipt["saved"] is True
    assert len(host_store.load("original-topic").learning_evidence) == 1


async def test_original_web_admission_and_admin_cleanup_cannot_steal_codex_lease(
    host_store, assessment, monkeypatch
):
    from deeptutor.api.routers import mastery_path
    from deeptutor.services.session.turns.learning_adapter import LearningTurnAdapter
    from fastapi import HTTPException

    monkeypatch.setattr("deeptutor.learning.storage.LearningStore", lambda: host_store)
    monkeypatch.setattr(mastery_path, "LearningStore", lambda: host_store)
    cancel = AsyncMock(side_effect=AssertionError("must not cancel an extension mutation"))
    monkeypatch.setattr(
        mastery_path,
        "_turn_application_service",
        lambda: SimpleNamespace(cancel_turn_and_wait=cancel),
    )
    get_turn = AsyncMock(return_value=None)
    web = LearningTurnAdapter()
    web.store = SimpleNamespace(get_turn=get_turn)
    acquired, finish = Event(), Event()
    original_acquire = host_store.acquire_path_lease

    def paused_acquire(path_id, session_id, turn_id, **kwargs):
        lease = original_acquire(path_id, session_id, turn_id, **kwargs)
        if turn_id.startswith("codex-"):
            acquired.set()
            if not finish.wait(timeout=10):
                raise AssertionError("test did not release paused assessment")
        return lease

    monkeypatch.setattr(host_store, "acquire_path_lease", paused_acquire)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(assess, host_store, "original-topic", assessment, "")
        try:
            assert await asyncio.to_thread(acquired.wait, 5)
            lease = host_store.get_path_lease("original-topic")
            assert lease.session_id == "__path_api__"
            with pytest.raises(RuntimeError, match="mastery_path_busy"):
                await web._acquire_mastery_path_lease(
                    path_id="original-topic",
                    session_id="web-session",
                    turn_id="web-turn",
                    owns_path=False,
                )
            with pytest.raises(HTTPException) as exc:
                async with mastery_path._exclusive_path_mutation("original-topic"):
                    pytest.fail("web administrative write stole the lease")
            assert exc.value.status_code == 409
            assert host_store.get_path_lease("original-topic").turn_id == lease.turn_id
            get_turn.assert_not_awaited()
            cancel.assert_not_awaited()
        finally:
            finish.set()
            receipt = await asyncio.wrap_future(future)
    assert receipt["saved"] is True
    assert host_store.get_path_lease("original-topic") is None
    await web._acquire_mastery_path_lease(
        path_id="original-topic", session_id="web-session", turn_id="web-turn", owns_path=False
    )
    assert host_store.get_path_lease("original-topic").turn_id == "web-turn"
    host_store.release_path_lease("original-topic", turn_id="web-turn")


@pytest.fixture
def guarded_service(host_store):
    import deeptutor.learning.service as host_service
    from conftest import SOURCE

    name = "learning/service.py"
    path = SOURCE / "deeptutor" / name
    source = patched_bytes(name, path.read_bytes())
    namespace = dict(vars(host_service))
    exec(compile(source, str(path), "exec"), namespace)
    return namespace["LearningService"](host_store)


@pytest.mark.parametrize("action", ["void", "correct"])
async def test_original_repair_entrypoints_explicitly_refuse_codex_qualitative(
    host_store, assessment, guarded_service, monkeypatch, action
):
    from deeptutor.capabilities.mastery import tools
    from deeptutor.learning.review_strategy import scheduler_for_progress
    from deeptutor.learning.service import MasteryInteractionError

    assess(host_store, "original-topic", assessment, "")
    before = host_store.load("original-topic")
    question_id = f"codex-{assessment.assessment_id}"
    original_interaction = host_store.get_interaction(before.book_id, question_id).model_dump()
    kwargs = {
        "action": action,
        "reason": "Review the external assessment",
        "expected_answer": "A revised answer key",
        "scheduler": scheduler_for_progress(before),
    }
    with pytest.raises(MasteryInteractionError, match="AI assistant qualitative assessment repair"):
        guarded_service.repair_question(before.book_id, question_id, **kwargs)
    with pytest.raises(MasteryInteractionError, match="AI assistant qualitative assessment repair"):
        await guarded_service.repair_question_semantic(before.book_id, question_id, **kwargs)
    monkeypatch.setattr(tools, "_new_service", lambda: guarded_service)
    result = await tools.MasteryRepairQuestionTool().execute(
        _mastery_path_id=before.book_id, question_id=question_id, **kwargs
    )
    assert result.success is False
    assert "No record was changed" in result.content
    assert host_store.load(before.book_id).model_dump() == before.model_dump()
    assert (
        host_store.get_interaction(before.book_id, question_id).model_dump() == original_interaction
    )


def test_original_native_question_repair_still_voids_attempts(host_store, guarded_service):
    from deeptutor.learning.models import InteractionStatus, MasteryInteraction, PendingQuestion
    from deeptutor.learning.review_strategy import scheduler_for_progress

    with host_store.transaction("original-topic") as tx:
        scheduler = scheduler_for_progress(tx.progress)
        guarded_service._apply_grade(
            tx.progress,
            question_id="web-question",
            knowledge_point_id="memory-original",
            module_id="module-original",
            user_answer="native answer",
            expected_answer="native answer",
            question_type="short",
            scheduler=scheduler,
        )
        tx.put_interaction(
            MasteryInteraction(
                interaction_id="web-question",
                path_id="original-topic",
                status=InteractionStatus.GRADED,
                question=PendingQuestion(
                    question_id="web-question",
                    knowledge_point_id="memory-original",
                    module_id="module-original",
                    prompt="A native web question",
                ),
            )
        )
        tx.touch()
    progress, details = guarded_service.repair_question(
        "original-topic",
        "web-question",
        action="void",
        reason="Invalid native question",
        scheduler=scheduler,
    )
    assert details["action"] == "void"
    assert progress.quiz_attempts[0].voided is True
    assert not progress.learning_evidence
    assert progress.mastery_levels["memory-original"] == 0
    assert "memory-original" not in progress.repetition_states


def test_codex_quantitative_record_retains_original_void_semantics(
    host_store, assessment, guarded_service
):
    from deeptutor.learning.review_strategy import scheduler_for_progress

    request = assessment.model_copy(update={"objective_id": "memory-original"})
    assess(host_store, "original-topic", request, "")
    question_id = f"codex-{request.assessment_id}"
    before = host_store.load("original-topic")
    progress, details = guarded_service.repair_question(
        before.book_id,
        question_id,
        action="void",
        reason="Invalid external question",
        scheduler=scheduler_for_progress(before),
    )
    assert details["action"] == "void"
    assert progress.quiz_attempts[0].voided is True
    assert not progress.learning_evidence
    assert progress.mastery_levels["memory-original"] == 0
    assert "memory-original" not in progress.repetition_states


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("objective", ["concept-original", "memory-original"])
async def test_repair_without_extension_imports(
    host_store, assessment, guarded_service, monkeypatch, external, objective
):
    from deeptutor.learning.review_strategy import scheduler_for_progress
    from deeptutor.learning.service import MasteryInteractionError

    assess(
        host_store, "original-topic", assessment.model_copy(update={"objective_id": objective}), ""
    )
    question_id = f"codex-{assessment.assessment_id}"
    if not external:
        with host_store.transaction("original-topic") as tx:
            interaction = tx.get_interaction(question_id)
            interaction.result.pop("receipt")
            tx.put_interaction(interaction)
            tx.touch()
    before = host_store.load("original-topic")
    original_import = builtins.__import__

    def missing_package(name, *args, **kwargs):
        if name == "deeptutor_link" or name.startswith("deeptutor_link."):
            raise ModuleNotFoundError("Extension package removed")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_package)
    kwargs = {
        "action": "void",
        "reason": "Invalid assessment",
        "scheduler": scheduler_for_progress(before),
    }
    if external and objective == "concept-original":
        for method in (guarded_service.repair_question, guarded_service.repair_question_semantic):
            with pytest.raises(MasteryInteractionError, match="No record was changed"):
                value = method(before.book_id, question_id, **kwargs)
                if asyncio.iscoroutine(value):
                    await value
        with pytest.raises(MasteryInteractionError, match="No record was changed"):
            await guarded_service.repair_question_semantic(
                before.book_id,
                question_id,
                **{**kwargs, "action": "correct", "expected_answer": "key"},
            )
        assert host_store.load(before.book_id).model_dump() == before.model_dump()
    else:
        _, details = guarded_service.repair_question(before.book_id, question_id, **kwargs)
        assert details["action"] == "void"
