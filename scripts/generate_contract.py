"""Maintainer tool: fingerprint reviewed host code; not used during installation."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

CONTRACT = [
    "__version__.py",
    "api/main.py",
    "api/routers/auth.py",
    "learning/storage.py",
    "learning/service.py",
    "learning/models.py",
    "learning/policy.py",
    "learning/mastery.py",
    "learning/scheduler.py",
    "learning/review_strategy.py",
    "learning/lesson_evidence.py",
    "learning/semantic_assessment.py",
    "learning/topic_generation.py",
    "learning/objective_relations.py",
    "learning/outline_revision.py",
    "api/routers/mastery_path.py",
    "services/session/turns/learning_adapter.py",
    "multi_user/context.py",
    "multi_user/paths.py",
    "multi_user/knowledge_access.py",
    "multi_user/learning_access.py",
    "services/workspace/context.py",
    "services/workspace/activity.py",
    "services/workspace/knowledge.py",
    "services/rag/service.py",
    "services/rag/embedding_binding.py",
    "services/rag/provider_binding.py",
    "services/rag/source_pages.py",
    "services/rag/pipelines/llamaindex/pipeline.py",
    "services/rag/pipelines/llamaindex/retrievers.py",
    "services/learning_journal/store.py",
]

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.check and args.force:
        parser.error("Use either --check or --force, not both.")
    value = {
        "version": "1.7.1",
        "commit": "f7aed0744c36c5afef16a4261c31ebee1208cac2",
        "sha256": {
            name: hashlib.sha256((args.host / name).read_bytes()).hexdigest() for name in CONTRACT
        },
    }
    rendered = json.dumps(value, indent=2) + chr(10)
    if args.check:
        current = args.output.read_text() if args.output.exists() else ""
        if current != rendered:
            print("Support fingerprint differs from the reviewed contract.", file=sys.stderr)
            return 1
        return 0
    if args.output.exists() and args.output.read_text() != rendered and not args.force:
        print("Support fingerprint drifted; inspect it and rerun with --force only after review.", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
