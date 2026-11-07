import json
from uuid import uuid4

from fastapi.testclient import TestClient
from test_http import headers

VISUAL_OBSERVATION = (
    "The arrow runs from the lower labelled state toward the higher state, showing an increase."
)


def _make_pdf_source(account, *, visual=False):
    import pymupdf
    from deeptutor.learning.models import TopicMetadata, TopicSource
    from deeptutor.multi_user.paths import user_context
    from deeptutor.services.path_service import get_path_service
    from deeptutor.services.workspace.context import workspace_context

    kb_ref = f"workspace:{account['workspace']}:kb:original-kb"
    text = (
        "Synthetic PDF evidence states the corrected explanation: "
        "a higher membrane permeability increases the measured diffusion rate. "
        "This passage is intentionally long enough for exact source citation."
    )
    with user_context(account["user"]), workspace_context(account["workspace"]):
        root = get_path_service().get_knowledge_bases_root()
        raw = root / "original-kb/raw"
        raw.mkdir(parents=True, exist_ok=True)
        pdf_path = raw / "lesson.pdf"
        pdf = pymupdf.open()
        page = pdf.new_page(width=500, height=350)
        page.insert_textbox((35, 30, 465, 120), text, fontsize=12)
        page.draw_line((80, 220), (390, 220), color=(0, 0, 0), width=2)
        page.draw_line((390, 220), (350, 200), color=(0, 0, 0), width=2)
        if visual:
            page.insert_text((90, 250), "lower", fontsize=12)
            page.insert_text((350, 250), "higher", fontsize=12)
        pdf.save(pdf_path)
        pdf.close()
        rendered = pymupdf.open(pdf_path)
        extracted_text = rendered[0].get_text()
        rendered.close()
        repair_quote = next(
            line.strip() for line in extracted_text.splitlines() if len(line.strip()) >= 20
        )
        (root / "kb_config.json").write_text(
            json.dumps({"knowledge_bases": {"original-kb": {"rag_provider": "llamaindex"}}})
        )
        progress = account["store"].load("same-topic-id")
        kp = progress.modules[0].knowledge_points[0]
        kp.topic_source_ids = ["source-pdf"]
        if visual:
            kp.required_visual_tasks = ["relationship"]
        account["store"].save(progress)
        account["store"].put_topic(
            TopicMetadata(path_id="same-topic-id", goal="Read the synthetic PDF evidence"),
            [
                TopicSource(
                    id="source-pdf",
                    kind="knowledge_base",
                    source_id=kb_ref,
                    label="Synthetic PDF",
                )
            ],
        )
    return {
        "kb_ref": kb_ref,
        "text": text,
        "repair_quote": repair_quote,
        "pdf_path": pdf_path,
    }


def _commit_body(source, preparation, request_id, *, visual=False, confirm=True):
    observation = VISUAL_OBSERVATION if visual else "The printed passage gives the measured diffusion relationship."
    return {
        "request_id": request_id,
        "preparation_id": preparation["preparation_id"],
        "expected_revision": preparation["revision"],
        "objective_id": "original-kp",
        "purpose": "assessment",
        "coverage_summary": "All named objective facets are covered by this original page and its labels.",
        "coverage_complete": True,
        "confirm": confirm,
        "sources": [{
            "page_number": 0,
            "quote": source["repair_quote"],
            "supports": "Supports the relationship between permeability and measured diffusion rate.",
            "visual_observation": observation,
        }],
    }


def _prepare(client, account, source, *, visual=False, commit_request_id=None):
    topic = account["store"].load("same-topic-id")
    listed = client.get(
        "/api/link/v1/topics/same-topic-id/pdfs",
        headers=headers(account),
        params={"objective_id": "original-kp"},
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["pdfs"] == [
        {"source_id": "source-pdf", "filename": "lesson.pdf", "page_count": 1}
    ]
    response = client.post(
        "/api/link/v1/topics/same-topic-id/pdf-preparations",
        headers=headers(account),
        json={
            "request_id": str(uuid4()),
            "expected_revision": topic.version,
            "objective_id": "original-kp",
            "pages": [{"source_id": "source-pdf", "filename": "lesson.pdf", "page_index": 0}],
        },
    )
    assert response.status_code == 200, response.text
    packet = response.json()
    image = client.get(packet["pages"][0]["image_path"], headers=headers(account))
    assert image.status_code == 200, image.text
    assert image.headers["content-type"].startswith("image/")
    assert image.content.startswith(b"\x89PNG\r\n\x1a\n") or image.content.startswith(b"\xff\xd8")
    request_id = commit_request_id or str(uuid4())
    commit = client.post(
        "/api/link/v1/topics/same-topic-id/pdf-evidence/commits",
        headers=headers(account),
        json=_commit_body(source, packet, request_id, visual=visual),
    )
    assert commit.status_code == 200, commit.text
    assert commit.json()["saved"] is True
    return packet, commit.json()


def _assessment(account, source, *, result="correct", visual=False):
    return {
        "assessment_id": str(uuid4()),
        "expected_revision": account["store"].load("same-topic-id").version,
        "objective_id": "original-kp",
        "question": "Explain how permeability changes the measured diffusion rate.",
        "reference_answer": "A higher membrane permeability increases the measured diffusion rate.",
        "learner_answer": "Higher permeability lets more material pass, increasing the measured rate.",
        "result": result,
        "confidence": 0.95,
        "feedback": "The explanation accurately identifies the direction of the measured change.",
        "independent": True,
        "source_ids": ["source-pdf"],
        "evidence_quotes": [{
            "source_id": "source-pdf",
            "filename": "lesson.pdf",
            "page_number": 1,
            "quote": source["repair_quote"],
            **({"visual_observation": VISUAL_OBSERVATION} if visual else {}),
        }],
        "question_citations": [{
            "source_id": "source-pdf",
            "filename": "lesson.pdf",
            "source_index": 0,
            "quote": source["repair_quote"],
            **({"visual_observation": VISUAL_OBSERVATION} if visual else {}),
        }],
    }


def _seed_native_question(account, question_id):
    from deeptutor.learning.models import InteractionStatus, MasteryInteraction, PendingQuestion
    from deeptutor.learning.review_strategy import scheduler_for_progress
    from deeptutor.learning.service import LearningService

    packet = account["store"].load("same-topic-id").lesson_evidence["original-kp"]
    with account["store"].transaction("same-topic-id") as tx:
        scheduler = scheduler_for_progress(tx.progress)
        LearningService(account["store"])._apply_grade(
            tx.progress,
            question_id=question_id,
            knowledge_point_id="original-kp",
            module_id="m",
            user_answer="4",
            expected_answer="5",
            question_type="short",
            scheduler=scheduler,
        )
        tx.put_interaction(
            MasteryInteraction(
                interaction_id=question_id,
                path_id="same-topic-id",
                status=InteractionStatus.GRADED,
                question=PendingQuestion(
                    question_id=question_id,
                    knowledge_point_id="original-kp",
                    module_id="m",
                    prompt="What does the synthetic equation give for two plus two?",
                    expected_answer="5",
                    source_evidence=json.loads(json.dumps(packet)),
                ),
                user_answer="4",
            )
        )
        tx.touch()


def _delete_repair_receipt(account, request_id):
    with account["store"]._connect() as conn:
        conn.execute("DELETE FROM codex_question_repair_receipts WHERE request_id=?", (request_id,))


def _repair_native_question(client, account, source, question_id, action):
    revision = account["store"].load("same-topic-id").version
    preview_body = {
        "request_id": str(uuid4()),
        "expected_revision": revision,
        "question_id": question_id,
        "action": action,
        "reason": "The stored key is wrong; the learner's answer is source-backed."
        if action == "correct"
        else "Void this duplicated synthetic attempt.",
    }
    if action == "correct":
        preview_body.update(
            {
                "expected_answer": "4",
                "result": "correct",
                "confidence": 0.95,
                "feedback": "The learner's answer matches the corrected source-backed key.",
                "citations": [{
                    "source_index": 0,
                    "quote": source["repair_quote"],
                    "support_statement": "The cited passage establishes the corrected arithmetic key.",
                }],
            }
        )
    preview = client.post(
        "/api/link/v1/topics/same-topic-id/question-repairs/previews",
        headers=headers(account),
        json=preview_body,
    )
    assert preview.status_code == 200, preview.text
    conflict_preview = client.post(
        "/api/link/v1/topics/same-topic-id/question-repairs/previews",
        headers=headers(account),
        json={**preview_body, "request_id": str(uuid4())},
    )
    assert conflict_preview.status_code == 200, conflict_preview.text
    request_id = str(uuid4())
    commit = client.post(
        "/api/link/v1/topics/same-topic-id/question-repairs/commits",
        headers=headers(account),
        json={"request_id": request_id, "preview_id": preview.json()["preview_id"], "confirm": True},
    )
    assert commit.status_code == 200, commit.text
    assert commit.json()["saved"] is True
    snapshot = account["store"].load("same-topic-id").model_dump()

    conflict = client.post(
        "/api/link/v1/topics/same-topic-id/question-repairs/commits",
        headers=headers(account),
        json={
            "request_id": request_id,
            "preview_id": conflict_preview.json()["preview_id"],
            "confirm": True,
        },
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["code"] == "request_id_conflict"
    _delete_repair_receipt(account, request_id)
    replay = client.post(
        "/api/link/v1/topics/same-topic-id/question-repairs/commits",
        headers=headers(account),
        json={"request_id": request_id, "preview_id": preview.json()["preview_id"], "confirm": True},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["saved"] is True
    assert replay.json()["replayed"] is True
    assert replay.json()["revision"] == commit.json()["revision"]
    assert account["store"].load("same-topic-id").model_dump() == snapshot
    return commit.json(), replay.json()


def test_pdf_prepare_render_commit_and_question_repair_without_host_models(http_host, monkeypatch):

    app, (account, _) = http_host
    source = _make_pdf_source(account, visual=True)
    monkeypatch.setattr(
        "deeptutor.services.llm.config.get_llm_config",
        lambda: (_ for _ in ()).throw(ValueError("not configured")),
    )
    monkeypatch.setattr(
        "deeptutor.services.embedding.config.get_embedding_config",
        lambda: (_ for _ in ()).throw(ValueError("not configured")),
    )
    with TestClient(app) as client:
        commit_request_id = str(uuid4())
        preparation, first = _prepare(
            client, account, source, visual=True, commit_request_id=commit_request_id
        )
        replay = client.post(
            "/api/link/v1/topics/same-topic-id/pdf-evidence/commits",
            headers=headers(account),
            json=_commit_body(source, preparation, commit_request_id, visual=True),
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["saved"] is True
        assert replay.json()["replayed"] is True
        assert replay.json()["revision"] == first["revision"]
        assert account["store"].load("same-topic-id").version == first["revision"]
        text_only = client.post(
            "/api/link/v1/topics/same-topic-id/assessments",
            headers=headers(account),
            json=_assessment(account, source, result="incorrect"),
        )
        assert text_only.status_code == 422, text_only.text
        assert text_only.json()["detail"]["code"] == "visual_question_citation_required"
        assert account["store"].load("same-topic-id").version == first["revision"]
        before = account["store"].load("same-topic-id")
        assert before.qualitative_mastery.get("original-kp", False) is False
        saved = client.post(
            "/api/link/v1/topics/same-topic-id/assessments",
            headers=headers(account),
            json=_assessment(account, source, result="incorrect", visual=True),
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["saved"] is True
        question_id = f"codex-{saved.json()['assessment_id']}"
        inspected = client.get(
            f"/api/link/v1/topics/same-topic-id/questions/{question_id}/repair",
            headers=headers(account),
        )
        assert inspected.status_code == 200, inspected.text
        assert inspected.json()["question_id"] == question_id
        preview_body = {
            "request_id": str(uuid4()),
            "expected_revision": account["store"].load("same-topic-id").version,
            "question_id": question_id,
            "action": "correct",
            "reason": "The first qualitative rubric omitted the measured direction.",
            "expected_answer": "Higher permeability increases the measured diffusion rate.",
            "result": "correct",
            "confidence": 0.95,
            "feedback": "The historical explanation matches the corrected source-backed rubric.",
            "citations": [{
                "source_index": 0,
                "quote": source["repair_quote"],
                "visual_observation": VISUAL_OBSERVATION,
                "support_statement": "This passage states both the variable and measured direction.",
            }],
        }
        preview = client.post(
            "/api/link/v1/topics/same-topic-id/question-repairs/previews",
            headers=headers(account),
            json=preview_body,
        )
        assert preview.status_code == 200, preview.text
        commit = client.post(
            "/api/link/v1/topics/same-topic-id/question-repairs/commits",
            headers=headers(account),
            json={"request_id": str(uuid4()), "preview_id": preview.json()["preview_id"], "confirm": True},
        )
        assert commit.status_code == 200, commit.text
        assert commit.json()["saved"] is True
        repaired = account["store"].load("same-topic-id")
        assert repaired.qualitative_mastery["original-kp"] is True
        assert repaired.qualitative_mastery["original-kp"] != before.qualitative_mastery.get(
            "original-kp", False
        )
        assert repaired.feynman_explanations["original-kp"] == (
            "Higher permeability lets more material pass, increasing the measured rate."
        )
        assert account["store"].load("same-topic-id").version == commit.json()["revision"]
        bound = account["store"].get_interaction("same-topic-id", question_id).question
        citations = bound.source_evidence["assessment_contract"]["citations"]
        assert {"index": 0, "quote": source["repair_quote"]} in citations
        assert {"index": 0, "visual_observation": VISUAL_OBSERVATION} in citations
        from deeptutor.api.routers import mastery_path
        from deeptutor.multi_user.paths import user_context
        from deeptutor.services.workspace.context import workspace_context

        monkeypatch.setattr(mastery_path, "LearningStore", lambda: account["store"])
        with user_context(account["user"]), workspace_context(account["workspace"]):
            with TestClient(app) as web_client:
                report = web_client.get(
                    "/api/mastery-paths/progress/same-topic-id/objectives/original-kp",
                    headers=headers(account),
                )
        assert report.status_code == 200, report.text
        objective = report.json()["objective"]
        assert objective["id"] == "original-kp"
        assert objective["explanation"] == (
            "Higher permeability lets more material pass, increasing the measured rate."
        )
        assert objective["evidence_count"] == 1
        assert objective["evidence"][-1]["result"] == "correct"


def test_pdf_rejects_changed_source_stale_revision_and_unconfirmed_commit(http_host):
    import pymupdf
    from deeptutor.multi_user.paths import user_context
    from deeptutor.services.workspace.context import workspace_context

    app, (account, _) = http_host
    source = _make_pdf_source(account)
    with TestClient(app) as client:
        preparation, _ = _prepare(client, account, source)
        with user_context(account["user"]), workspace_context(account["workspace"]):
            pdf = pymupdf.open(source["pdf_path"])
            pdf[0].insert_text((35, 320), "Changed after page preparation", fontsize=12)
            pdf.saveIncr()
            pdf.close()
        stale_image = client.get(preparation["pages"][0]["image_path"], headers=headers(account))
        assert stale_image.status_code == 409
        # The changed content changes the objective scope fingerprint before rendering.
        assert stale_image.json()["detail"]["code"] == "stale_preparation"

        version_after_prepare = account["store"].load("same-topic-id").version
        unconfirmed = client.post(
            "/api/link/v1/topics/same-topic-id/pdf-evidence/commits",
            headers=headers(account),
            json=_commit_body(source, preparation, str(uuid4()), confirm=False),
        )
        assert unconfirmed.status_code == 422, unconfirmed.text
        assert account["store"].load("same-topic-id").version == version_after_prepare

        body = {
            "request_id": str(uuid4()),
            "preparation_id": preparation["preparation_id"],
            "expected_revision": preparation["revision"],
            "objective_id": "original-kp",
            "purpose": "assessment",
            "coverage_summary": "All named objective facets are covered by this original page and its labels.",
            "coverage_complete": True,
            "confirm": True,
            "sources": [{
                "page_number": 0,
                "quote": source["repair_quote"],
                "supports": "Supports the relationship between permeability and measured diffusion rate.",
            }],
        }
        stale_commit = client.post(
            "/api/link/v1/topics/same-topic-id/pdf-evidence/commits",
            headers=headers(account),
            json=body,
        )
        assert stale_commit.status_code == 409


def test_qualitative_void_preserves_later_assessment_and_rejects_web_pending(http_host):
    from deeptutor.learning.models import InteractionStatus, MasteryInteraction, PendingQuestion

    app, (account, _) = http_host
    source = _make_pdf_source(account)
    with TestClient(app) as client:
        _prepare(client, account, source)
        first = client.post(
            "/api/link/v1/topics/same-topic-id/assessments",
            headers=headers(account),
            json=_assessment(account, source, result="correct"),
        )
        assert first.status_code == 200, first.text
        second = client.post(
            "/api/link/v1/topics/same-topic-id/assessments",
            headers=headers(account),
            json=_assessment(account, source, result="incorrect"),
        )
        assert second.status_code == 200, second.text
        first_id = f"codex-{first.json()['assessment_id']}"
        inspect = client.get(
            f"/api/link/v1/topics/same-topic-id/questions/{first_id}/repair",
            headers=headers(account),
        )
        assert inspect.status_code == 200, inspect.text
        inspected = inspect.json()
        assert inspected["question_id"] == first_id
        assert inspected["assessment"]["result"] == "correct"
        assert inspected["assessment"]["source"] == "codex_external_assessment"
        assert inspected["pending_interaction"] is False
        preview = client.post(
            "/api/link/v1/topics/same-topic-id/question-repairs/previews",
            headers=headers(account),
            json={
                "request_id": str(uuid4()),
                "expected_revision": account["store"].load("same-topic-id").version,
                "question_id": first_id,
                "action": "void",
                "reason": "The original learner answer was copied from the prompt.",
            },
        )
        assert preview.status_code == 200, preview.text
        with account["store"].transaction("same-topic-id") as tx:
            pending = PendingQuestion(
                question_id="web-pending",
                knowledge_point_id="original-kp",
                module_id="m",
                prompt="Website-owned pending question",
            )
            tx.progress.pending_question = pending
            tx.put_interaction(MasteryInteraction(
                interaction_id="web-pending",
                path_id="same-topic-id",
                question=pending,
                status=InteractionStatus.AWAITING_INPUT,
                session_id="website",
            ))
            tx.touch()
        blocked = client.post(
            "/api/link/v1/topics/same-topic-id/question-repairs/commits",
            headers=headers(account),
            json={"request_id": str(uuid4()), "preview_id": preview.json()["preview_id"], "confirm": True},
        )
        assert blocked.status_code == 409, blocked.text
        with account["store"].transaction("same-topic-id") as tx:
            tx.progress.pending_question = None
            tx.abandon_active_interactions()
            tx.touch()
        refreshed = client.post(
            "/api/link/v1/topics/same-topic-id/question-repairs/previews",
            headers=headers(account),
            json={
                "request_id": str(uuid4()),
                "expected_revision": account["store"].load("same-topic-id").version,
                "question_id": first_id,
                "action": "void",
                "reason": "The original learner answer was copied from the prompt.",
            },
        )
        assert refreshed.status_code == 200, refreshed.text
        committed = client.post(
            "/api/link/v1/topics/same-topic-id/question-repairs/commits",
            headers=headers(account),
            json={"request_id": str(uuid4()), "preview_id": refreshed.json()["preview_id"], "confirm": True},
        )
        assert committed.status_code == 200, committed.text
        remaining = account["store"].load("same-topic-id").learning_evidence
        assert len(remaining) == 1
        assert remaining[0].question_id == f"codex-{second.json()['assessment_id']}"


def test_native_wrong_key_correction_and_interrupted_commit_replays(http_host):
    app, (account, _) = http_host
    source = _make_pdf_source(account)
    with TestClient(app) as client:
        _prepare(client, account, source)
        _seed_native_question(account, "native-wrong-key")
        commit, replay = _repair_native_question(
            client, account, source, "native-wrong-key", "correct"
        )
        assert commit["objective_id"] == "original-kp"
        assert replay["audit"]["expected_answer"] == "4"
        progress = account["store"].load("same-topic-id")
        attempt = next(
            item for item in progress.quiz_attempts if item.question_id == "native-wrong-key"
        )
        assert attempt.user_answer == "4"
        assert attempt.is_correct is True
        assert progress.mastery_levels["original-kp"] > 0
        interaction = account["store"].get_interaction("same-topic-id", "native-wrong-key")
        assert interaction.question.expected_answer == "4"


def test_native_void_replays_after_receipt_window(http_host):
    app, (account, _) = http_host
    source = _make_pdf_source(account)
    with TestClient(app) as client:
        _prepare(client, account, source)
        _seed_native_question(account, "native-void")
        commit, replay = _repair_native_question(
            client, account, source, "native-void", "void"
        )
        assert commit["action"] == "void"
        assert replay["audit"]["question_id"] == "native-void"
        progress = account["store"].load("same-topic-id")
        attempt = next(
            item for item in progress.quiz_attempts if item.question_id == "native-void"
        )
        assert attempt.voided is True
        assert attempt.user_answer == "4"
