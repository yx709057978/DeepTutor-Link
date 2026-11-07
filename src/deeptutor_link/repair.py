"""Package-independent guard injected into the two original repair entrypoints."""


def guard_source(progress):
    return f"""if interaction is not None and (
    interaction.result.get("receipt", {{}}).get("source") == "codex_external_assessment"
):
    from deeptutor.learning.policy import QUALITATIVE_TYPES, find_knowledge_point
    from deeptutor.learning.service import MasteryInteractionError
    _codex_kp, _, _ = find_knowledge_point(
        {progress}, interaction.question.knowledge_point_id
    )
    _codex_qualitative = any(
        event.assessment_type == "qualitative"
        and (event.question_id == interaction.interaction_id
             or event.turn_id == interaction.interaction_id)
        for event in {progress}.learning_evidence
    )
    if _codex_qualitative or _codex_kp is not None and _codex_kp.type in QUALITATIVE_TYPES:
        raise MasteryInteractionError(
            "AI assistant qualitative assessment repair is unsupported in this extension version. "
            "No record was changed. Submit a fresh independent assessment instead."
        )"""
