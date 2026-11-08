from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"


class AssessmentEvidenceQuote(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_id: str = Field(min_length=1, max_length=200)
    filename: str = Field(min_length=1, max_length=1000)
    quote: str = Field(default="", max_length=1200)
    source_index: int | None = Field(default=None, ge=0, le=5)
    page_number: int | None = Field(default=None, ge=1, le=10000)
    visual_observation: str = Field(default="", max_length=2000)
    support_statement: str = Field(default="", max_length=2000)


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    assessment_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    expected_revision: int = Field(ge=0)
    objective_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=8, max_length=4000)
    reference_answer: str = Field(default="", max_length=4000)
    learner_answer: str = Field(min_length=1, max_length=8000)
    result: Literal["correct", "partial", "incorrect"]
    confidence: float = Field(ge=0, le=1)
    feedback: str = Field(min_length=8, max_length=2000)
    independent: bool = True
    source_ids: list[str] = Field(default_factory=list, max_length=30)
    evidence_quotes: list[AssessmentEvidenceQuote] = Field(default_factory=list, max_length=12)
    question_citations: list[AssessmentEvidenceQuote] = Field(default_factory=list, max_length=12)


class Retrieval(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    objective_id: str = Field(min_length=1, max_length=200)
    source_id: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=8)


class PendingAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    request_id: str = Field(pattern=UUID_PATTERN)
    expected_revision: int = Field(ge=0)
    question_id: str = Field(min_length=1, max_length=200)
    state_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    learner_answer: str = Field(min_length=1, max_length=8000)
    result: Literal["correct", "partial", "incorrect", "ungraded"]
    confidence: float = Field(ge=0, le=1)
    feedback: str = Field(min_length=8, max_length=2000)
    independent: bool = True
    revise_answer: bool = False
    source_ids: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=30)
    evidence_quotes: list[AssessmentEvidenceQuote] = Field(default_factory=list, max_length=12)


class NativeTutorCall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    client_id: str = Field(default="deeptutor-link", min_length=1, max_length=100)
    session_id: str | None = Field(default=None, min_length=1, max_length=200)
    client_session_id: str | None = Field(default=None, min_length=1, max_length=200)
    turn_id: str = Field(min_length=1, max_length=200)
    request_id: str = Field(pattern=UUID_PATTERN)
    tool: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]{0,99}$")
    arguments: dict = Field(default_factory=dict)
    user_message: str | None = Field(default=None, max_length=20000)
    assistant_message: str | None = Field(default=None, max_length=24000)
    mode: Literal["outline", "study", "review"] | None = None
    language: str | None = Field(default=None, max_length=32)
    confirm: Literal[True]

    @model_validator(mode="after")
    def require_stable_client_session(self):
        if not self.session_id and not self.client_session_id:
            raise ValueError("session_id or client_session_id is required")
        if self.session_id and self.client_session_id and self.session_id != self.client_session_id:
            raise ValueError("session_id and client_session_id must match")
        return self


class DirectCapabilityCall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    capability: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    message: str = Field(min_length=1, max_length=20000)
    request_id: str = Field(pattern=UUID_PATTERN)
    session_id: str | None = Field(default=None, max_length=200)
    knowledge_bases: list[str] = Field(default_factory=list, max_length=10)
    config: dict = Field(default_factory=dict)
    language: str | None = Field(default=None, max_length=32)
    book_references: list[dict] = Field(default_factory=list, max_length=20)
    reading_references: list[dict] = Field(default_factory=list, max_length=20)
    direct_dt_conversation: Literal[True]
    confirm: Literal[True]


class DirectCapabilityReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    session_id: str = Field(min_length=1, max_length=200)
    turn_id: str = Field(min_length=1, max_length=200)
    request_id: str = Field(pattern=UUID_PATTERN)
    message: str = Field(min_length=1, max_length=20000)
    after_seq: int = Field(default=0, ge=0)
    confirm: Literal[True]


class RouteModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class RouteObjective(RouteModel):
    id: str = Field(default="", max_length=200)
    client_ref: str = Field(default="", max_length=200)
    name: str = Field(min_length=2, max_length=200)
    type: Literal["memory", "concept", "procedure", "design"] = "concept"
    # Omitted relations on existing objectives retain their stored values.
    prerequisite_ids: list[str] | None = Field(default=None, max_length=200)
    topic_source_ids: list[str] | None = Field(default=None, max_length=80)


class RouteModule(RouteModel):
    id: str = Field(default="", max_length=200)
    name: str = Field(min_length=1, max_length=200)
    objective: str | None = Field(default=None, max_length=300)
    pass_threshold: float | None = Field(default=None, ge=0, le=1)
    knowledge_points: list[RouteObjective] = Field(min_length=1, max_length=200)


class RouteSource(RouteModel):
    client_ref: str = Field(min_length=1, max_length=200)
    kind: Literal["knowledge_base", "file"]
    source_id: str = Field(min_length=1, max_length=300)
    label: str = Field(min_length=1, max_length=200)
    kb_name: str = Field(default="", max_length=300)


class RouteProfile(RouteModel):
    prior_knowledge: str = Field(default="", max_length=2000)
    target_level: str = Field(default="", max_length=2000)
    time_budget: str = Field(default="", max_length=600)
    preferences: str = Field(default="", max_length=2000)
    teaching_strategy: Literal["probe_first", "teach_first"] = "probe_first"


class RouteCreate(RouteModel):
    creation_id: str = Field(pattern=UUID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=2000)
    description: str = Field(default="", max_length=500)
    modules: list[RouteModule] = Field(min_length=1, max_length=20)
    sources: list[RouteSource] = Field(default_factory=list, max_length=16)
    profile: RouteProfile | None = None


class RoutePreview(RouteModel):
    request_id: str = Field(pattern=UUID_PATTERN)
    expected_revision: int = Field(ge=0)
    modules: list[RouteModule] = Field(min_length=1, max_length=20)


class RouteCommit(RouteModel):
    request_id: str = Field(pattern=UUID_PATTERN)
    preview_id: int = Field(ge=1)


class PDFPageSelection(RouteModel):
    source_id: str = Field(min_length=1, max_length=200)
    filename: str = Field(min_length=1, max_length=1000)
    page_index: int = Field(ge=0)


class PDFPrepare(RouteModel):
    request_id: str = Field(pattern=UUID_PATTERN)
    expected_revision: int = Field(ge=0)
    objective_id: str = Field(min_length=1, max_length=200)
    directory: str = Field(default="", max_length=1000)
    pages: list[PDFPageSelection] = Field(min_length=1, max_length=6)


class PDFCommitSource(RouteModel):
    page_number: int = Field(ge=0, le=5)
    quote: str = Field(default="", max_length=1200)
    supports: str = Field(min_length=8, max_length=1000)
    visual_observation: str = Field(default="", max_length=2000)


class PDFCommit(RouteModel):
    request_id: str = Field(pattern=UUID_PATTERN)
    preparation_id: str = Field(pattern=UUID_PATTERN)
    expected_revision: int = Field(ge=0)
    objective_id: str = Field(min_length=1, max_length=200)
    purpose: Literal["teaching", "assessment"]
    coverage_summary: str = Field(min_length=20, max_length=2000)
    coverage_complete: bool
    sources: list[PDFCommitSource] = Field(min_length=1, max_length=6)
    confirm: Literal[True]


class QuestionRepairCitation(RouteModel):
    source_index: int = Field(ge=0, le=5)
    quote: str = Field(default="", max_length=1200)
    visual_observation: str = Field(default="", max_length=2000)
    support_statement: str = Field(min_length=10, max_length=2000)


class QuestionRepairPreview(RouteModel):
    request_id: str = Field(pattern=UUID_PATTERN)
    expected_revision: int = Field(ge=0)
    question_id: str = Field(min_length=1, max_length=200)
    action: Literal["correct", "void"]
    reason: str = Field(min_length=8, max_length=500)
    expected_answer: str = Field(default="", max_length=4000)
    result: Literal["correct", "partial", "incorrect"] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    feedback: str = Field(default="", max_length=2000)
    citations: list[QuestionRepairCitation] = Field(default_factory=list, max_length=6)


class QuestionRepairCommit(RouteModel):
    request_id: str = Field(pattern=UUID_PATTERN)
    preview_id: str = Field(pattern=UUID_PATTERN)
    confirm: Literal[True]
