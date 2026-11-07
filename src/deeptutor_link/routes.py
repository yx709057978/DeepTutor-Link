"""Codex-authored routes, persisted by the original DeepTutor transaction engine."""

import hashlib
import json
from uuid import uuid4

from deeptutor_link.adapter import BridgeError, source_resource, topic_link
from deeptutor_link.leases import assessment_lease, mutation_lock_path
from deeptutor_link.locking import LockBusy, advisory_lock

SESSION = "__codex_routes_v1__"


def _bounded(value):
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 1000000:
        raise BridgeError(
            "route_too_large", "This route exceeds bridge limits. Edit it in the website.", 413
        )
    return value


def _event(store, path_id, event_type, *, request_prefix="", turn_id=""):
    # Query only our receipt, not the host's potentially large/private event history.
    with store._connect() as conn:
        row = conn.execute(
            """SELECT id, payload_json, turn_id FROM mastery_events
               WHERE path_id = ? AND session_id = ? AND event_type = ?
               AND (? = '' OR turn_id = ?)
               AND (? = '' OR substr(json_extract(payload_json, '$.request_id'), 1, 36) = ?)
               ORDER BY id LIMIT 1""",
            (path_id, SESSION, event_type, turn_id, turn_id, request_prefix, request_prefix),
        ).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "payload": json.loads(row["payload_json"]), "turn_id": row["turn_id"]}


def _digest(request):
    return hashlib.sha256(
        json.dumps(
            request.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def _points(modules):
    return {point.id: point for module in modules for point in module.knowledge_points}


def _contains(value, identifiers):
    if isinstance(value, dict):
        return any(k in identifiers or _contains(v, identifiers) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains(v, identifiers) for v in value)
    return isinstance(value, str) and value in identifiers


def _has_history(store, progress, identifiers):
    # knowledge_types is curriculum metadata, not evidence of study.
    state = progress.model_dump(
        mode="json",
        exclude={"modules", "knowledge_types", "current_module_id", "current_kp_index"},
    )
    if _contains(state, identifiers):
        return True
    # Include old durable interactions, not just the aggregate's current question.
    # This read uses the pinned host schema; no answers leave this function.
    with store._connect() as conn:
        return (
            conn.execute(
                """SELECT 1 FROM mastery_interactions
               WHERE path_id = ? AND EXISTS (
                 SELECT 1 FROM json_tree(question_json)
                 WHERE atom IN (SELECT value FROM json_each(?))) LIMIT 1""",
                (progress.book_id, json.dumps(sorted(identifiers))),
            ).fetchone()
            is not None
        )


def _assert_mutable(store, tx):
    if tx.active_interaction() or tx.progress.pending_question:
        raise BridgeError("pending_interaction", "Finish the pending question in the website.")
    topic = store.get_topic(tx.progress.book_id, progress=tx.progress)
    if topic and topic.metadata.status != "active":
        raise BridgeError("archived_topic", "Restore the topic before editing its route.")


def _service(store):
    from deeptutor.learning.service import LearningService

    class RouteService(LearningService):
        def _replace_module_set(self, tx, modules, **kwargs):
            _assert_mutable(store, tx)
            if kwargs.get("dry_run") and _editable(
                sorted(tx.progress.modules, key=lambda module: module.order)
            ) == _editable(modules):
                raise BridgeError("route_unchanged", "The route is unchanged. Nothing was saved.")
            return super()._replace_module_set(tx, modules, **kwargs)

        def replace_modules(self, progress, modules):
            old, new = _points(progress.modules), _points(modules)
            removed = old.keys() - new.keys()
            if removed and _has_history(store, progress, removed):
                raise BridgeError(
                    "objective_has_history",
                    "A removed or replaced objective has learning history. Retain its ID and content.",
                )
            for identifier in old.keys() & new.keys():
                if old[identifier].required_visual_tasks != new[identifier].required_visual_tasks:
                    raise BridgeError("visual_gate_changed", "Retain the original visual tasks.")
                if old[identifier].topic_source_ids != new[identifier].topic_source_ids:
                    if _has_history(store, progress, {identifier}):
                        raise BridgeError(
                            "objective_has_history",
                            "Retain source associations on objectives with learning history.",
                        )
            failures = (progress.stage_failure_counts.copy(), progress.stage_failure_notes.copy())
            before = progress.model_dump(mode="json", exclude={"modules", "knowledge_types"})
            super().replace_modules(progress, modules)
            progress.stage_failure_counts, progress.stage_failure_notes = failures
            after = progress.model_dump(mode="json", exclude={"modules", "knowledge_types"})
            if before != after:
                raise BridgeError("history_changed", "The host would alter learning history.")

    return RouteService(store)


def _modules(path_id, inputs, progress, sources, source_aliases=None):
    from deeptutor.learning.objective_relations import (
        RelationRefs,
        resolve_relation_refs,
        validate_objective_relations,
    )
    from deeptutor.learning.topic_generation import TopicGenerationError, materialize_modules

    old_modules = {m.id: m for m in progress.modules} if progress else {}
    old_points = _points(progress.modules) if progress else {}
    module_ids, point_ids = set(), set()
    for module in inputs:
        if module.id:
            if module.id not in old_modules or module.id in module_ids:
                raise BridgeError("invalid_route_ids", "Use each existing module ID once.", 422)
            module_ids.add(module.id)
        for point in module.knowledge_points:
            if point.id:
                if point.id not in old_points or point.id in point_ids:
                    raise BridgeError(
                        "invalid_route_ids", "Use each existing objective ID once.", 422
                    )
                point_ids.add(point.id)
    try:
        modules = materialize_modules(
            path_id,
            [m.model_dump(mode="json") for m in inputs],
            strict=True,
            existing_module_ids=set(old_modules) if progress else None,
            existing_objective_ids=set(old_points) if progress else None,
            existing_objective_counts={k: len(m.knowledge_points) for k, m in old_modules.items()},
            module_limit=20,
        )
    except TopicGenerationError as exc:
        raise BridgeError(
            "invalid_route", "Check module names and objective limits (normally 7 per module).", 422
        ) from exc
    aliases, refs = {}, {}

    def alias(key, identifier):
        if key:
            if key in aliases and aliases[key] != identifier:
                raise BridgeError(
                    "ambiguous_reference", "Objective references must be unique.", 422
                )
            aliases[key] = identifier

    for submitted, final in zip(inputs, modules, strict=True):
        old_module = old_modules.get(submitted.id)
        final.objective = (
            submitted.objective
            if submitted.objective is not None
            else old_module.objective
            if old_module
            else ""
        )
        final.pass_threshold = (
            submitted.pass_threshold
            if submitted.pass_threshold is not None
            else old_module.pass_threshold
            if old_module
            else 0.7
        )
        for spec, point in zip(submitted.knowledge_points, final.knowledge_points, strict=True):
            old = old_points.get(spec.id)
            if old:
                point.required_visual_tasks = list(old.required_visual_tasks)
                if (point.name, point.type) != (old.name, old.type):
                    point.id = f"{final.id}_kp_{uuid4().hex}"
            alias(point.id, point.id)
            alias(spec.id, point.id)
            alias(spec.client_ref, point.id)
            refs[point.id] = RelationRefs(
                prerequisite_refs=(
                    spec.prerequisite_ids
                    if spec.prerequisite_ids is not None
                    else old.prerequisite_ids
                    if old
                    else []
                ),
                topic_source_refs=(
                    spec.topic_source_ids
                    if spec.topic_source_ids is not None
                    else old.topic_source_ids
                    if old
                    else []
                ),
            )
    try:
        resolve_relation_refs(
            modules,
            refs,
            prerequisite_aliases=aliases,
            source_aliases=source_aliases or {},
            sources=sources,
        )
        validate_objective_relations(modules, sources)
    except ValueError as exc:
        raise BridgeError(
            "invalid_relations", "Check prerequisite cycles and objective/source references.", 422
        ) from exc
    return modules


def _editable(modules):
    return [
        {
            "id": m.id,
            "name": m.name,
            "objective": m.objective,
            "pass_threshold": m.pass_threshold,
            "knowledge_points": [
                {
                    k: point.model_dump(mode="json")[k]
                    for k in ("id", "name", "type", "prerequisite_ids", "topic_source_ids")
                }
                for point in m.knowledge_points
            ],
        }
        for m in modules
    ]


def read_route(store, path_id, workspace):
    progress = store.load(path_id)
    if progress is None:
        raise BridgeError("unknown_topic", "Topic not found in this account/workspace.", 404)
    topic = store.get_topic(path_id, progress=progress)
    return _bounded(
        {
            "schema_version": 1,
            "topic_id": path_id,
            "revision": progress.version,
            "name": progress.name,
            "goal": topic.metadata.goal if topic else "",
            "modules": _editable(sorted(progress.modules, key=lambda m: m.order)),
            "visual_requirements": {
                p.id: p.required_visual_tasks
                for p in _points(progress.modules).values()
                if p.required_visual_tasks
            },
            "sources": [
                {
                    "id": s.id,
                    "kind": s.kind.value,
                    "label": s.label,
                    "source_id": s.source_id,
                    "kb_name": str(
                        s.metadata.get("kb_name") or s.metadata.get("knowledge_base") or ""
                    ),
                    "available": s.available,
                }
                for s in (topic.sources if topic else [])[:80]
            ],
            "web_url": topic_link(path_id, workspace),
            "sources_omitted": max(0, len(topic.sources if topic else []) - 80),
        }
    )


def create_route(store, request, workspace):
    from deeptutor.learning.models import LearnerProfile, TopicMetadata, TopicSource
    from deeptutor.multi_user.knowledge_access import resolve_kb_document_path
    from deeptutor.services.workspace.knowledge import learning_source_access

    path_id = "topic_" + request.creation_id.replace("-", "")
    digest = _digest(request)
    try:
        with advisory_lock(mutation_lock_path(store, path_id)):
            with store.transaction(path_id, create=True) as tx:
                if not any(event[0] == "path.created" for event in tx.events):
                    previous = _event(store, path_id, "codex.topic_created")
                    if previous is None or previous["payload"].get("request_digest") != digest:
                        raise BridgeError(
                            "idempotency_conflict", "Creation ID already has another payload."
                        )
                    return {**previous["payload"]["receipt"], "deduplicated": True}
                sources, aliases = [], {}
                for index, spec in enumerate(request.sources):
                    if (
                        not spec.client_ref.strip()
                        or not spec.source_id.strip()
                        or not spec.label.strip()
                    ):
                        raise BridgeError(
                            "invalid_source", "Source references and labels must contain text.", 422
                        )
                    if spec.client_ref in aliases:
                        raise BridgeError(
                            "ambiguous_reference", "Source references must be unique.", 422
                        )
                    source = TopicSource(
                        id=f"{path_id}_source{index}",
                        kind=spec.kind,
                        source_id=spec.source_id,
                        label=spec.label.strip(),
                        position=index,
                        metadata={"kb_name": spec.kb_name} if spec.kind == "file" else {},
                    )
                    kb, _ = source_resource(source)
                    if spec.kind == "file":
                        with learning_source_access([kb]):
                            if resolve_kb_document_path(kb, spec.source_id) is None:
                                raise BridgeError(
                                    "source_unavailable", "Selected file is unavailable."
                                )
                    sources.append(source)
                    aliases[spec.client_ref] = source.id
                if not sources:
                    sources = [
                        TopicSource(id=f"{path_id}_goal", kind="goal", label=request.goal[:200])
                    ]
                modules = _modules(path_id, request.modules, None, sources, aliases)
                tx.progress.name = request.name.strip()
                if not tx.progress.name or not request.goal.strip():
                    raise BridgeError("invalid_route", "Name and goal must contain text.", 422)
                tx.put_topic(
                    TopicMetadata(
                        path_id=path_id,
                        goal=request.goal.strip(),
                        description=request.description.strip(),
                        map_seed=store._default_map_seed(path_id),
                    ),
                    sources,
                )
                service = _service(store)
                service.replace_modules(tx.progress, modules)
                tx.progress.current_module_id = modules[0].id
                if request.profile:
                    tx.progress.learner_profile = LearnerProfile(**request.profile.model_dump())
                receipt = {
                    "topic_id": path_id,
                    "revision": tx.base_revision + 1,
                    "saved": True,
                    "deduplicated": False,
                    "web_url": topic_link(path_id, workspace),
                }
                tx.emit(
                    "topic.created", {"module_count": len(modules), "source_count": len(sources)}
                )
                tx.emit(
                    "codex.topic_created",
                    {"request_digest": digest, "receipt": receipt},
                    session_id=SESSION,
                )
            return receipt
    except LockBusy as exc:
        raise BridgeError("path_busy", "Another creation request is active. Retry later.") from exc


def _preview_receipt(draft, preview_id, path_id, workspace):
    # The host's raw progress diff can contain answers; expose curriculum only.
    return _bounded(
        {
            "topic_id": path_id,
            "preview_id": preview_id,
            "saved": False,
            "base_revision": draft["base_revision"],
            "changes": [c for c in draft["changes"] if c["path"].startswith("/modules")],
            "identity_map": draft["effects"]["identity_map"],
            "before_module_order": draft["before_module_order"],
            "proposed_module_order": draft["proposed_module_order"],
            "history_preserved": True,
            "web_url": topic_link(path_id, workspace),
        }
    )


def preview_route(store, path_id, request, workspace):
    from deeptutor.learning.storage import LearningConflictError

    if not store.exists(path_id):
        raise BridgeError("unknown_topic", "Topic not found in this account/workspace.", 404)
    digest = _digest(request)
    request_key = f"{request.request_id}:{digest}"
    with assessment_lease(store, path_id):
        event = _event(store, path_id, "path.modules_previewed", request_prefix=request.request_id)
        if event:
            if event["payload"].get("request_id") != request_key:
                raise BridgeError(
                    "idempotency_conflict", "Preview ID was used with another payload."
                )
            with store.transaction(path_id) as tx:
                draft = _load_preview(tx, event["id"])
                if tx.base_revision != request.expected_revision:
                    raise LearningConflictError(
                        path_id, request.expected_revision, tx.base_revision
                    )
            return {
                **_preview_receipt(draft, event["id"], path_id, workspace),
                "deduplicated": True,
            }
        progress = store.load(path_id)
        if progress.version != request.expected_revision:
            raise LearningConflictError(path_id, request.expected_revision, progress.version)
        topic = store.get_topic(path_id, progress=progress)
        modules = _modules(path_id, request.modules, progress, topic.sources if topic else [])
        try:
            result, _ = _service(store).preview_module_revision(
                path_id,
                modules,
                expected_revision=request.expected_revision,
                session_id=SESSION,
                request_id=request_key,
            )
        except ValueError as exc:
            raise BridgeError(
                "route_unchanged", "The route is unchanged. Nothing was saved."
            ) from exc
        return {
            **_preview_receipt(result, result["preview_id"], path_id, workspace),
            "deduplicated": False,
        }


def _load_preview(tx, preview_id):
    try:
        return tx.module_preview(preview_id, session_id=SESSION)
    except ValueError as exc:
        raise BridgeError(
            "preview_unavailable", "Preview is unavailable or expired. Review a fresh preview."
        ) from exc


def commit_route(store, path_id, request, workspace):
    if not store.exists(path_id):
        raise BridgeError("unknown_topic", "Topic not found in this account/workspace.", 404)
    turn_id = f"codex-route-{request.request_id}"
    with assessment_lease(store, path_id):
        event = _event(store, path_id, "path.modules_revision_committed", turn_id=turn_id)
        if event and event["payload"].get("preview_id") != request.preview_id:
            raise BridgeError(
                "idempotency_conflict", "Commit request ID already names another preview."
            )
        with store.transaction(path_id) as tx:
            draft = _load_preview(tx, request.preview_id)
            if draft.get("request_id", "").split(":", 1)[0] == request.request_id:
                raise BridgeError(
                    "review_required", "Inspect the preview before using a new commit request ID."
                )
            if draft["committed_revision"] is not None and not event:
                raise BridgeError(
                    "preview_already_committed",
                    "Retry the original commit request ID or read the current route.",
                )
        try:
            _, receipt = _service(store).commit_module_revision(
                path_id,
                request.preview_id,
                session_id=SESSION,
                turn_id=turn_id,
                request_id=request.request_id,
            )
        except ValueError as exc:
            raise BridgeError(
                "preview_changed", "Preview changed or expired. Review a fresh preview."
            ) from exc
        return {
            "topic_id": path_id,
            **receipt,
            "saved": True,
            "revision": receipt["applied_revision"],
            "history_preserved": True,
            "web_url": topic_link(path_id, workspace),
        }
