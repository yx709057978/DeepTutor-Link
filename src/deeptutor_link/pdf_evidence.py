"""Direct, source-bound PDF evidence through the original LearningStore."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import PurePosixPath
from uuid import uuid4

from deeptutor_link.adapter import BridgeError, source_resource, topic_link
from deeptutor_link.leases import assessment_lease

PREPARATION_TTL = 3600
MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_PAGE_IMAGE_BYTES = 6 * 1024 * 1024

_DDL = """CREATE TABLE IF NOT EXISTS codex_pdf_preparations (
    preparation_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    path_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL
)"""

_RECEIPT_DDL = """CREATE TABLE IF NOT EXISTS codex_pdf_commits (
    request_id TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at REAL NOT NULL
)"""


def _fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _quote_path(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def _course_intent(topic, progress) -> dict[str, str]:
    return {
        "course_goal": str(getattr(getattr(topic, "metadata", None), "goal", ""))[:2000],
        "target_level": str(getattr(progress.learner_profile, "target_level", ""))[:500],
    }


def _objective_context(store, path_id: str, objective_id: str):
    from deeptutor.learning.lesson_evidence import objective_fingerprint, selected_scope
    from deeptutor.learning.policy import find_knowledge_point

    progress = store.load(path_id)
    if progress is None:
        raise BridgeError("unknown_topic", "Topic not found in this account/workspace.", 404)
    topic = store.get_topic(path_id, progress=progress)
    kp, _, _ = find_knowledge_point(progress, objective_id)
    if kp is None:
        raise BridgeError("unknown_objective", "Objective not found on this topic.", 404)
    try:
        scope = selected_scope(topic, progress, kp.id)
        objective_key = objective_fingerprint(progress, kp.id)
    except Exception as exc:
        raise BridgeError("source_scope_unavailable", "Selected PDF scope is unavailable.", 409) from exc
    eligible = [s for s in (topic.sources if topic else []) if not kp.topic_source_ids or s.id in kp.topic_source_ids]
    source_rows = [
        {
            "id": s.id,
            "kind": s.kind.value,
            "source_id": s.source_id,
            "available": bool(s.available),
            "path": str(s.metadata.get("path") or ""),
            "kb_name": str(s.metadata.get("kb_name") or s.metadata.get("knowledge_base") or ""),
        }
        for s in sorted(eligible, key=lambda item: item.id)
    ]
    scope_key = _fingerprint({"scope": scope, "sources": source_rows})
    intent_key = _fingerprint(_course_intent(topic, progress))
    return progress, topic, kp, scope, objective_key, scope_key, intent_key, eligible


def _safe_directory(value: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    if raw in {"", "."}:
        return ""
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BridgeError("invalid_directory", "Choose a relative directory inside the selected material.", 422)
    return path.as_posix().rstrip("/")


def _in_directory(filename: str, directory: str) -> bool:
    return not directory or filename == directory or filename.startswith(directory + "/")


def list_objective_pdfs(store, path_id: str, objective_id: str, directory: str) -> dict:
    from deeptutor.multi_user.knowledge_access import resolve_kb_document_path, resolve_kb_manifest
    from deeptutor.services.workspace.knowledge import learning_source_access

    directory = _safe_directory(directory)
    progress, _, kp, scope, _, _, _, sources = _objective_context(store, path_id, objective_id)
    if not scope:
        raise BridgeError("pdf_scope_required", "This objective has no selected local PDF materials.", 422)
    rows = []
    for source in sources:
        if source.kind.value not in {"file", "knowledge_base"} or not source.available:
            continue
        kb, _ = source_resource(source)
        with learning_source_access([kb]):
            if source.kind.value == "file":
                filename = str(source.metadata.get("path") or source.source_id).replace("\\", "/")
                names = [filename] if filename.lower().endswith(".pdf") and _in_directory(filename, directory) else []
            else:
                manifest = resolve_kb_manifest(kb, limit=1000, pattern="")
                if manifest is None or not manifest.enumerable:
                    raise BridgeError("pdf_inventory_unavailable", "The selected knowledge base cannot list local PDFs.", 409)
                if manifest.omitted:
                    raise BridgeError("pdf_inventory_too_large", "Narrow the selected directory; its file listing exceeds 1000 entries.", 413)
                names = [
                    item.name for item in manifest.documents
                    if item.name.lower().endswith(".pdf") and _in_directory(item.name, directory)
                ]
            for filename in names:
                target = resolve_kb_document_path(kb, filename)
                if target is None:
                    continue
                if target.stat().st_size > MAX_PDF_BYTES:
                    continue
                try:
                    import pymupdf

                    with pymupdf.open(target) as pdf:
                        page_count = len(pdf)
                except Exception:
                    continue
                if 0 < page_count <= 10000:
                    rows.append({
                        "source_id": source.id,
                        "filename": filename,
                        "page_count": page_count,
                    })
                    if len(rows) > 100:
                        raise BridgeError("pdf_listing_too_large", "Narrow the directory to at most 100 PDF files.", 413)
    return {
        "topic_id": path_id,
        "revision": progress.version,
        "objective_id": kp.id,
        "directory": directory or ".",
        "pdfs": rows,
        "total": len(rows),
        "embedding_used": False,
        "index_rebuilt": False,
    }


def _resolve_selected_page(store, path_id: str, objective_id: str, directory: str, selection):
    from deeptutor.multi_user.knowledge_access import resolve_kb_document_path
    from deeptutor.services.rag.source_pages import SourceRef, source_digest
    from deeptutor.services.workspace.knowledge import learning_source_access

    _, _, _, scope, _, _, _, sources = _objective_context(store, path_id, objective_id)
    source = next((s for s in sources if s.id == selection.source_id), None)
    if source is None or source.kind.value not in {"file", "knowledge_base"}:
        raise BridgeError("source_not_selected", "PDF source is outside this objective's selected materials.", 403)
    filename = selection.filename.replace("\\", "/")
    path = PurePosixPath(filename)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or not filename.lower().endswith(".pdf"):
        raise BridgeError("invalid_source_locator", "Use a listed relative PDF filename.", 422)
    if not _in_directory(filename, directory):
        raise BridgeError("source_outside_directory", "PDF pages must come from the selected directory.", 403)
    kb, resource = source_resource(source)
    if kb not in scope or (scope[kb] is not None and filename not in scope[kb]):
        raise BridgeError("source_not_selected", "PDF is outside the objective's selected source scope.", 403)
    if source.kind.value == "file":
        allowed = str(source.metadata.get("path") or source.source_id).replace("\\", "/")
        if filename != allowed:
            raise BridgeError("source_not_selected", "File source must match the selected original file.", 403)
    with learning_source_access([kb]):
        target = resolve_kb_document_path(kb, filename)
        if target is None or target.stat().st_size > MAX_PDF_BYTES:
            raise BridgeError("source_unavailable", "Selected PDF is unavailable or exceeds the size limit.", 409)
        try:
            import pymupdf

            digest_before = source_digest(target)
            with pymupdf.open(target) as pdf:
                if not 0 <= selection.page_index < len(pdf):
                    raise BridgeError("invalid_pdf_page", "Select a page within the original PDF.", 422)
                page = pdf[selection.page_index]
                text = page.get_text(sort=True)[:8000]
                page_label = str(selection.page_index + 1)
            digest_after = source_digest(target)
        except BridgeError:
            raise
        except Exception as exc:
            raise BridgeError("pdf_unreadable", "The selected PDF page could not be read.", 422) from exc
        if digest_before != digest_after:
            raise BridgeError("source_changed", "PDF changed while its page was being prepared. Retry.", 409)
        ref = SourceRef(
            kb_ref=kb,
            document_key=filename,
            source_digest=digest_after,
            page_index=selection.page_index,
            page_label=page_label,
        )
        return {"source_id": source.id, "filename": filename, "ref": ref.model_dump(), "text": text}


def prepare_pdf_evidence(store, path_id: str, request, account_id: str, workspace_id: str, directory: str) -> dict:
    from deeptutor.learning.storage import LearningConflictError
    from deeptutor.services.rag.source_pages import SourceRef, render_source_page
    from deeptutor.services.workspace.knowledge import learning_source_access

    directory = _safe_directory(directory)
    payload_hash = _fingerprint({
        "path_id": path_id,
        "account_id": account_id,
        "workspace_id": workspace_id,
        **request.model_dump(mode="json"),
        "directory": directory,
    })
    with assessment_lease(store, path_id):
        progress, topic, kp, _, objective_key, scope_key, intent_key, _ = _objective_context(store, path_id, request.objective_id)
        if progress.version != request.expected_revision:
            raise LearningConflictError(path_id, request.expected_revision, progress.version)
        if store.get_active_interaction(path_id) or progress.pending_question:
            raise BridgeError("pending_interaction", "Finish the pending question in the original website.")
        seen = set()
        pages = []
        total_image_bytes = 0
        for selection in request.pages:
            identity = (selection.source_id, selection.filename, selection.page_index)
            if identity in seen:
                raise BridgeError("duplicate_pdf_page", "Select each physical page only once.", 422)
            seen.add(identity)
            page = _resolve_selected_page(store, path_id, kp.id, directory, selection)
            ref = SourceRef.model_validate(page["ref"])
            with learning_source_access([ref.kb_ref]):
                pixels = render_source_page(ref)
            total_image_bytes += len(pixels)
            if len(pixels) > MAX_PAGE_IMAGE_BYTES or total_image_bytes > 12 * 1024 * 1024:
                raise BridgeError("pdf_images_too_large", "Select fewer or smaller PDF pages.", 413)
            page["image_bytes"] = len(pixels)
            pages.append(page)
        preparation_id = str(uuid4())
        record = {
            "path_id": path_id,
            "account_id": account_id,
            "workspace_id": workspace_id,
            "revision": progress.version,
            "objective_id": kp.id,
            "objective_fingerprint": objective_key,
            "scope_fingerprint": scope_key,
            "course_intent_fingerprint": intent_key,
            "directory": directory,
            "pages": pages,
        }
        with store._connect() as conn:
            conn.execute(_DDL)
            prior = conn.execute("SELECT preparation_id, payload_hash FROM codex_pdf_preparations WHERE request_id=?", (request.request_id,)).fetchone()
            if prior:
                if prior["payload_hash"] != payload_hash:
                    raise BridgeError("request_id_conflict", "Preparation request_id was used with different content.", 409)
                preparation_id = prior["preparation_id"]
                stored = conn.execute("SELECT payload_json, created_at FROM codex_pdf_preparations WHERE preparation_id=?", (preparation_id,)).fetchone()
                if stored is None or time.time() - float(stored["created_at"]) > PREPARATION_TTL:
                    raise BridgeError("preparation_expired", "Create a fresh PDF preparation.", 409)
                record = json.loads(stored["payload_json"])
            else:
                conn.execute(
                    "INSERT INTO codex_pdf_preparations VALUES (?, ?, ?, ?, ?, ?)",
                    (preparation_id, request.request_id, path_id, payload_hash, json.dumps(record, ensure_ascii=False), time.time()),
                )
        return {
            "preparation_id": preparation_id,
            "topic_id": path_id,
            "revision": record["revision"],
            "objective_id": kp.id,
            "objective": kp.name,
            "directory": record["directory"] or ".",
            "expires_in_seconds": PREPARATION_TTL,
            "pages": [
                {
                    "page_number": index,
                    "source_id": row["source_id"],
                    "filename": row["filename"],
                    "physical_page": row["ref"]["page_index"] + 1,
                    "source_digest": row["ref"]["source_digest"],
                    "text": row["text"],
                    "image_bytes": row["image_bytes"],
                    "image_path": f"/api/link/v1/topics/{_quote_path(path_id)}/pdf-preparations/{preparation_id}/pages/{index}/image",
                }
                for index, row in enumerate(record["pages"])
            ],
            "image_inspection": "Images are original-page renders. Open each downloaded image and inspect its pixels before making visual claims; the server cannot prove a client inspected them.",
        }


def _load_preparation(store, path_id: str, preparation_id: str, account_id: str, workspace_id: str):
    with store._connect() as conn:
        conn.execute(_DDL)
        row = conn.execute("SELECT payload_json, created_at FROM codex_pdf_preparations WHERE preparation_id=? AND path_id=?", (preparation_id, path_id)).fetchone()
    if row is None or time.time() - float(row["created_at"]) > PREPARATION_TTL:
        raise BridgeError("preparation_expired", "Create a fresh PDF preparation.", 409)
    record = json.loads(row["payload_json"])
    if record["account_id"] != account_id or record["workspace_id"] != workspace_id:
        raise BridgeError("preparation_scope_mismatch", "Preparation belongs to another account/workspace.", 403)
    progress, _, kp, _, objective_key, scope_key, intent_key, _ = _objective_context(store, path_id, record["objective_id"])
    if (
        progress.version != record["revision"]
        or kp.id != record["objective_id"]
        or objective_key != record["objective_fingerprint"]
        or scope_key != record["scope_fingerprint"]
        or intent_key != record["course_intent_fingerprint"]
    ):
        raise BridgeError("stale_preparation", "Topic, objective, source scope or course intent changed; prepare the PDF pages again.", 409)
    return record


def read_pdf_page_image(store, path_id: str, preparation_id: str, page_number: int, account_id: str, workspace_id: str) -> bytes:
    from deeptutor.services.rag.source_pages import SourceRef, render_source_page
    from deeptutor.services.workspace.knowledge import learning_source_access

    record = _load_preparation(store, path_id, preparation_id, account_id, workspace_id)
    if type(page_number) is not int or not 0 <= page_number < len(record["pages"]):
        raise BridgeError("invalid_pdf_page", "Page is outside this preparation.", 404)
    ref = SourceRef.model_validate(record["pages"][page_number]["ref"])
    try:
        with learning_source_access([ref.kb_ref]):
            pixels = render_source_page(ref)
    except Exception as exc:
        raise BridgeError("source_changed", "The selected PDF is unavailable or changed.", 409) from exc
    if len(pixels) > MAX_PAGE_IMAGE_BYTES:
        raise BridgeError("pdf_image_too_large", "Rendered page exceeds the image transfer limit.", 413)
    return pixels


def _normal(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _validate_pdf_packet(store, progress, kp_id: str, evidence: dict, *, allow_partial: bool):
    from deeptutor.learning.lesson_evidence import LessonUnavailable
    from deeptutor.services.rag.source_pages import SourceRef, resolve_source
    from deeptutor.services.workspace.knowledge import learning_source_access

    record = evidence.get("codex_pdf_evidence")
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        raise LessonUnavailable("Invalid AI assistant PDF evidence packet.")
    _, _, kp, scope, objective_key, scope_key, intent_key, sources = _objective_context(store, progress.book_id, kp_id)
    if (
        evidence.get("status") != "ready"
        or evidence.get("objective_fingerprint") != objective_key
        or record.get("scope_fingerprint") != scope_key
        or record.get("course_intent_fingerprint") != intent_key
    ):
        raise LessonUnavailable("The AI assistant PDF evidence is stale for this objective or selected source scope.")
    coverage = evidence.get("coverage", {})
    if not allow_partial and (coverage.get("complete") is not True or coverage.get("scope") != "objective"):
        raise LessonUnavailable("This PDF packet supports teaching only; prepare complete objective evidence before assessment.")
    if not evidence.get("sources"):
        raise LessonUnavailable("The AI assistant PDF packet has no source pages.")
    prep_id = str(record.get("preparation_id") or "")
    with store._connect() as conn:
        conn.execute(_DDL)
        prepared = conn.execute(
            "SELECT payload_json FROM codex_pdf_preparations WHERE preparation_id=? AND path_id=?",
            (prep_id, progress.book_id),
        ).fetchone()
    if prepared is None:
        raise LessonUnavailable("The original PDF preparation record is unavailable.")
    prepared_record = json.loads(prepared["payload_json"])
    if prepared_record.get("account_id") != record.get("account_id") or prepared_record.get("workspace_id") != record.get("workspace_id"):
        raise LessonUnavailable("The original PDF preparation belongs to another account/workspace.")
    if (
        prepared_record.get("objective_id") != kp_id
        or prepared_record.get("objective_fingerprint") != objective_key
        or prepared_record.get("scope_fingerprint") != scope_key
        or prepared_record.get("course_intent_fingerprint") != intent_key
    ):
        raise LessonUnavailable("The original PDF preparation no longer matches this objective and source scope.")
    prepared_refs = {
        _fingerprint(row["ref"]): row
        for row in prepared_record.get("pages", [])
    }
    if evidence.get("lesson_packet_id") != prep_id:
        raise LessonUnavailable("This evidence packet is not linked to the original PDF preparation.")
    if len(evidence["sources"]) != len(prepared_refs):
        raise LessonUnavailable("The source packet must retain every page issued by its preparation.")
    source_by_id = {source.id: source for source in sources}

    try:
        for entry in evidence["sources"]:
            source = source_by_id.get(entry.get("source_id"))
            ref = SourceRef.model_validate(entry.get("ref"))
            prepared_page = prepared_refs.get(_fingerprint(ref.model_dump()))
            if prepared_page is None or prepared_page["source_id"] != entry.get("source_id"):
                raise LessonUnavailable("A PDF evidence reference was not issued by this preparation.")
            if source is None or source.kind.value not in {"file", "knowledge_base"}:
                raise LessonUnavailable("A PDF page is outside the selected objective source scope.")
            kb, _ = source_resource(source)
            if ref.kb_ref != kb or kb not in scope or (scope[kb] is not None and ref.document_key not in scope[kb]):
                raise LessonUnavailable("A PDF page is outside the selected objective source scope.")
            if source.kind.value == "file" and ref.document_key != str(source.metadata.get("path") or source.source_id).replace("\\", "/"):
                raise LessonUnavailable("A PDF page does not match the selected original file.")
            quote, text = str(entry.get("quote") or ""), str(entry.get("text") or "")
            if quote and _normal(quote) not in _normal(text):
                raise LessonUnavailable("A PDF evidence quote does not match its original page text.")
            if len(str(entry.get("supports") or "").strip()) < 8:
                raise LessonUnavailable("Each page needs a source-grounded support statement.")
            if kp.required_visual_tasks and len(str(entry.get("visual_observation") or "").strip()) < 20:
                raise LessonUnavailable("Inspect the rendered original page images and record the visual evidence for this objective.")
            with learning_source_access([kb]):
                resolve_source(ref)
    except Exception as exc:
        if isinstance(exc, LessonUnavailable):
            raise
        raise LessonUnavailable("The selected original PDF is unavailable or changed.") from exc
    if kp.required_visual_tasks and not any(len(str(entry.get("visual_observation") or "").strip()) >= 20 for entry in evidence["sources"]):
        raise LessonUnavailable("Inspect the rendered original page images before supporting this visual task.")


def install_validation_hook():
    from deeptutor.learning import lesson_evidence

    if getattr(lesson_evidence.validate_evidence, "_codex_pdf_hook", False):
        return
    original = lesson_evidence.validate_evidence

    def validate(store, progress, kp_id, evidence, *, allow_partial=False):
        if isinstance(evidence, dict) and evidence.get("codex_pdf_evidence"):
            return _validate_pdf_packet(store, progress, kp_id, evidence, allow_partial=allow_partial)
        return original(store, progress, kp_id, evidence, allow_partial=allow_partial)

    validate._codex_pdf_hook = True
    lesson_evidence.validate_evidence = validate


def commit_pdf_evidence(store, path_id: str, request, account_id: str, workspace_id: str, workspace: str) -> dict:
    from deeptutor.learning.storage import LearningConflictError

    payload_hash = _fingerprint({
        "path_id": path_id,
        "account_id": account_id,
        "workspace_id": workspace_id,
        **request.model_dump(mode="json"),
    })
    with assessment_lease(store, path_id):
        with store._connect() as conn:
            conn.execute(_RECEIPT_DDL)
            previous = conn.execute("SELECT payload_hash, response_json FROM codex_pdf_commits WHERE request_id=?", (request.request_id,)).fetchone()
            if previous:
                if previous["payload_hash"] != payload_hash:
                    raise BridgeError("request_id_conflict", "PDF commit request_id was used with different content.", 409)
                return {**json.loads(previous["response_json"]), "replayed": True}
        record = _load_preparation(store, path_id, request.preparation_id, account_id, workspace_id)
        if request.expected_revision != record["revision"] or request.objective_id != record["objective_id"]:
            raise BridgeError("stale_preparation", "Commit must use the prepared objective and revision.", 409)
        if len(request.sources) != len(record["pages"]) or {item.page_number for item in request.sources} != set(range(len(record["pages"]))):
            raise BridgeError("invalid_pdf_citations", "Cite every prepared page exactly once.", 422)
        sources = []
        for item in request.sources:
            page = record["pages"][item.page_number]
            quote = item.quote.strip()
            if len(quote) < 20 and len(item.visual_observation.strip()) < 20:
                raise BridgeError("insufficient_pdf_evidence", "Each page needs an exact text quote or a detailed image observation.", 422)
            if quote and _normal(quote) not in _normal(page["text"]):
                raise BridgeError("pdf_quote_not_found", "A submitted quote does not match the selected original PDF page.", 422)
            sources.append({
                "source_id": page["source_id"],
                "ref": page["ref"],
                "quote": quote,
                "text": page["text"],
                "supports": item.supports,
                "visual_observation": item.visual_observation.strip(),
            })
        if request.purpose == "assessment" and not request.coverage_complete:
            raise BridgeError("assessment_coverage_incomplete", "Assessment evidence must be explicitly reviewed as covering the full objective.", 422)
        coverage = {
            "complete": request.coverage_complete,
            "scope": "objective" if request.coverage_complete else "focus",
            "focus": "" if request.coverage_complete else request.coverage_summary,
        }
        evidence = {
            "lesson_packet_id": request.preparation_id,
            "status": "ready",
            "schema_version": 1,
            "objective_fingerprint": record["objective_fingerprint"],
            "scope_fingerprint": record["scope_fingerprint"],
            "course_intent_fingerprint": record["course_intent_fingerprint"],
            "sources": sources,
            "coverage": coverage,
            "codex_pdf_evidence": {
                "schema_version": 1,
                "preparation_id": request.preparation_id,
                "account_id": account_id,
                "workspace_id": workspace_id,
                "directory": record["directory"] or ".",
                "provenance": "codex_external_source_review",
                "scope_fingerprint": record["scope_fingerprint"],
                "course_intent_fingerprint": record["course_intent_fingerprint"],
                "coverage_summary": request.coverage_summary,
            },
        }
        install_validation_hook()
        with store.transaction(path_id) as tx:
            if tx.base_revision != request.expected_revision:
                raise LearningConflictError(path_id, request.expected_revision, tx.base_revision)
            if tx.active_interaction() or tx.progress.pending_question:
                raise BridgeError("pending_interaction", "Finish the pending question in the original website.")
            _validate_pdf_packet(store, tx.progress, request.objective_id, evidence, allow_partial=True)
            tx.progress.lesson_evidence[request.objective_id] = evidence
            if request.purpose == "assessment":
                tx.progress.assessment_evidence[request.objective_id] = json.loads(json.dumps(evidence))
            tx.touch()
            tx.emit("source.evidence_prepared", {
                "knowledge_point_id": request.objective_id,
                "source": "codex_external_source_review",
                "preparation_id": request.preparation_id,
                "page_count": len(sources),
                "purpose": request.purpose,
            }, session_id="__codex_pdf__", turn_id=request.request_id)
            if request.purpose == "assessment":
                tx.progress.source_grounded = True
            receipt = {
                "topic_id": path_id,
                "objective_id": request.objective_id,
                "preparation_id": request.preparation_id,
                "revision": tx.base_revision + 1,
                "saved": True,
                "provenance": "codex_external_source_review",
                "pages": [
                    {"source_id": row["source_id"], "filename": row["ref"]["document_key"], "page": row["ref"]["page_index"] + 1}
                    for row in sources
                ],
                "web_url": topic_link(path_id, workspace),
            }
            tx._conn.execute(
                "INSERT INTO codex_pdf_commits VALUES (?, ?, ?, ?)",
                (request.request_id, payload_hash, json.dumps(receipt, ensure_ascii=False), time.time()),
            )
        return receipt
