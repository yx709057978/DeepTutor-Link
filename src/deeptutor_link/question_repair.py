"""Preview and commit exact historical Mastery leaf-question repairs."""

from __future__ import annotations

import hashlib
import json
import time
from uuid import uuid4

from deeptutor_link.adapter import BridgeError, citation_contracts
from deeptutor_link.leases import assessment_lease

PREVIEW_TTL = 3600
_PREVIEW_DDL = """CREATE TABLE IF NOT EXISTS codex_question_repair_previews (
    preview_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    path_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state_fingerprint TEXT NOT NULL,
    created_at REAL NOT NULL
)"""
_RECEIPT_DDL = """CREATE TABLE IF NOT EXISTS codex_question_repair_receipts (
    request_id TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at REAL NOT NULL
)"""


def _fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _interaction(store, path_id: str, question_id: str):
    from deeptutor.learning.policy import find_knowledge_point

    progress = store.load(path_id)
    if progress is None:
        raise BridgeError("unknown_topic", "Topic not found in this account/workspace.", 404)
    interaction = store.get_interaction(path_id, question_id)
    if interaction is None or interaction.interaction_id != question_id:
        raise BridgeError("question_not_found", "Use an exact stored question or assessment ID.", 404)
    if interaction.question.questions:
        raise BridgeError("group_question_unsupported", "Repair an exact stored leaf question, not its group parent.", 422)
    if interaction.status.value not in {"graded", "abandoned"}:
        raise BridgeError("question_not_historical", "Only a completed historical question can be repaired.", 409)
    kp_id = interaction.question.knowledge_point_id
    kp, module_id, _ = find_knowledge_point(progress, kp_id)
    if kp is None:
        raise BridgeError("objective_changed", "The question's original objective no longer exists.", 409)
    attempts = [a for a in progress.quiz_attempts if a.question_id == question_id and not a.voided]
    evidence = [
        item for item in progress.learning_evidence
        if item.question_id == question_id and item.knowledge_point_id == kp_id
    ]
    if not attempts and not evidence:
        raise BridgeError("question_has_no_assessment", "This stored question has no committed answer or assessment to repair.", 409)
    answers = sorted({str(a.user_answer or "") for a in attempts})
    if not answers and interaction.user_answer:
        answers = [interaction.user_answer]
    if interaction.answers:
        answers = sorted(set(answers) | {str(v) for v in interaction.answers.values()})
    return progress, interaction, kp, module_id, attempts, evidence, answers


def _external_source(interaction) -> bool:
    return (
        interaction.result.get("source") == "codex_external_assessment"
        or interaction.result.get("receipt", {}).get("source") == "codex_external_assessment"
    )


def _state_fingerprint(progress, interaction, attempts, evidence):
    return _fingerprint({
        "revision": progress.version,
        "interaction": interaction.model_dump(mode="json"),
        "attempts": [item.model_dump(mode="json") for item in attempts],
        "evidence": [item.model_dump(mode="json") for item in evidence],
    })


def inspect_question_repair(store, path_id: str, question_id: str) -> dict:
    progress, interaction, kp, module_id, attempts, evidence, answers = _interaction(store, path_id, question_id)
    return {
        "topic_id": path_id,
        "revision": progress.version,
        "question_id": question_id,
        "objective": {"id": kp.id, "name": kp.name, "type": kp.type.value, "module_id": module_id},
        "question": {
            "prompt": interaction.question.prompt,
            "question_type": interaction.question.question_type,
            "expected_answer": interaction.question.expected_answer,
            "options": interaction.question.choice_map,
            "explanation": interaction.question.explanation,
            "source_ids": [item.get("source_id") for item in interaction.question.source_evidence.get("sources", [])],
        },
        "historical_answer": interaction.user_answer,
        "attempt_count": len(attempts),
        "distinct_attempt_answers": len(answers),
        "attempts": [
            {"result": a.result, "is_correct": a.is_correct, "voided": a.voided, "timestamp": a.timestamp}
            for a in [item for item in progress.quiz_attempts if item.question_id == question_id][:20]
        ],
        "assessment": {
            "result": interaction.result.get("result"),
            "confidence": interaction.result.get("confidence"),
            "source": interaction.result.get("source") or next((item.source for item in evidence), None),
            "evidence_count": len(evidence),
        },
        "source_evidence_available": bool(interaction.question.source_evidence.get("sources")),
        "pending_interaction": bool(progress.pending_question or store.get_active_interaction(path_id)),
        "state_fingerprint": _state_fingerprint(progress, interaction, attempts, evidence),
    }


def _assert_no_pending(store, path_id, progress):
    if progress.pending_question is not None or store.get_active_interaction(path_id) is not None:
        raise BridgeError("pending_interaction", "Finish the pending question or website turn first.")


def preview_question_repair(store, path_id: str, request, account_id: str, workspace_id: str) -> dict:
    from deeptutor.learning.lesson_evidence import objective_fingerprint

    body = request.model_dump(mode="json")
    payload_hash = _fingerprint({"path_id": path_id, "account_id": account_id, "workspace_id": workspace_id, **body})
    with assessment_lease(store, path_id):
        progress, interaction, kp, _, attempts, evidence, answers = _interaction(store, path_id, request.question_id)
        if progress.version != request.expected_revision:
            raise BridgeError("stale_revision", "Refresh the topic before previewing a repair.", 409)
        _assert_no_pending(store, path_id, progress)
        if request.action == "correct":
            if not request.result or request.confidence is None or len(request.feedback.strip()) < 8:
                raise BridgeError("repair_decision_required", "A correction requires the assistant's result, confidence and feedback.", 422)
            if request.confidence < 0.8:
                raise BridgeError("repair_decision_uncertain", "The proposed repair is not sufficiently certain; void it or review the evidence.", 422)
            if len(answers) > 1:
                raise BridgeError("multiple_distinct_attempts", "This question has distinct historical answers; void the invalid record rather than applying one verdict to all attempts.", 409)
            external = _external_source(interaction)
            if not external:
                if not request.expected_answer.strip():
                    raise BridgeError("answer_key_required", "Correcting a native question requires a replacement answer key.", 422)
            if not request.expected_answer.strip():
                raise BridgeError("corrected_rubric_required", "A correction must include the corrected answer key or rubric.", 422)
            if not request.citations:
                raise BridgeError("source_citations_required", "Corrected keys require current source citations.", 422)
        state_key = _state_fingerprint(progress, interaction, attempts, evidence)
        try:
            objective_key = objective_fingerprint(progress, kp.id)
        except Exception as exc:
            raise BridgeError("objective_changed", "The question objective changed.", 409) from exc
        with store._connect() as conn:
            conn.execute(_PREVIEW_DDL)
            prior = conn.execute("SELECT preview_id, payload_hash, payload_json, state_fingerprint, created_at FROM codex_question_repair_previews WHERE request_id=?", (request.request_id,)).fetchone()
            if prior:
                if prior["payload_hash"] != payload_hash:
                    raise BridgeError("request_id_conflict", "Repair preview request_id was used with different content.", 409)
                if time.time() - float(prior["created_at"]) > PREVIEW_TTL:
                    raise BridgeError("preview_expired", "Create a fresh repair preview.", 409)
                preview_id = prior["preview_id"]
                state_key = prior["state_fingerprint"]
            else:
                preview_id = str(uuid4())
                snapshot = {
                    "path_id": path_id,
                    "account_id": account_id,
                    "workspace_id": workspace_id,
                    "revision": progress.version,
                    "objective_id": kp.id,
                    "objective_fingerprint": objective_key,
                    "question_id": request.question_id,
                    "action": request.action,
                    "reason": request.reason.strip(),
                    "expected_answer": request.expected_answer.strip(),
                    "result": request.result,
                    "confidence": request.confidence,
                    "feedback": request.feedback.strip(),
                    "citations": [item.model_dump(mode="json") for item in request.citations],
                }
                conn.execute(
                    "INSERT INTO codex_question_repair_previews VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (preview_id, request.request_id, path_id, payload_hash, json.dumps(snapshot, ensure_ascii=False), state_key, time.time()),
                )
        return {
            "preview_id": preview_id,
            "request_id": request.request_id,
            "topic_id": path_id,
            "revision": progress.version,
            "question_id": request.question_id,
            "action": request.action,
            "reason": request.reason.strip(),
            "before": {
                "answer_key": interaction.question.expected_answer,
                "result": interaction.result.get("result"),
                "is_correct": interaction.result.get("is_correct"),
                "attempt_count": len(attempts),
                "learner_answer": interaction.user_answer,
            },
            "after": {
                "answer_key": request.expected_answer.strip() if request.action == "correct" else None,
                "result": request.result if request.action == "correct" else "voided",
                "confidence": request.confidence if request.action == "correct" else None,
            },
            "source_citations": len(request.citations),
            "expires_in_seconds": PREVIEW_TTL,
            "commit_required": True,
        }


def _verify_citations(store, progress, interaction, snapshot):
    from deeptutor.learning import semantic_assessment as semantic
    from deeptutor.learning.lesson_evidence import LessonUnavailable, assessment_evidence
    from deeptutor.learning.policy import find_knowledge_point

    question = interaction.question.model_copy(deep=True)
    if (
        interaction.result.get("receipt", {}).get("source") == "codex_external_assessment"
        and not question.source_evidence.get("sources")
    ):
        question.source_evidence = json.loads(
            json.dumps(assessment_evidence(progress, question.knowledge_point_id))
        )
    if snapshot["expected_answer"]:
        question.expected_answer = snapshot["expected_answer"]
        question.accepted_answers = []
    sources = question.source_evidence.get("sources", [])
    citations = snapshot["citations"]
    if not sources or not citations:
        raise BridgeError("source_citations_required", "Corrected answer requires current source evidence.", 422)
    converted = []
    has_visual_citation = False
    for citation in citations:
        index = citation["source_index"]
        if index >= len(sources):
            raise BridgeError("invalid_source_citation", "Citation refers to a page outside the stored question evidence.", 422)
        quote = citation["quote"].strip()
        observation = citation["visual_observation"].strip()
        support = citation["support_statement"].strip()
        source = sources[index]
        if source.get("codex_pdf_page"):
            pass
        if source.get("ref") and question.source_evidence.get("codex_pdf_evidence"):
            if quote and quote not in str(source.get("text") or ""):
                raise BridgeError("invalid_source_citation", "Citation quote is not present on this prepared PDF page.", 422)
            if observation and observation not in str(source.get("visual_observation") or ""):
                raise BridgeError("invalid_source_citation", "Visual observation is not present in the prepared PDF evidence.", 422)
        if len(support) < 10:
            raise BridgeError("invalid_source_citation", "State how this citation supports the corrected question/key.", 422)
        if quote and len(quote) < 20:
            raise BridgeError("invalid_source_citation", "Text citations must contain at least 20 characters.", 422)
        if not quote and len(observation) < 20:
            raise BridgeError("invalid_source_citation", "Provide an exact source quote or a detailed visual observation.", 422)
        if not quote and not observation:
            raise BridgeError("invalid_source_citation", "Each citation needs an exact quote or inspected-page observation.", 422)
        converted.extend(citation_contracts(index, quote, observation))
        has_visual_citation = has_visual_citation or bool(observation)
    kp, _, _ = find_knowledge_point(progress, question.knowledge_point_id)
    if kp is None:
        raise BridgeError("objective_changed", "The question objective no longer exists.", 409)
    if (kp.required_visual_tasks or question.visual_context) and not has_visual_citation:
        raise BridgeError("visual_review_required", "Inspect the original page image and cite the visual relationship before correcting this question.", 422)
    try:
        semantic._attach_support(
            question,
            [
                {"index": i, "text": str(row.get("text") or ""), "quote": str(row.get("quote") or ""), "ref": row.get("ref")}
                for i, row in enumerate(sources)
            ],
            {"supported": True, "citations": converted, "provenance": "adapted"},
        )
        question.source_evidence["assessment_contract"]["codex_support_statements"] = [
            item["support_statement"] for item in snapshot["citations"]
        ]
        semantic.validate_question(store, progress, question)
    except (LessonUnavailable, semantic.AssessmentUnavailable, ValueError) as exc:
        raise BridgeError("repair_source_validation_failed", str(exc)[:500], 422) from exc
    return question


def _event_for_request(store, path_id: str, request_id: str):
    prefix = f"codex-repair:{request_id}:"
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT id, revision, event_type, payload_json, turn_id FROM mastery_events WHERE path_id=? AND turn_id LIKE ? ORDER BY id",
            (path_id, prefix + "%"),
        ).fetchall()
    return rows


def _external_repair(store, snapshot, interaction, request_hash: str, workspace: str):
    from deeptutor.learning import policy
    from deeptutor.learning.review_strategy import scheduler_for_progress
    from deeptutor.learning.service import LearningService
    from deeptutor.learning.storage import LearningConflictError

    path_id = snapshot["path_id"]
    question_id = snapshot["question_id"]
    event_turn = f"codex-repair:{snapshot['commit_request_id']}:{request_hash}"
    with store.transaction(path_id) as tx:
        if tx.base_revision != snapshot["revision"]:
            raise LearningConflictError(path_id, snapshot["revision"], tx.base_revision)
        _assert_no_pending(store, path_id, tx.progress)
        current = tx.get_interaction(question_id)
        if current is None or _fingerprint(current.model_dump(mode="json")) != snapshot["interaction_fingerprint"]:
            raise BridgeError("repair_target_changed", "The historical answer or assessment changed after preview.", 409)
        kp_id = snapshot["objective_id"]
        kp, _, _ = policy.find_knowledge_point(tx.progress, kp_id)
        if kp is None:
            raise BridgeError("objective_changed", "The question objective no longer exists.", 409)
        if snapshot["action"] == "correct":
            from deeptutor.learning.semantic_assessment import validate_question

            try:
                validate_question(store, tx.progress, snapshot["corrected_question"])
            except Exception as exc:
                raise BridgeError("repair_source_validation_failed", "Source evidence changed after repair preview.", 409) from exc
        event_rows = [
            item for item in tx.progress.learning_evidence
            if item.question_id == question_id and item.source == "codex_external_assessment"
        ]
        attempts = [item for item in tx.progress.quiz_attempts if item.question_id == question_id and not item.voided]
        if len(event_rows) != 1:
            raise BridgeError("assessment_history_ambiguous", "The exact AI assistant assessment event is unavailable or ambiguous.", 409)
        event = event_rows[0]
        before_result = event.result
        before_expected = current.question.expected_answer
        reason = snapshot["reason"]
        if snapshot["action"] == "void":
            if not attempts and event.assessment_type != "qualitative":
                raise BridgeError("assessment_history_ambiguous", "The assessment has no repairable stored attempt.", 409)
            for attempt in attempts:
                attempt.voided = True
                attempt.void_reason = reason
            tx.progress.learning_evidence = [item for item in tx.progress.learning_evidence if item is not event]
            tx.progress.error_records = [item for item in tx.progress.error_records if item.question_id != question_id]
            current.result = {**current.result, "voided": True, "reason": reason}
            action = "assessment.voided"
            result = "voided"
        else:
            receipt = {
                "result": snapshot["result"],
                "confidence": snapshot["confidence"],
                "feedback": snapshot["feedback"],
            }
            if event.assessment_type == "qualitative":
                current.question = snapshot["corrected_question"]
                event.result = snapshot["result"]
                event.quality = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}[snapshot["result"]]
                event.confidence = snapshot["confidence"]
                event.feedback = snapshot["feedback"]
                current.result = {**current.result, **receipt, "is_correct": snapshot["result"] == "correct", "final": True, "needs_clarification": False, "corrected": True, "reason": reason}
                action = "assessment.corrected"
            elif attempts:
                for attempt in attempts:
                    attempt.result = snapshot["result"]
                    attempt.is_correct = snapshot["result"] == "correct"
                    attempt.error_type = None if attempt.is_correct or snapshot["result"] == "partial" else attempt.error_type
                event.result = snapshot["result"]
                event.quality = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}[snapshot["result"]]
                event.confidence = snapshot["confidence"]
                event.feedback = snapshot["feedback"]
                current.result = {**current.result, **receipt, "is_correct": snapshot["result"] == "correct" if snapshot["result"] in {"correct", "incorrect"} else None, "final": True, "needs_clarification": False, "corrected": True, "reason": reason}
                action = "assessment.corrected"
            else:
                raise BridgeError("assessment_history_ambiguous", "This assessment has no supported stored verdict.", 409)
            result = snapshot["result"]
        service = LearningService(store)
        scheduler = scheduler_for_progress(tx.progress)
        service._recompute_objective_state(tx.progress, kp_id, scheduler)
        remaining_qualitative = [
            item for item in tx.progress.learning_evidence
            if item.knowledge_point_id == kp_id and item.assessment_type == "qualitative"
        ]
        if remaining_qualitative:
            tx.progress.qualitative_mastery[kp_id] = remaining_qualitative[-1].result == "correct"
            base = service.calculate_mastery(tx.progress, kp_id)
            level = base
            for item in remaining_qualitative:
                level = max(level, 1.0) if item.result == "correct" else min(level, 0.4)
            service.update_mastery(tx.progress, kp_id, level)
            tx.progress.feynman_explanations[kp_id] = remaining_qualitative[-1].learner_answer
        else:
            tx.progress.qualitative_mastery.pop(kp_id, None)
            tx.progress.feynman_explanations.pop(kp_id, None)
        current.updated_at = time.time()
        tx.put_interaction(current)
        tx.touch()
        audit = {
            "question_id": question_id,
            "knowledge_point_id": kp_id,
            "reason": reason,
            "before_result": before_result,
            "after_result": result,
            "before_expected_answer": before_expected,
            "after_expected_answer": (
                current.question.expected_answer
                if snapshot["action"] == "correct"
                else before_expected
            ),
            "attempt_count": len(attempts),
            "provenance": "codex_external_assessment",
        }
        tx.emit(action, audit, session_id="__codex_question_repair__", turn_id=event_turn)
        receipt = {
            **audit,
            "topic_id": path_id,
            "revision": tx.base_revision + 1,
            "saved": True,
            "web_url": snapshot["web_url"],
        }
        tx._conn.execute(_RECEIPT_DDL)
        tx._conn.execute(
            "INSERT INTO codex_question_repair_receipts VALUES (?, ?, ?, ?)",
            (snapshot["commit_request_id"], request_hash, json.dumps(receipt, ensure_ascii=False), time.time()),
        )
    return receipt


def commit_question_repair(store, path_id: str, request, account_id: str, workspace_id: str, workspace: str) -> dict:
    from deeptutor.learning import semantic_assessment as semantic
    from deeptutor.learning.lesson_evidence import objective_fingerprint
    from deeptutor.learning.review_strategy import scheduler_for_progress
    from deeptutor.learning.service import LearningService, MasteryInteractionError

    commit_hash = _fingerprint({
        "path_id": path_id,
        "account_id": account_id,
        "workspace_id": workspace_id,
        **request.model_dump(mode="json"),
    })
    with assessment_lease(store, path_id):
        with store._connect() as conn:
            conn.execute(_RECEIPT_DDL)
            prior = conn.execute("SELECT payload_hash, response_json FROM codex_question_repair_receipts WHERE request_id=?", (request.request_id,)).fetchone()
            if prior:
                if prior["payload_hash"] != commit_hash:
                    raise BridgeError("request_id_conflict", "Repair commit request_id was used with different content.", 409)
                return {**json.loads(prior["response_json"]), "replayed": True}
            conn.execute(_PREVIEW_DDL)
            row = conn.execute("SELECT * FROM codex_question_repair_previews WHERE preview_id=? AND path_id=?", (request.preview_id, path_id)).fetchone()
        if row is None or time.time() - float(row["created_at"]) > PREVIEW_TTL:
            raise BridgeError("preview_expired", "Create and review a fresh repair preview.", 409)
        snapshot = json.loads(row["payload_json"])
        if snapshot["account_id"] != account_id or snapshot["workspace_id"] != workspace_id:
            raise BridgeError("preview_scope_mismatch", "Repair preview belongs to another account/workspace.", 403)
        prior_events = _event_for_request(store, path_id, request.request_id)
        if prior_events:
            if prior_events[-1]["turn_id"].endswith(":" + commit_hash):
                event = prior_events[-1]
                audit = json.loads(event["payload_json"])
                action = "void" if event["event_type"] == "assessment.voided" else "correct"
                return {
                    **audit,
                    "topic_id": path_id,
                    "question_id": audit.get("question_id"),
                    "objective_id": audit.get("knowledge_point_id"),
                    "revision": event["revision"],
                    "action": action,
                    "saved": True,
                    "replayed": True,
                    "audit": audit,
                }
            raise BridgeError("request_id_conflict", "Repair request_id already committed with different content.", 409)
        progress, interaction, kp, _, attempts, evidence, _ = _interaction(store, path_id, snapshot["question_id"])
        if progress.version != snapshot["revision"] or request.confirm is not True:
            raise BridgeError("stale_revision", "Topic changed after preview; inspect and preview again.", 409)
        _assert_no_pending(store, path_id, progress)
        if _state_fingerprint(progress, interaction, attempts, evidence) != row["state_fingerprint"]:
            raise BridgeError("repair_target_changed", "Historical question, learner answer or assessment changed after preview.", 409)
        if objective_fingerprint(progress, kp.id) != snapshot["objective_fingerprint"]:
            raise BridgeError("objective_changed", "The question objective changed after preview.", 409)
        qualitative_external = any(
            item.source == "codex_external_assessment"
            and item.assessment_type == "qualitative"
            and item.question_id == interaction.interaction_id
            for item in evidence
        )
        snapshot["interaction_fingerprint"] = _fingerprint(interaction.model_dump(mode="json"))
        if snapshot["action"] == "correct" or not qualitative_external:
            corrected_question = None
            if snapshot["action"] == "correct":
                corrected_question = _verify_citations(store, progress, interaction, snapshot)
            if not qualitative_external:
                if snapshot["action"] == "void":
                    try:
                        service = LearningService(store)
                        progress_after, details = service.repair_question(
                            path_id,
                            snapshot["question_id"],
                            action="void",
                            reason=snapshot["reason"],
                            scheduler=scheduler_for_progress(progress),
                            session_id="__codex_question_repair__",
                            turn_id=f"codex-repair:{request.request_id}:{commit_hash}",
                        )
                    except MasteryInteractionError as exc:
                        raise BridgeError("repair_rejected", str(exc)[:500], 422) from exc
                    receipt = {
                        "topic_id": path_id,
                        "question_id": snapshot["question_id"],
                        "objective_id": kp.id,
                        "revision": progress_after.version,
                        "action": "void",
                        "reason": snapshot["reason"],
                        "attempt_count": len(attempts),
                        "saved": True,
                        "web_url": workspace_link(path_id, workspace),
                        "details": {key: details.get(key) for key in ("previous_expected", "previous_is_correct", "user_answer")},
                    }
                    with store._connect() as conn:
                        conn.execute(_RECEIPT_DDL)
                        conn.execute("INSERT INTO codex_question_repair_receipts VALUES (?, ?, ?, ?)", (request.request_id, commit_hash, json.dumps(receipt, ensure_ascii=False), time.time()))
                    return receipt
                verdict = semantic.Verdict(
                    result=snapshot["result"],
                    confidence=snapshot["confidence"],
                    feedback=snapshot["feedback"],
                ).receipt()
                if not verdict["final"]:
                    raise BridgeError("repair_decision_uncertain", "Correction was not final; no record changed.", 422)
                try:
                    service = LearningService(store)
                    progress_after, details = service.repair_question(
                        path_id,
                        snapshot["question_id"],
                        action="correct",
                        expected_answer=snapshot["expected_answer"],
                        reason=snapshot["reason"],
                        scheduler=scheduler_for_progress(progress),
                        session_id="__codex_question_repair__",
                        turn_id=f"codex-repair:{request.request_id}:{commit_hash}",
                        _assessment={
                            "state": semantic.fingerprint(interaction.model_dump()),
                            "objective": objective_fingerprint(progress, kp.id),
                            "question": corrected_question,
                            "receipt": verdict,
                        },
                    )
                except MasteryInteractionError as exc:
                    raise BridgeError("repair_rejected", str(exc)[:500], 422) from exc
                receipt = {
                    "topic_id": path_id,
                    "question_id": snapshot["question_id"],
                    "objective_id": kp.id,
                    "revision": progress_after.version,
                    "action": "correct",
                    "result": verdict["result"],
                    "reason": snapshot["reason"],
                    "attempt_count": len(attempts),
                    "saved": True,
                    "web_url": workspace_link(path_id, workspace),
                    "details": {key: details.get(key) for key in ("previous_expected", "expected_answer", "previous_is_correct", "is_correct")},
                }
                with store._connect() as conn:
                    conn.execute(_RECEIPT_DDL)
                    conn.execute("INSERT INTO codex_question_repair_receipts VALUES (?, ?, ?, ?)", (request.request_id, commit_hash, json.dumps(receipt, ensure_ascii=False), time.time()))
                return receipt
            if snapshot["action"] == "correct":
                snapshot["corrected_question"] = corrected_question
        snapshot["commit_request_id"] = request.request_id
        snapshot["web_url"] = workspace_link(path_id, workspace)
        return _external_repair(store, snapshot, interaction, commit_hash, workspace)


def workspace_link(path_id: str, workspace: str) -> str:
    from deeptutor_link.adapter import topic_link

    return topic_link(path_id, workspace)
