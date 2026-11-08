import json
from types import SimpleNamespace

from deeptutor_link.adapter import (
    BridgeError,
    _verify_assessment_evidence_quotes,
    selected_sources,
    source_resource,
    topic_link,
)
from deeptutor_link.leases import assessment_lease, recover_lease


def _target(store, path_id, progress, tx=None):
    from deeptutor.learning.service import LearningService

    interaction = tx.active_interaction() if tx else store.get_active_interaction(path_id)
    pending = progress.pending_question
    if interaction and pending and interaction.interaction_id != pending.question_id:
        raise BridgeError("pending_state_conflict", "The stored pending question and interaction disagree.")
    if interaction is None:
        interaction = LearningService._interaction_from_legacy_pending(progress)
        existing = (tx.get_interaction(interaction.interaction_id) if tx
                    else store.get_interaction(path_id, interaction.interaction_id)) if interaction else None
        if existing and existing.status.value in {"graded", "abandoned"}:
            raise BridgeError("pending_state_conflict", "The compatibility pending field refers to an already completed question; reconcile it with the original host service.")
    return interaction


def _state(progress, interaction):
    from deeptutor.learning.lesson_evidence import objective_fingerprint
    from deeptutor.learning.semantic_assessment import fingerprint

    question = interaction.question
    return fingerprint({
        "revision": progress.version,
        "question": question.model_dump(mode="json"),
        "status": interaction.status.value,
        "answer": interaction.user_answer,
        "answers": interaction.answers,
        "objective": objective_fingerprint(progress, question.knowledge_point_id)
        if not question.questions else None,
    })


def _validate_question(store, progress, question):
    from deeptutor.learning.semantic_assessment import validate_question

    if question.source_evidence and not question.source_evidence.get("assessment_contract"):
        raise BridgeError("pending_question_binding_required", "The original PDF question has no verified question/key binding; its lesson packet alone cannot authorize external grading.", 422)
    validate_question(store, progress, question)


def pending_summary(store, path_id, progress=None):
    progress = progress or store.load(path_id)
    if progress is None:
        raise BridgeError("unknown_topic", "Topic not found in this account/workspace.", 404)
    present = bool(store.get_active_interaction(path_id) or progress.pending_question)
    return {
        "present": present,
        "kind": "persisted_question" if present else None,
        "action": "read_pending_question" if present else None,
        "command": "CLIENT pending-question" if present else None,
        "blocks_new_assessment": present,
        "blocks_maintenance_by_itself": False,
        "active_work_check": "Inspect mutation_lease and actual running service tasks separately.",
    }


def pending_question(store, path_id, workspace):
    from deeptutor.learning.lesson_evidence import LessonUnavailable
    from deeptutor.learning.pending import has_option_bodies, public_pending_question

    if not store.exists(path_id):
        raise BridgeError("unknown_topic", "Topic not found in this account/workspace.", 404)
    with store.transaction(path_id) as tx:
        progress = tx.progress
        interaction = _target(store, path_id, progress, tx)
        result = {
            "topic_id": path_id,
            "revision": tx.base_revision,
            "pending": pending_summary(store, path_id, progress),
            "mutation_lease": recover_lease(store, path_id, inspect_only=True),
            "question": None,
            "host_chat_model_used": False,
            "untrusted_content": True,
        }
        if interaction is None:
            return result
        question = interaction.question
        public = public_pending_question(question).to_dict()
        if len(json.dumps(public, ensure_ascii=False)) > 24000:
            raise BridgeError("pending_question_too_large", "The original question exceeds the bounded client projection.", 422)
        blocker = (
            "pending_group_unsupported" if question.questions
            else "pending_visual_review_required" if question.visual_context
            else "pending_options_unavailable" if question.question_type == "choice" and not has_option_bodies(question.choice_map)
            else "pending_answer_too_large" if len(interaction.user_answer) > 8000
            else None
        )
        if blocker is None:
            try:
                topic = store.get_topic(path_id, progress=progress)
                objective, sources = selected_sources(progress, topic, question.knowledge_point_id)
                if objective.required_visual_tasks:
                    blocker = "pending_visual_review_required"
                elif topic and topic.metadata.status != "active":
                    blocker = "archived_topic"
                elif any(source.kind.value not in {"goal", "file", "knowledge_base"} for source in sources):
                    blocker = "assessment_source_unsupported"
                else:
                    _validate_question(store, progress, question)
            except BridgeError as exc:
                blocker = exc.code
            except (LessonUnavailable, ValueError):
                blocker = "pending_evidence_unavailable"
        result.update(
            question=public,
            question_id=interaction.interaction_id,
            objective_id=question.knowledge_point_id,
            status=interaction.status.value,
            stored_answer=interaction.user_answer if interaction.status.value == "answered" and len(interaction.user_answer) <= 8000 else None,
            answer_revision_allowed=interaction.status.value == "answered" and interaction.result.get("final") is False,
            previous_assessment={key: interaction.result[key] for key in ("result", "final", "needs_clarification", "confidence", "feedback") if key in interaction.result},
            state_fingerprint=_state(progress, interaction),
            continuation={
                "supported": blocker is None,
                "blocker": blocker,
                "command": "CLIENT answer-pending-question --file -" if blocker is None else None,
                "requires_real_answer": True,
                "requires_current_source_evidence": True,
                "host_chat_model_required": False,
            },
        )
        return result


class _ServiceTransaction:
    def __init__(self, store, path_id, tx):
        self.store, self.path_id, self.tx = store, path_id, tx

    def __getattr__(self, name):
        return getattr(self.store, name)

    def mutate(self, book_id, mutation, *, create=False):
        if book_id != self.path_id or create:
            raise ValueError("Unexpected service transaction scope.")
        return self.tx.progress, mutation(self.tx)


def answer_pending_question(store, path_id, request, workspace):
    from deeptutor.learning import semantic_assessment as semantic
    from deeptutor.learning.lesson_evidence import LessonUnavailable, objective_fingerprint
    from deeptutor.learning.review_strategy import scheduler_for_progress
    from deeptutor.learning.service import LearningService, MasteryInteractionError
    from deeptutor.learning.storage import LearningConflictError

    if not store.exists(path_id):
        raise BridgeError("unknown_topic", "Topic not found in this account/workspace.", 404)
    digest = semantic.fingerprint(request.model_dump(mode="json"))
    with assessment_lease(store, path_id):
        with store.transaction(path_id) as tx:
            previous = tx._conn.execute(
                "SELECT payload_json FROM mastery_events WHERE path_id=? AND event_type=? AND turn_id=?",
                (path_id, "link.pending_answer", request.request_id),
            ).fetchone()
            if previous:
                record = json.loads(previous["payload_json"])
                if record["digest"] != digest:
                    raise BridgeError("request_id_conflict", "Pending answer request_id was used with another payload.")
                return {**record["receipt"], "deduplicated": True}
            if tx.base_revision != request.expected_revision:
                raise LearningConflictError(path_id, request.expected_revision, tx.base_revision)
            interaction = _target(store, path_id, tx.progress, tx)
            if interaction is None or interaction.interaction_id != request.question_id:
                raise BridgeError("pending_question_changed", "Refresh pending-question; the original question is no longer pending.")
            if _state(tx.progress, interaction) != request.state_fingerprint:
                raise BridgeError("pending_question_changed", "Refresh pending-question; its question, answer or objective changed.")
            question = interaction.question
            if question.questions:
                raise BridgeError("pending_group_unsupported", "Grouped original questions cannot yet use this external continuation contract.", 422)
            if question.visual_context:
                raise BridgeError("pending_visual_review_required", "This original visual question requires its verified image review contract; text-only continuation cannot grade it.", 422)
            if not request.learner_answer.strip():
                raise BridgeError("learner_answer_required", "Wait for the learner's actual answer.", 422)
            if request.revise_answer and not (
                interaction.status.value == "answered" and interaction.result.get("final") is False
            ):
                raise BridgeError("pending_answer_revision_unavailable", "Only an explicitly non-final original assessment accepts a revised learner answer.", 422)
            if interaction.status.value == "answered" and interaction.user_answer != request.learner_answer and not request.revise_answer:
                raise BridgeError("stored_answer_mismatch", "Evaluate the exact stored learner answer; do not replace it.")
            if question.question_type == "choice":
                from deeptutor.learning.pending import has_option_bodies, is_readable_choice_answer

                if not has_option_bodies(question.choice_map):
                    raise BridgeError("pending_options_unavailable", "The original choice question has no readable option bodies; restore its original options first.", 422)
                if not is_readable_choice_answer(request.learner_answer, question.choice_map):
                    raise BridgeError("pending_answer_ambiguous", "Ask the learner to select one unambiguous original option.", 422)
            topic = store.get_topic(path_id, progress=tx.progress)
            if topic and topic.metadata.status != "active":
                raise BridgeError("archived_topic", "Restore this topic before recording an answer.")
            objective, sources = selected_sources(tx.progress, topic, question.knowledge_point_id)
            cited = set(request.source_ids)
            if not cited.issubset({source.id for source in sources}):
                raise BridgeError("source_changed", "The submitted evidence cites material outside this objective.")
            for source in sources:
                if source.id not in cited:
                    continue
                if not source.available:
                    raise BridgeError("source_unavailable", "A selected material is unavailable.")
                if source.kind.value in {"file", "knowledge_base"}:
                    source_resource(source)
            if any(source.kind.value not in {"goal", "file", "knowledge_base"} for source in sources):
                raise BridgeError("assessment_source_unsupported", "This continuation supports goal, file and knowledge-base sources only.", 422)
            if any(source.kind.value != "goal" for source in sources) and not any(
                source.id in cited and source.kind.value in {"file", "knowledge_base"} for source in sources
            ):
                raise BridgeError("source_required", "Read and cite the original selected material before evaluating this question.", 422)
            evidence = SimpleNamespace(
                objective_id=objective.id,
                source_ids=request.source_ids,
                evidence_quotes=request.evidence_quotes,
            )
            _verify_assessment_evidence_quotes(store, tx.progress, sources, evidence)
            try:
                _validate_question(store, tx.progress, question)
            except (LessonUnavailable, ValueError) as exc:
                raise BridgeError("pending_evidence_unavailable", str(exc)[:500], 422) from exc
            if objective.required_visual_tasks:
                raise BridgeError("pending_visual_review_required", "The original objective requires inspected image evidence; this text-only continuation cannot grade it.", 422)
            verdict = semantic.Verdict(
                result=request.result, confidence=request.confidence, feedback=request.feedback
            ).receipt()
            service = LearningService(_ServiceTransaction(store, path_id, tx))
            if request.revise_answer:
                interaction = service.record_question_answer(
                    path_id, request.learner_answer, interaction_id=interaction.interaction_id
                )
            if not request.independent:
                interaction.question.assistance_used = True
                if tx.progress.pending_question:
                    tx.progress.pending_question.assistance_used = True
            if not request.independent or tx.get_interaction(interaction.interaction_id) is None:
                tx.put_interaction(interaction)
            assessment = {
                "state": semantic.fingerprint(interaction.model_dump()),
                "objectives": {objective.id: objective_fingerprint(tx.progress, objective.id)},
                "verdicts": {question.question_id: verdict},
            }
            try:
                _, graded, replayed = service.grade_interaction(
                    path_id,
                    answer=request.learner_answer,
                    question_id=interaction.interaction_id,
                    scheduler=scheduler_for_progress(tx.progress),
                    _assessment=assessment,
                )
            except MasteryInteractionError as exc:
                raise BridgeError("pending_answer_unavailable", str(exc)[:500], 422) from exc
            receipt = {
                **verdict,
                "request_id": request.request_id,
                "topic_id": path_id,
                "question_id": graded.interaction_id,
                "objective_id": objective.id,
                "revision": tx.base_revision + 1,
                "saved": True,
                "progress_recorded": verdict["final"],
                "deduplicated": replayed,
                "host_chat_model_used": False,
                "web_url": topic_link(path_id, workspace),
            }
            tx.emit(
                "link.pending_answer",
                {"digest": digest, "receipt": receipt},
                session_id=interaction.session_id,
                turn_id=request.request_id,
            )
        return receipt
