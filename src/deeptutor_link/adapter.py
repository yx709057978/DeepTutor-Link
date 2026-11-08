"""Version-scoped adapter. No store, indexes, or chat engine of our own."""

import hashlib
import json
import re
from pathlib import PurePosixPath
from urllib.parse import quote, urlencode

from deeptutor_link.leases import assessment_lease, recover_lease
from deeptutor_link.models import Assessment, Retrieval


class BridgeError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def web_link(path: str, workspace: str) -> str:
    return path + ("&" if "?" in path else "?") + urlencode({"dt_workspace": workspace})


def topic_link(path_id: str, workspace: str) -> str:
    return web_link(f"/learning/mastery/{quote(path_id, safe='')}", workspace)


def selected_sources(progress, topic, objective_id: str):
    from deeptutor.learning.policy import find_knowledge_point

    kp, _, _ = find_knowledge_point(progress, objective_id)
    if kp is None:
        raise BridgeError("unknown_objective", "Refresh the topic; objective not found.", 404)
    sources = list(topic.sources if topic else [])
    selected = set(kp.topic_source_ids)
    if selected and not selected.issubset({s.id for s in sources}):
        raise BridgeError("source_changed", "Selected objective materials changed.")
    return kp, [s for s in sources if not selected or s.id in selected]


def source_resource(source):
    from deeptutor.multi_user.knowledge_access import resolve_for_rag
    from deeptutor.services.workspace.knowledge import learning_source_access

    if not source.available:
        raise BridgeError("source_unavailable", "A selected material is unavailable.")
    kind = source.kind.value
    if kind not in {"knowledge_base", "file"}:
        raise BridgeError("source_not_retrievable", "First version retrieves KB/file sources only.")
    kb = (
        source.source_id
        if kind == "knowledge_base"
        else str(source.metadata.get("kb_name") or source.metadata.get("knowledge_base") or "")
    )
    if not kb:
        raise BridgeError("missing_kb_reference", "This file has no saved KB reference.")
    with learning_source_access([kb]):
        resource = resolve_for_rag(kb)
    if resource is None:
        raise BridgeError("source_unavailable", "Selected KB is unavailable.")
    return kb, resource


def _safe_source_locator(source, kb: str) -> dict:
    from deeptutor.multi_user.knowledge_access import resolve_kb_document_path, resolve_kb_manifest
    from deeptutor.services.workspace.knowledge import learning_source_access

    locator = {"kb_name": kb, "filename": None}
    if source.kind.value != "file":
        with learning_source_access([kb]):
            manifest = resolve_kb_manifest(kb, limit=1)
        locator["enumerable"] = bool(manifest and manifest.enumerable)
        return locator
    raw = str(source.metadata.get("path") or source.source_id or "").replace("\\", "/").strip()
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return {"kb_name": kb, "filename": None}
    with learning_source_access([kb]):
        target = resolve_kb_document_path(kb, raw)
    if target is None:
        return {"kb_name": kb, "filename": None}
    locator["filename"] = path.as_posix()
    return locator


def _verify_assessment_evidence_quotes(store, progress, sources, request: Assessment) -> None:
    from deeptutor.learning.lesson_evidence import assessment_evidence, validate_evidence
    from deeptutor.multi_user.knowledge_access import resolve_kb_document_path
    from deeptutor.services.workspace.knowledge import learning_source_access
    from deeptutor.utils.document_extractor import extract_text_from_path
    from deeptutor.utils.document_validator import DocumentValidator

    cited_ids = set(request.source_ids)
    selected = {source.id: source for source in sources if source.id in cited_ids}
    required = {
        source.id for source in selected.values()
        if source.kind.value in {"file", "knowledge_base"}
    }
    packet = assessment_evidence(progress, request.objective_id)
    codex_packet = bool(packet.get("codex_pdf_evidence"))
    if codex_packet:
        try:
            validate_evidence(store, progress, request.objective_id, packet)
        except Exception as exc:
            raise BridgeError("pdf_evidence_stale", "Refresh the Assistant-prepared PDF evidence before assessing.", 409) from exc
    packet_pages = packet.get("sources", []) if codex_packet else []
    quotes: dict[str, list[str]] = {}
    for evidence in request.evidence_quotes:
        source = selected.get(evidence.source_id)
        if source is None or source.kind.value not in {"file", "knowledge_base"}:
            raise BridgeError("invalid_evidence_quote", "Evidence quote references an unselected material.", 422)
        filename = evidence.filename.replace("\\", "/").strip()
        if (
            not filename
            or filename.startswith("/")
            or re.match(r"^[A-Za-z]:", filename)
            or any(part in {"", ".", ".."} for part in filename.split("/"))
            or "\x00" in filename
        ):
            raise BridgeError("invalid_evidence_quote", "A citation must use a safe relative file path.", 422)
        source_path = str(source.metadata.get("path") or source.source_id).replace("\\", "/").strip()
        allowed_paths = {
            str(value).replace("\\", "/").strip()
            for value in (source.source_id, source.metadata.get("path"))
            if value
        }
        if source.kind.value == "file" and filename not in allowed_paths:
            raise BridgeError("invalid_evidence_quote", "A file citation must use the selected file path.", 422)
        kb = (
            source.source_id
            if source.kind.value == "knowledge_base"
            else str(source.metadata.get("kb_name") or source.metadata.get("knowledge_base") or "")
        )
        if not kb:
            raise BridgeError("missing_kb_reference", "The selected file has no saved knowledge-base reference.", 409)
        if codex_packet:
            page = next(
                (
                    item for item in packet_pages
                    if item.get("source_id") == source.id
                    and item.get("ref", {}).get("document_key") == filename
                    and (
                        evidence.page_number is None
                        or item.get("ref", {}).get("page_index") == evidence.page_number - 1
                    )
                ),
                None,
            )
            if page is None:
                raise BridgeError("pdf_page_not_prepared", "Cite a page from the current AI assistant PDF preparation.", 422)
            if evidence.quote and (
                len(evidence.quote) < 20
                or normalize_text(evidence.quote) not in normalize_text(page.get("text", ""))
            ):
                raise BridgeError("evidence_quote_not_found", "A submitted PDF quote does not match the cited original page.", 422)
            if evidence.visual_observation and (
                len(evidence.visual_observation.strip()) < 20
                or normalize_text(evidence.visual_observation) not in normalize_text(page.get("visual_observation", ""))
            ):
                raise BridgeError("visual_evidence_not_prepared", "Cite an observation recorded for the inspected original page image.", 422)
            if not evidence.quote and not evidence.visual_observation.strip():
                raise BridgeError("evidence_quote_not_found", "Cite an exact page quote or a recorded page observation.", 422)
        else:
            if len(evidence.quote) < 20:
                raise BridgeError("invalid_evidence_quote", "Text citations must contain at least 20 characters.", 422)
            with learning_source_access([kb]):
                target = resolve_kb_document_path(kb, source_path if source.kind.value == "file" else filename)
                if target is None:
                    raise BridgeError("source_unavailable", "A cited source file is unavailable in the selected knowledge base.", 409)
                try:
                    text = extract_text_from_path(
                        target,
                        filename_hint=filename,
                        max_bytes=DocumentValidator.MAX_FILE_SIZE,
                        max_chars=200000,
                    )
                except Exception as exc:
                    raise BridgeError("source_unreadable", "The cited source file could not be read as text.", 422) from exc
            if normalize_text(evidence.quote) not in normalize_text(text):
                raise BridgeError("evidence_quote_not_found", "A submitted evidence quote does not match the selected original file.", 422)
        quotes.setdefault(source.id, []).append(source_path if source.kind.value == "file" else filename)
    missing = required - quotes.keys()
    if missing:
        raise BridgeError(
            "source_evidence_required",
            "For each selected file/knowledge-base source, read the original text and cite at least one exact passage (minimum 20 characters).",
            422,
        )


def _bind_external_question(store, progress, objective, request, packet, question_id):
    from deeptutor.learning import semantic_assessment as semantic
    from deeptutor.learning.lesson_evidence import LessonUnavailable, validate_evidence
    from deeptutor.learning.models import PendingQuestion

    if not packet.get("sources"):
        return None
    if not request.reference_answer.strip() or not request.question_citations:
        raise BridgeError(
            "question_binding_required",
            "Source-grounded assessment needs a reference answer and citations for the exact question/key.",
            422,
        )
    question = PendingQuestion(
        question_id=question_id,
        knowledge_point_id=objective.id,
        module_id=objective.module_id,
        prompt=request.question,
        expected_answer=request.reference_answer.strip(),
        source_evidence=json.loads(json.dumps(packet)),
    )
    citations = []
    has_visual_citation = False
    for citation in request.question_citations:
        index = citation.source_index
        if index is None or index >= len(packet["sources"]):
            raise BridgeError("invalid_question_citation", "A question citation must reference a prepared source page.", 422)
        page = packet["sources"][index]
        if citation.source_id != page["source_id"] or citation.filename != page["ref"]["document_key"]:
            raise BridgeError("invalid_question_citation", "Question citation does not match its prepared source page.", 422)
        quote, observation = citation.quote.strip(), citation.visual_observation.strip()
        if quote and (len(quote) < 20 or normalize_text(quote) not in normalize_text(page.get("text", ""))):
            raise BridgeError("invalid_question_citation", "Question citation quote does not match the original page.", 422)
        if observation and (
            len(observation) < 20
            or normalize_text(observation) not in normalize_text(page.get("visual_observation", ""))
        ):
            raise BridgeError("invalid_question_citation", "Visual question citation does not match the inspected page observation.", 422)
        if not quote and not observation:
            raise BridgeError("invalid_question_citation", "Each question citation needs an exact quote or inspected-page observation.", 422)
        if quote:
            citations.append({"index": index, "quote": quote})
        if observation:
            citations.append({"index": index, "visual_observation": observation})
            has_visual_citation = True
    if objective.required_visual_tasks and not has_visual_citation:
        raise BridgeError(
            "visual_question_citation_required",
            "Inspect the original page image and cite its recorded visual relationship for this visual objective.",
            422,
        )
    sources = [
        {"index": i, "text": row.get("text", ""), "quote": row.get("quote", ""), "ref": row["ref"]}
        for i, row in enumerate(packet["sources"])
    ]
    try:
        semantic._attach_support(
            question,
            sources,
            {"supported": True, "citations": citations, "provenance": "adapted"},
        )
        semantic.validate_question(store, progress, question)
        validate_evidence(store, progress, objective.id, question.source_evidence, allow_partial=True)
    except (LessonUnavailable, semantic.AssessmentUnavailable, ValueError) as exc:
        raise BridgeError("question_binding_failed", str(exc)[:500], 422) from exc
    return question


def normalize_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def citation_contracts(index: int, quote: str, observation: str) -> list[dict]:
    contracts = []
    if quote:
        contracts.append({"index": index, "quote": quote})
    if observation:
        contracts.append({"index": index, "visual_observation": observation})
    return contracts


def context(store, path_id: str, workspace: str, offset: int = 0, limit: int = 40) -> dict:
    from deeptutor.learning import policy

    progress = store.load(path_id)
    if progress is None:
        raise BridgeError("unknown_topic", "Topic not found in this account/workspace.", 404)
    lease_state = recover_lease(store, path_id, inspect_only=True)
    topic = store.get_topic(path_id, progress=progress)
    objectives = [
        (m, kp)
        for m in sorted(progress.modules, key=lambda m: m.order)
        for kp in m.knowledge_points
    ]
    rows = []
    for module, kp in objectives[offset : offset + limit]:
        state = progress.repetition_states.get(kp.id)
        rows.append(
            {
                "id": kp.id,
                "name": kp.name[:400],
                "module_id": module.id,
                "module_name": module.name[:400],
                "type": kp.type.value,
                "prerequisite_ids": kp.prerequisite_ids[:100],
                "source_ids": kp.topic_source_ids[:100],
                "stage": policy.learning_stage(progress, kp.id),
                "mastery": policy.display_mastery(progress, kp),
                "mastery_source": policy.mastery_source(progress, kp),
                "assessed_mastered": policy.is_assessed_mastered(progress, kp),
                "review": {
                    "due_at": state.next_review_at,
                    "review_count": state.review_count,
                    "desired_retention": state.desired_retention,
                }
                if state
                else None,
            }
        )
    sources = []
    for source in (topic.sources if topic else [])[:80]:
        row = {
            "id": source.id,
            "kind": source.kind.value,
            "label": source.label[:400],
            "available": source.available,
            "direct_read_available": False,
            "retrieval_status": "not_applicable",
            "web_url": None,
            "locator": None,
        }
        if source.kind.value in {"file", "knowledge_base"}:
            try:
                kb, resource = source_resource(source)
                safe_kb_ref = str(getattr(resource, "id", "") or kb)
                locator = _safe_source_locator(source, safe_kb_ref)
                row.update(
                    direct_read_available=(
                        locator.get("enumerable", False)
                        if source.kind.value == "knowledge_base"
                        else locator["filename"] is not None
                    ),
                    retrieval_status="vector_index_and_query_embedding_not_verified",
                    locator=locator,
                    web_url=web_link(
                        f"/knowledge-bases/{quote(safe_kb_ref, safe='')}", workspace
                    ),
                )
            except (BridgeError, PermissionError, ValueError):
                row["available"] = False
        sources.append(row)
    step = policy.next_objective(progress)
    from deeptutor_link.pending import pending_summary
    profile = progress.learner_profile
    return {
        "schema_version": 1,
        "topic_id": path_id,
        "revision": progress.version,
        "name": policy.path_display_name(progress)[:400],
        "goal": topic.metadata.goal[:2000] if topic else "",
        "stage": progress.current_stage.value,
        "objectives": rows,
        "total_objectives": len(objectives),
        "next_offset": offset + limit if offset + limit < len(objectives) else None,
        "sources": sources,
        "sources_omitted": max(0, len(topic.sources if topic else []) - 80),
        "preferences": {
            k: str(getattr(profile, k, ""))[:600]
            for k in (
                "prior_knowledge",
                "target_level",
                "time_budget",
                "preferences",
                "teaching_strategy",
            )
        },
        "teaching_preferences": [
            v.text[:400]
            for v in list(profile.teaching_preferences.values() if profile else [])[:12]
        ],
        "pending": pending_summary(store, path_id, progress),
        "mutation_lease": lease_state,
        "next": {
            "action": step.action,
            "objective_id": step.knowledge_point_id,
            "reason": step.reason[:600],
        },
        "web_url": topic_link(path_id, workspace),
    }


def assess(store, path_id: str, request: Assessment, workspace: str) -> dict:
    from deeptutor.learning import policy
    from deeptutor.learning.lesson_evidence import assessment_evidence, validate_evidence
    from deeptutor.learning.models import InteractionStatus, MasteryInteraction, PendingQuestion
    from deeptutor.learning.review_strategy import scheduler_for_progress
    from deeptutor.learning.semantic_assessment import Verdict
    from deeptutor.learning.service import LearningService
    from deeptutor.learning.storage import LearningConflictError

    if not store.exists(path_id):
        raise BridgeError("unknown_topic", "Topic not found in this account/workspace.", 404)
    payload = request.model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    interaction_id = f"codex-{request.assessment_id}"
    with assessment_lease(store, path_id):
        with store.transaction(path_id) as tx:
            previous = tx.get_interaction(interaction_id)
            if previous:
                if previous.result.get("request_digest") != digest:
                    raise BridgeError(
                        "idempotency_conflict", "Assessment ID was used with another payload."
                    )
                return {**previous.result["receipt"], "deduplicated": True}
            if tx.base_revision != request.expected_revision:
                raise LearningConflictError(path_id, request.expected_revision, tx.base_revision)
            if tx.active_interaction() or tx.progress.pending_question:
                raise BridgeError(
                    "pending_interaction", "Read pending-question and continue the original question instead of submitting a fresh assessment."
                )
            topic = store.get_topic(path_id, progress=tx.progress)
            if topic and topic.metadata.status != "active":
                raise BridgeError(
                    "archived_topic", "Restore this topic before recording an assessment."
                )
            kp, sources = selected_sources(tx.progress, topic, request.objective_id)
            allowed_ids = {s.id for s in sources}
            if not set(request.source_ids).issubset(allowed_ids):
                raise BridgeError(
                    "source_changed", "Assessment cites sources outside this objective."
                )
            cited_sources = [s for s in sources if s.id in set(request.source_ids)]
            for source in cited_sources:
                if not source.available:
                    raise BridgeError("source_unavailable", "A selected material is unavailable.")
                if source.kind.value in {"file", "knowledge_base"}:
                    source_resource(source)
            _verify_assessment_evidence_quotes(store, tx.progress, sources, request)
            if any(s.kind.value not in {"goal", "file", "knowledge_base"} for s in cited_sources):
                raise BridgeError(
                    "assessment_source_unsupported",
                    "First version assesses goal/KB/file objectives only.",
                )
            cited_material_ids = {
                source.id for source in sources
                if source.kind.value in {"file", "knowledge_base"}
                and source.id in set(request.source_ids)
            }
            if any(s.kind.value != "goal" for s in sources) and not cited_material_ids:
                raise BridgeError(
                    "source_required", "Cite the selected material IDs used for assessment."
                )
            # Preserve the host's stronger source-grounded PDF gate. Never fabricate a packet.
            packet = assessment_evidence(tx.progress, kp.id)
            packet_has_pages = bool(packet.get("sources"))
            if packet_has_pages:
                validate_evidence(store, tx.progress, kp.id, packet, allow_partial=True)
                bound_question = _bind_external_question(
                    store, tx.progress, kp, request, packet, interaction_id
                )
            else:
                validate_evidence(store, tx.progress, kp.id, packet)
                bound_question = None
            if kp.required_visual_tasks and bound_question is None:
                raise BridgeError(
                    "visual_assessment_unsupported",
                    "Inspect original PDF pages and bind the exact question, answer and visual citations first.",
                )
            verdict = Verdict(
                result=request.result, confidence=request.confidence, feedback=request.feedback
            ).receipt()
            receipt = {
                **verdict,
                "assessment_id": str(request.assessment_id),
                "topic_id": path_id,
                "objective_id": kp.id,
                "source": "codex_external_assessment",
                "revision": tx.base_revision,
                "saved": False,
                "deduplicated": False,
                "web_url": topic_link(path_id, workspace),
            }
            if not verdict["final"]:
                return receipt
            scheduler = scheduler_for_progress(tx.progress)
            service = LearningService(store)
            if kp.type in policy.QUALITATIVE_TYPES:
                if not request.independent:
                    raise BridgeError(
                        "independent_answer_required",
                        "Qualitative assessment requires a fresh independent explanation.",
                    )
                service.record_qualitative_in_memory(
                    tx.progress,
                    kp.id,
                    passed=request.result == "correct",
                    evidence=request.learner_answer,
                    scheduler=scheduler,
                    session_id="__codex__",
                    turn_id=interaction_id,
                    verdict=verdict,
                )
            else:
                service._apply_grade(
                    tx.progress,
                    question_id=interaction_id,
                    knowledge_point_id=kp.id,
                    module_id=kp.module_id,
                    user_answer=request.learner_answer,
                    expected_answer="",
                    question_type="short",
                    scheduler=scheduler,
                    session_id="__codex__",
                    turn_id=interaction_id,
                    assistance_used=not request.independent,
                    verdict=verdict,
                )
            event = tx.progress.learning_evidence[-1]
            event.source = "codex_external_assessment"
            event.question_id = interaction_id
            if not event.evidence_id:
                event.evidence_id = interaction_id
            receipt = {
                **receipt,
                "revision": tx.base_revision + 1,
                "saved": True,
            }
            tx.put_interaction(
                MasteryInteraction(
                    interaction_id=interaction_id,
                    path_id=path_id,
                    status=InteractionStatus.GRADED,
                    question=PendingQuestion(
                        question_id=interaction_id,
                        knowledge_point_id=kp.id,
                        module_id=kp.module_id,
                        prompt=request.question,
                    ) if bound_question is None else bound_question.model_copy(update={"question_id": interaction_id}),
                    session_id="__codex__",
                    turn_id=interaction_id,
                    user_answer=request.learner_answer,
                    result={**verdict, "request_digest": digest, "receipt": receipt},
                )
            )
            tx.touch()
            tx.emit(
                "evidence.recorded",
                {"knowledge_point_id": kp.id, "source": event.source, "result": request.result},
                session_id="__codex__",
                turn_id=interaction_id,
            )
        return receipt


async def retrieve(store, path_id: str, request: Retrieval, workspace: str) -> dict:
    from deeptutor.services.rag.provider_binding import resolve_bound_provider
    from deeptutor.services.rag.service import RAGService
    from deeptutor.services.workspace.knowledge import learning_source_access

    progress = store.load(path_id)
    if progress is None:
        raise BridgeError("unknown_topic", "Topic not found.", 404)
    topic = store.get_topic(path_id, progress=progress)
    _, sources = selected_sources(progress, topic, request.objective_id)
    source = next((s for s in sources if s.id == request.source_id), None)
    if source is None:
        raise BridgeError("source_not_selected", "Source is not selected for this objective.", 403)
    kb, resource = source_resource(source)
    provider = resolve_bound_provider(resource.base_dir, resource.name)
    if provider != "llamaindex":
        raise BridgeError(
            "provider_unsupported",
            f"Pure retrieval supports llamaindex; selected provider is {provider}.",
            422,
        )
    kwargs = {}
    if source.kind.value == "file":
        key = str(source.metadata.get("path") or source.source_id).replace("\\", "/")
        if not key or key.startswith("/") or ".." in key.split("/"):
            raise BridgeError(
                "invalid_source_locator", "File has no safe indexed document locator."
            )
        kwargs["document_keys"] = [key]
    with learning_source_access([kb]):
        result = await RAGService(kb_base_dir=str(resource.base_dir)).search(
            query=request.query, kb_name=resource.name, rewrite=False, top_k=request.top_k, **kwargs
        )
    if result.get("error_type") or result.get("needs_reindex"):
        raise BridgeError(
            "retrieval_unavailable",
            "Existing index/embedding is unavailable; check the KB in the original website.",
            503,
        )
    references = []
    for item in result.get("sources", [])[: request.top_k]:
        # No arbitrary metadata or local absolute paths leave the server.
        references.append(
            {
                "title": str(item.get("title", ""))[:400],
                "document_key": str(item.get("document_key", ""))[:1000],
                "page": str(item.get("page", ""))[:40],
                "page_index": item.get("page_index")
                if type(item.get("page_index")) is int
                else None,
                "chunk_id": str(item.get("chunk_id", ""))[:200],
                "excerpt": str(item.get("evidence_text") or item.get("content", ""))[:6000],
                "web_url": web_link(f"/knowledge-bases/{quote(kb, safe='')}", workspace),
            }
        )
    if not references or not str(result.get("content", "")).strip():
        raise BridgeError(
            "no_grounded_results", "No grounded material matched; do not assess from guesses.", 422
        )
    return {
        "schema_version": 1,
        "provider": provider,
        "chat_model_used": False,
        "topic_id": path_id,
        "revision": progress.version,
        "source_id": source.id,
        "content": str(result["content"])[:24000],
        "content_truncated": len(str(result["content"])) > 24000,
        "references": references,
    }
