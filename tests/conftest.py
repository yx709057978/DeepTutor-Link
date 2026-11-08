import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI

from deeptutor_link.server import build_router

# Set before any host import: its modules capture runtime roots at import time.
ISOLATED = tempfile.TemporaryDirectory(prefix="dtcodex-tests-")
os.environ["DEEPTUTOR_HOME"] = ISOLATED.name
source_value = os.environ.get("DEEPTUTOR_SOURCE")
SOURCE = Path(source_value).expanduser().resolve() if source_value else None
if SOURCE is not None and (SOURCE / "deeptutor/learning/storage.py").exists():
    sys.path.insert(0, str(SOURCE))
    inherited_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath_entries = [str(SOURCE), *inherited_pythonpath.split(os.pathsep)]
    os.environ["PYTHONPATH"] = os.pathsep.join(
        dict.fromkeys(entry for entry in pythonpath_entries if entry)
    )


@pytest.fixture
def http_host(monkeypatch):
    if SOURCE is None:
        raise RuntimeError(
            "Set DEEPTUTOR_SOURCE to scripts/prepare_test_host.py output before host tests."
        )
    from deeptutor.api.routers import auth as auth_router
    from deeptutor.api.routers.mastery_path import router as mastery_router
    from deeptutor.learning.models import (
        KnowledgePoint,
        LearningModule,
        LearningProgress,
        TopicMetadata,
    )
    from deeptutor.learning.storage import LearningStore
    from deeptutor.multi_user.context import user_from_token_payload
    from deeptutor.multi_user.paths import user_context
    from deeptutor.services import auth
    from deeptutor.services.workspace import get_content_workspace_service
    from deeptutor.services.workspace.activity import WorkspaceActivityMiddleware
    from deeptutor.services.workspace.context import workspace_context

    monkeypatch.setattr(
        auth, "AUTH_SECRET", "synthetic-only-test-secret-64-characters-xxxxxxxxxxxxxxxxxxxxxxxxx"
    )
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    accounts = []
    for label in ("alpha", "beta"):
        uid = f"test-{uuid4().hex}"
        payload = auth.TokenPayload(username=label, role="user", user_id=uid)
        user = user_from_token_payload(payload)
        with user_context(user):
            ws = get_content_workspace_service().create_workspace(f"Synthetic {label}")
            with workspace_context(ws["workspace_id"]):
                store = LearningStore()
                p = LearningProgress(
                    book_id="same-topic-id",
                    name=f"{label} original topic",
                    modules=[
                        LearningModule(
                            id="m",
                            name="Module",
                            order=0,
                            knowledge_points=[
                                KnowledgePoint(
                                    id="original-kp", module_id="m", name=label, type="concept"
                                )
                            ],
                        )
                    ],
                    knowledge_types={"original-kp": "concept"},
                )
                store.save(p)
                store.put_topic(TopicMetadata(path_id=p.book_id, goal=f"{label} goal"), [])
        accounts.append(
            {
                "uid": uid,
                "workspace": ws["workspace_id"],
                "user": user,
                "token": auth.create_token(label, "user", uid),
                "store": store,
            }
        )
    app = FastAPI()
    app.add_middleware(WorkspaceActivityMiddleware)
    app.include_router(mastery_router, prefix="/api/mastery-paths", dependencies=[Depends(auth_router.require_auth)])
    app.include_router(build_router())
    return app, accounts


@pytest.fixture
def host_store(tmp_path):
    from deeptutor.learning.models import (
        KnowledgePoint,
        LearnerProfile,
        LearningModule,
        LearningProgress,
        TopicMetadata,
        TopicSource,
    )
    from deeptutor.learning.storage import LearningStore

    store = LearningStore(root=tmp_path / "original-learning")
    p = LearningProgress(
        book_id="original-topic",
        name="Original topic",
        learner_profile=LearnerProfile(preferences="Chinese examples"),
        modules=[
            LearningModule(
                id="module-original",
                name="Original module",
                order=0,
                knowledge_points=[
                    KnowledgePoint(
                        id="concept-original",
                        name="Original concept",
                        type="concept",
                        module_id="module-original",
                    ),
                    KnowledgePoint(
                        id="memory-original",
                        name="Original memory",
                        type="memory",
                        module_id="module-original",
                    ),
                ],
            )
        ],
    )
    p.knowledge_types = {kp.id: kp.type for m in p.modules for kp in m.knowledge_points}
    store.save(p)
    store.put_topic(
        TopicMetadata(path_id=p.book_id, goal="Continue the original goal"),
        [TopicSource(id="goal-original", kind="goal", label="Original goal")],
    )
    return store


@pytest.fixture
def assessment(host_store):
    from deeptutor_link.models import Assessment

    return Assessment(
        assessment_id=str(uuid4()),
        expected_revision=host_store.load("original-topic").version,
        objective_id="concept-original",
        question="Explain this concept in your own words.",
        learner_answer="An actual synthetic learner explanation.",
        result="correct",
        confidence=0.9,
        feedback="The explanation demonstrates the original objective.",
    )
