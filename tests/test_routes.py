import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from deeptutor_link.adapter import BridgeError, assess
from deeptutor_link.models import RouteCommit, RouteCreate, RoutePreview
from deeptutor_link.routes import commit_route, create_route, preview_route, read_route


def create_request(**updates):
    return RouteCreate.model_validate(
        {
            "creation_id": str(uuid4()),
            "name": "Synthetic route",
            "goal": "Learn synthetic concepts",
            "profile": {"preferences": "Chinese examples", "time_budget": "30 minutes a day"},
            "modules": [
                {
                    "name": "Foundations",
                    "objective": "Understand the foundations",
                    "pass_threshold": 0.8,
                    "knowledge_points": [
                        {"client_ref": "first", "name": "First concept"},
                        {"name": "Second concept", "prerequisite_ids": ["first"]},
                    ],
                }
            ],
            **updates,
        }
    )


def preview_request(store, modules=None, **updates):
    route = read_route(store, "original-topic", "ws")
    return RoutePreview.model_validate(
        {
            "request_id": str(uuid4()),
            "expected_revision": route["revision"],
            "modules": modules if modules is not None else route["modules"],
            **updates,
        }
    )


def changed_request(store):
    request = preview_request(store)
    request.modules[0].name = "Updated module"
    request.modules[0].knowledge_points.append(
        request.modules[0].knowledge_points[0].model_copy(update={"id": "", "name": "New concept"})
    )
    return request


def commit(store, preview, request_id=None):
    return commit_route(
        store,
        "original-topic",
        RouteCommit(request_id=request_id or str(uuid4()), preview_id=preview["preview_id"]),
        "ws",
    )


def test_create_relations_profile_and_idempotent_receipt(host_store):
    request = create_request()
    first = create_route(host_store, request, "ws")
    topic_id = first["topic_id"]
    p = host_store.load(topic_id)
    assert p.version == first["revision"] == 1
    assert p.name == request.name
    assert p.learner_profile.preferences == "Chinese examples"
    assert p.modules[0].pass_threshold == 0.8
    assert p.modules[0].objective == "Understand the foundations"
    points = p.modules[0].knowledge_points
    assert points[1].prerequisite_ids == [points[0].id]
    assert create_route(host_store, request, "ws") == {**first, "deduplicated": True}
    assert host_store.load(topic_id).model_dump() == p.model_dump()
    assert host_store.get_path_lease(topic_id) is None
    assert host_store.list_session_ids(topic_id) == []
    with pytest.raises(BridgeError) as exc:
        create_route(host_store, request.model_copy(update={"name": "Changed"}), "ws")
    assert exc.value.code == "idempotency_conflict"


@pytest.mark.parametrize("kind", ["cycle", "unknown", "duplicate", "blank", "too_many"])
def test_invalid_create_rolls_back_without_empty_topic(host_store, kind):
    request = create_request()
    points = request.modules[0].knowledge_points
    if kind == "cycle":
        points[0].prerequisite_ids = ["first"]
    elif kind == "unknown":
        points[1].topic_source_ids = ["foreign-source"]
    elif kind == "duplicate":
        points[1].client_ref = "first"
    elif kind == "blank":
        request.name = "   "
    else:
        request.modules[0].knowledge_points = [points[0].model_copy() for _ in range(8)]
    with pytest.raises(BridgeError):
        create_route(host_store, request, "ws")
    assert not host_store.exists("topic_" + request.creation_id.replace("-", ""))


def test_preview_commit_preserves_history_visual_gates_and_failure_state(host_store, assessment):
    assess(host_store, "original-topic", assessment, "ws")
    p = host_store.load("original-topic")
    p.stage_failure_counts = {"stage": 2}
    p.stage_failure_notes = {"stage": "PRIVATE_FAILURE_NOTE"}
    p.modules[0].objective = "Original purpose"
    p.modules[0].pass_threshold = 0.9
    p.modules[0].knowledge_points[0].required_visual_tasks = ["identification"]
    p.lesson_evidence = {"concept-original": {"secret": "PRIVATE_PACKET"}}
    host_store.save(p)
    before = host_store.load("original-topic")
    request = changed_request(host_store)
    request.modules[0].pass_threshold = None
    request.modules[0].objective = None
    preview = preview_route(host_store, "original-topic", request, "ws")
    assert preview["saved"] is False
    assert host_store.load("original-topic").model_dump() == before.model_dump()
    output = json.dumps(preview)
    for secret in ("PRIVATE_FAILURE_NOTE", "PRIVATE_PACKET", assessment.learner_answer):
        assert secret not in output
    assert preview["identity_map"]["preserved"] == ["concept-original", "memory-original"]
    assert len(preview["identity_map"]["minted"]) == 1
    assert (
        preview_route(host_store, "original-topic", request, "ws")["preview_id"]
        == preview["preview_id"]
    )
    assert (
        len(
            [
                e
                for e in host_store.list_events("original-topic")
                if e.event_type == "path.modules_previewed"
            ]
        )
        == 1
    )
    commit_id = str(uuid4())
    committed = commit(host_store, preview, commit_id)
    saved = host_store.load("original-topic")
    assert committed["revision"] == before.version + 1
    assert saved.learning_evidence == before.learning_evidence
    assert saved.repetition_states == before.repetition_states
    assert saved.stage_failure_notes == before.stage_failure_notes
    assert saved.stage_failure_counts == before.stage_failure_counts
    assert saved.lesson_evidence == before.lesson_evidence
    assert saved.modules[0].knowledge_points[0].required_visual_tasks == ["identification"]
    assert saved.modules[0].pass_threshold == 0.9
    assert saved.modules[0].objective == "Original purpose"
    assert commit(host_store, preview, commit_id)["replayed"] is True
    assert host_store.load("original-topic").model_dump() == saved.model_dump()


@pytest.mark.parametrize("change", ["delete", "rename", "type", "sources"])
def test_learned_objective_is_protected(host_store, assessment, change):
    from deeptutor.learning.models import TopicSource

    assess(host_store, "original-topic", assessment, "ws")
    topic = host_store.get_topic("original-topic")
    host_store.put_topic(
        topic.metadata,
        [
            *topic.sources,
            TopicSource(
                id="another-source",
                kind="knowledge_base",
                source_id="kb",
                label="Existing material",
            ),
        ],
    )
    before = host_store.load("original-topic")
    request = changed_request(host_store)
    point = request.modules[0].knowledge_points[0]
    if change == "delete":
        del request.modules[0].knowledge_points[0]
    elif change == "rename":
        point.name = "Entirely new concept"
    elif change == "type":
        point.type = "memory"
    else:
        point.topic_source_ids = ["another-source"]
    with pytest.raises(BridgeError) as exc:
        preview_route(host_store, "original-topic", request, "ws")
    assert exc.value.code == "objective_has_history"
    assert host_store.load("original-topic").model_dump() == before.model_dump()
    assert not any(
        e.event_type == "path.modules_previewed" for e in host_store.list_events("original-topic")
    )


def test_unlearned_objective_can_be_replaced_and_references_remap(host_store):
    request = preview_request(host_store)
    points = request.modules[0].knowledge_points
    points[0].name = "Revised concept"
    points[1].prerequisite_ids = [points[0].id]
    preview = preview_route(host_store, "original-topic", request, "ws")
    assert preview["identity_map"]["dropped"] == ["concept-original"]
    commit(host_store, preview)
    points = host_store.load("original-topic").modules[0].knowledge_points
    assert points[0].id != "concept-original"
    assert points[1].prerequisite_ids == [points[0].id]
    assert points[1].id == "memory-original"


def test_unlearned_deletion_and_move_keep_current_objective(host_store):
    p = host_store.load("original-topic")
    p.current_module_id = "module-original"
    p.current_kp_index = 1
    host_store.save(p)
    route = read_route(host_store, "original-topic", "ws")
    memory = route["modules"][0]["knowledge_points"][1]
    modules = [{"name": "Moved module", "knowledge_points": [memory]}]
    preview = preview_route(
        host_store, "original-topic", preview_request(host_store, modules), "ws"
    )
    commit(host_store, preview)
    saved = host_store.load("original-topic")
    assert saved.modules[0].knowledge_points[0].id == "memory-original"
    assert saved.current_module_id == saved.modules[0].id
    assert saved.current_kp_index == 0


@pytest.mark.parametrize("guard", ["lease", "pending", "interaction", "archived"])
def test_web_state_is_never_cancelled(host_store, guard):
    from deeptutor.learning.models import MasteryInteraction, PendingQuestion

    question = PendingQuestion(
        question_id="web",
        knowledge_point_id="concept-original",
        prompt="Web question",
        expected_answer="PRIVATE_ANSWER",
    )
    if guard == "lease":
        host_store.acquire_path_lease("original-topic", "web-session", "web-turn")
    elif guard in {"pending", "interaction"}:
        with host_store.transaction("original-topic") as tx:
            if guard == "pending":
                tx.progress.pending_question = question
                tx.touch()
            else:
                tx.put_interaction(
                    MasteryInteraction(
                        interaction_id="web", path_id="original-topic", question=question
                    )
                )
    else:
        topic = host_store.get_topic("original-topic")
        topic.metadata.status = "archived"
        host_store.put_topic(topic.metadata, topic.sources)
    before = host_store.load("original-topic").model_dump()
    with pytest.raises(BridgeError) as exc:
        preview_route(host_store, "original-topic", changed_request(host_store), "ws")
    assert exc.value.code in {"path_busy", "pending_interaction", "archived_topic"}
    assert host_store.load("original-topic").model_dump() == before
    if guard == "lease":
        assert host_store.get_path_lease("original-topic").turn_id == "web-turn"


def test_historical_interaction_protects_objective_even_without_aggregate_evidence(host_store):
    from deeptutor.learning.models import MasteryInteraction, PendingQuestion

    with host_store.transaction("original-topic") as tx:
        tx.put_interaction(
            MasteryInteraction(
                interaction_id="historical",
                path_id="original-topic",
                status="graded",
                question=PendingQuestion(
                    question_id="old",
                    knowledge_point_id="concept-original",
                    prompt="Old question",
                    expected_answer="SECRET",
                ),
            )
        )
    request = changed_request(host_store)
    del request.modules[0].knowledge_points[0]
    with pytest.raises(BridgeError) as exc:
        preview_route(host_store, "original-topic", request, "ws")
    assert exc.value.code == "objective_has_history"


def test_stale_preview_expiry_and_separate_commit_request(host_store):
    from deeptutor.learning.storage import LearningConflictError

    request = changed_request(host_store)
    preview = preview_route(host_store, "original-topic", request, "ws")
    with pytest.raises(BridgeError) as exc:
        commit_route(
            host_store,
            "original-topic",
            RouteCommit(request_id=request.request_id, preview_id=preview["preview_id"]),
            "ws",
        )
    assert exc.value.code == "review_required"
    with host_store._connect() as conn:
        conn.execute(
            "UPDATE mastery_events SET created_at = 0 WHERE id = ?", (preview["preview_id"],)
        )
        conn.commit()
    with pytest.raises(BridgeError) as exc:
        commit(host_store, preview)
    assert exc.value.code == "preview_unavailable"
    preview = preview_route(host_store, "original-topic", changed_request(host_store), "ws")
    p = host_store.load("original-topic")
    p.name = "Web rename"
    host_store.save(p)
    with pytest.raises(LearningConflictError):
        commit(host_store, preview)


def test_preview_ids_are_scoped_and_payload_reuse_conflicts(host_store):
    request = changed_request(host_store)
    preview = preview_route(host_store, "original-topic", request, "ws")
    request.modules[0].name = "Different payload"
    with pytest.raises(BridgeError) as exc:
        preview_route(host_store, "original-topic", request, "ws")
    assert exc.value.code == "idempotency_conflict"
    with host_store._connect() as conn:
        conn.execute(
            "UPDATE mastery_events SET session_id = 'web' WHERE id = ?", (preview["preview_id"],)
        )
        conn.commit()
    with pytest.raises(BridgeError) as exc:
        commit(host_store, preview)
    assert exc.value.code == "preview_unavailable"


def test_noop_duplicate_and_foreign_ids_are_rejected(host_store):
    request = preview_request(host_store)
    with pytest.raises(BridgeError) as exc:
        preview_route(host_store, "original-topic", request, "ws")
    assert exc.value.code == "route_unchanged"
    for identifier in ("foreign", "concept-original"):
        request = changed_request(host_store)
        request.modules[0].knowledge_points[-1].id = identifier
        with pytest.raises(BridgeError) as exc:
            preview_route(host_store, "original-topic", request, "ws")
        assert exc.value.code == "invalid_route_ids"


def test_concurrent_creation_is_single_atomic_topic(host_store):
    request = create_request()

    def run(_):
        try:
            return create_route(host_store, request, "ws")
        except BridgeError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(4)))
    assert any(isinstance(r, dict) for r in results)
    topic_id = "topic_" + request.creation_id.replace("-", "")
    assert host_store.load(topic_id).version == 1
    assert (
        len([e for e in host_store.list_events(topic_id) if e.event_type == "codex.topic_created"])
        == 1
    )


def test_selected_material_access_and_aliases(host_store, monkeypatch):
    import deeptutor.multi_user.knowledge_access as access

    calls = []
    monkeypatch.setattr(access, "resolve_for_rag", lambda kb: calls.append(kb) or object())
    monkeypatch.setattr(access, "resolve_kb_document_path", lambda kb, path: None)
    request = create_request(
        sources=[
            {
                "client_ref": "material",
                "kind": "file",
                "source_id": "chapter.pdf",
                "label": "Chapter",
                "kb_name": "existing-kb",
            }
        ]
    )
    with pytest.raises(BridgeError) as exc:
        create_route(host_store, request, "ws")
    assert exc.value.code == "source_unavailable"
    assert not host_store.exists("topic_" + request.creation_id.replace("-", ""))
    monkeypatch.setattr(access, "resolve_kb_document_path", lambda kb, path: object())
    request.modules[0].knowledge_points[0].topic_source_ids = ["material"]
    result = create_route(host_store, request, "ws")
    topic = host_store.get_topic(result["topic_id"])
    assert host_store.load(result["topic_id"]).modules[0].knowledge_points[0].topic_source_ids == [
        topic.sources[0].id
    ]
    assert not topic.sources[0].metadata.get("grounded_for_route")
    assert calls == ["existing-kb", "existing-kb"]


def test_unknown_topics_never_create_placeholders(host_store):
    with pytest.raises(BridgeError):
        preview_route(host_store, "missing", changed_request(host_store), "ws")
    with pytest.raises(BridgeError):
        commit_route(
            host_store, "missing", RouteCommit(request_id=str(uuid4()), preview_id=1), "ws"
        )
    assert not host_store.exists("missing")


def test_commit_rechecks_pending_state_and_request_identity(host_store):
    from deeptutor.learning.models import PendingQuestion
    from deeptutor.learning.storage import LearningConflictError

    preview = preview_route(host_store, "original-topic", changed_request(host_store), "ws")
    with host_store.transaction("original-topic") as tx:
        tx.progress.pending_question = PendingQuestion(
            question_id="new-web-question",
            knowledge_point_id="concept-original",
            prompt="Question",
            expected_answer="SECRET",
        )
        tx.touch()
    before = host_store.load("original-topic").model_dump()
    with pytest.raises(LearningConflictError):
        commit(host_store, preview)
    assert host_store.load("original-topic").model_dump() == before


def test_commit_retry_is_deduplicated_and_reuse_for_different_preview_conflicts(host_store):
    preview = preview_route(host_store, "original-topic", changed_request(host_store), "ws")
    request_id = str(uuid4())
    commit(host_store, preview, request_id)
    with pytest.raises(BridgeError) as exc:
        commit(host_store, preview)
    assert exc.value.code == "preview_already_committed"
    next_request = changed_request(host_store)
    next_request.modules[0].name = "Another module name"
    next_preview = preview_route(host_store, "original-topic", next_request, "ws")
    with pytest.raises(BridgeError) as exc:
        commit(host_store, next_preview, request_id)
    assert exc.value.code == "idempotency_conflict"
    assert commit(host_store, preview, request_id)["replayed"]


@pytest.mark.parametrize("operation", ["preview", "commit"])
@pytest.mark.parametrize("committed", [False, True])
def test_sigkill_route_retry_recovers_original_store(host_store, operation, committed):
    from test_crash_recovery import paused_child

    request = changed_request(host_store)
    if operation == "commit":
        preview = preview_route(host_store, "original-topic", request, "ws")
        request = RouteCommit(request_id=str(uuid4()), preview_id=preview["preview_id"])
    source = """
import json, sys, threading
from pathlib import Path
from deeptutor.learning.storage import LearningStore
from deeptutor_link.models import RouteCommit, RoutePreview
from deeptutor_link.routes import preview_route, commit_route
store = LearningStore(root=Path(sys.argv[1]))
operation, committed = sys.argv[3:5]
original = store.acquire_path_lease
def paused(*args, **kwargs):
    if committed == 'False':
        original(*args, **kwargs)
    print('READY', flush=True)
    threading.Event().wait()
if committed == 'True':
    store.release_path_lease = paused
else:
    store.acquire_path_lease = paused
model = RouteCommit if operation == 'commit' else RoutePreview
call = commit_route if operation == 'commit' else preview_route
call(store, 'original-topic', model.model_validate(json.loads(sys.argv[2])), 'ws')
"""
    with paused_child(
        source, host_store.db_path.parent, request.model_dump_json(), operation, committed
    ) as child:
        lease = host_store.get_path_lease("original-topic")
        assert lease.session_id == "__path_api__"
        child.kill()
        child.wait(timeout=10)
        call = commit_route if operation == "commit" else preview_route
        result = call(host_store, "original-topic", request, "ws")
    assert result["replayed" if operation == "commit" else "deduplicated"] is committed
    assert host_store.get_path_lease("original-topic") is None
    assert (
        len(
            [
                e
                for e in host_store.list_events("original-topic")
                if e.event_type == "path.modules_previewed"
            ]
        )
        == 1
    )


@pytest.mark.parametrize("committed", [False, True])
def test_sigkill_creation_has_no_partial_route_or_duplicate(host_store, committed):
    from test_crash_recovery import paused_child

    request = create_request()
    source = """
import json, sys, threading
from contextlib import contextmanager
from pathlib import Path
from deeptutor.learning.storage import LearningStore
from deeptutor_link.models import RouteCreate
from deeptutor_link.routes import create_route
store = LearningStore(root=Path(sys.argv[1]))
original = store.transaction
def pause():
    print('READY', flush=True)
    threading.Event().wait()
@contextmanager
def transaction(*args, **kwargs):
    with original(*args, **kwargs) as tx:
        yield tx
        if sys.argv[3] == 'False':
            pause()
    if sys.argv[3] == 'True':
        pause()
store.transaction = transaction
create_route(store, RouteCreate.model_validate(json.loads(sys.argv[2])), 'ws')
"""
    with paused_child(
        source, host_store.db_path.parent, request.model_dump_json(), committed
    ) as child:
        child.kill()
        child.wait(timeout=10)
        result = create_route(host_store, request, "ws")
    assert result["deduplicated"] is committed
    assert host_store.load(result["topic_id"]).version == 1
    assert (
        len(
            [
                e
                for e in host_store.list_events(result["topic_id"])
                if e.event_type == "codex.topic_created"
            ]
        )
        == 1
    )
