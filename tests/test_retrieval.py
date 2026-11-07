import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deeptutor_link.adapter import BridgeError, retrieve
from deeptutor_link.models import Retrieval


@pytest.fixture
def kb_source(host_store, tmp_path):
    from deeptutor.learning.models import TopicSource

    topic = host_store.get_topic("original-topic")
    topic.sources.append(
        TopicSource(
            id="kb-original",
            kind="knowledge_base",
            source_id="account:kb:original-kb",
            label="Original materials",
        )
    )
    host_store.put_topic(topic.metadata, topic.sources)
    return host_store, tmp_path / "knowledge_bases"


async def test_existing_persisted_index_is_read_without_rebuild(kb_source, monkeypatch):
    pytest.importorskip("llama_index.core")
    from deeptutor.services.rag import embedding_binding, factory
    from deeptutor.services.rag.pipelines.llamaindex import embedding_adapter, retrievers, storage
    from deeptutor.services.rag.pipelines.llamaindex.config import RetrievalConfig
    from deeptutor.services.rag.pipelines.llamaindex.pipeline import LlamaIndexPipeline
    from deeptutor.services.rag.query_rewrite import maybe_rewrite
    from llama_index.core import Settings, VectorStoreIndex
    from llama_index.core.embeddings import MockEmbedding
    from llama_index.core.schema import TextNode

    store, base = kb_source
    kb = base / "original-kb"
    index_dir = kb / "llamaindex_storage"
    index_dir.mkdir(parents=True)
    (base / "kb_config.json").write_text(
        json.dumps({"knowledge_bases": {"original-kb": {"rag_provider": "llamaindex"}}})
    )
    embedding = MockEmbedding(embed_dim=8)
    monkeypatch.setattr(Settings, "_embed_model", embedding)
    nodes = [
        TextNode(
            id_="existing-node",
            text="Existing indexed content about Bayes theorem.",
            metadata={
                "file_name": "original.txt",
                "document_key": "original.txt",
                "page_label": "12",
            },
        )
    ]
    VectorStoreIndex(nodes, embed_model=embedding).storage_context.persist(
        persist_dir=str(index_dir)
    )
    original_files = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in index_dir.iterdir()
    }
    pipeline = LlamaIndexPipeline(kb_base_dir=str(base), signature_provider=lambda: None)
    monkeypatch.setattr(pipeline, "_configure_settings", lambda: None)
    monkeypatch.setattr(embedding_adapter, "current_embedding", lambda: embedding)
    monkeypatch.setattr(
        retrievers, "retrieval_config_from_settings", lambda: RetrievalConfig(profile="vector")
    )
    monkeypatch.setattr(embedding_binding, "migrate_binding", lambda *a, **k: False)
    monkeypatch.setattr(embedding_binding, "default_selection", lambda: None)
    monkeypatch.setattr(factory, "_PIPELINE_CACHE", {(str(base), "llamaindex"): pipeline})
    # Only authorization resolver / embedding provider are synthetic. The host RAGService,
    # pipeline, persisted index loader, vector retrieval and source projection run for real.
    monkeypatch.setattr(
        "deeptutor.multi_user.knowledge_access.resolve_for_rag",
        lambda ref: SimpleNamespace(base_dir=base, name="original-kb"),
    )
    monkeypatch.setattr(
        pipeline, "initialize", AsyncMock(side_effect=AssertionError("must not rebuild"))
    )
    rewrite_flags = []

    async def observed_rewrite(query, **kwargs):
        rewrite_flags.append(kwargs.get("rewrite"))
        return await maybe_rewrite(query, **kwargs)

    monkeypatch.setattr("deeptutor.services.rag.query_rewrite.maybe_rewrite", observed_rewrite)
    storage.clear_index_cache()
    result = await retrieve(
        store,
        "original-topic",
        Retrieval(objective_id="concept-original", source_id="kb-original", query="Bayes theorem"),
        "original-workspace",
    )
    assert "Bayes theorem" in result["content"]
    assert result["chat_model_used"] is False
    assert result["references"][0]["chunk_id"] == "existing-node"
    assert result["references"][0]["document_key"] == "original.txt"
    assert rewrite_flags == [False]
    assert original_files == {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in index_dir.iterdir()
    }
    assert len(store.load("original-topic").learning_evidence) == 0


async def test_unsupported_provider_never_invokes_host_chat(kb_source, monkeypatch):
    from deeptutor.services.rag.service import RAGService

    store, base = kb_source
    monkeypatch.setattr(
        "deeptutor.multi_user.knowledge_access.resolve_for_rag",
        lambda ref: SimpleNamespace(base_dir=base, name="original-kb"),
    )
    monkeypatch.setattr(
        "deeptutor.services.rag.provider_binding.resolve_bound_provider", lambda *a: "lightrag"
    )
    search = AsyncMock(side_effect=AssertionError("no search call allowed"))
    monkeypatch.setattr(RAGService, "search", search)
    with pytest.raises(BridgeError) as exc:
        await retrieve(
            store,
            "original-topic",
            Retrieval(objective_id="concept-original", source_id="kb-original", query="query"),
            "",
        )
    assert exc.value.code == "provider_unsupported"
    search.assert_not_called()


async def test_retrieval_requires_selected_source_and_propagates_denial(kb_source, monkeypatch):
    from deeptutor.core.errors import AccessError

    store, _ = kb_source
    with pytest.raises(BridgeError) as exc:
        await retrieve(
            store,
            "original-topic",
            Retrieval(objective_id="concept-original", source_id="other-source", query="query"),
            "",
        )
    assert exc.value.code == "source_not_selected"

    def deny(ref):
        raise AccessError(403, "revoked")

    monkeypatch.setattr("deeptutor.multi_user.knowledge_access.resolve_for_rag", deny)
    with pytest.raises(AccessError):
        await retrieve(
            store,
            "original-topic",
            Retrieval(objective_id="concept-original", source_id="kb-original", query="query"),
            "",
        )


async def test_unhealthy_index_fails_closed(kb_source, monkeypatch):
    from deeptutor.services.rag.service import RAGService

    store, base = kb_source
    monkeypatch.setattr(
        "deeptutor.multi_user.knowledge_access.resolve_for_rag",
        lambda ref: SimpleNamespace(base_dir=base, name="original-kb"),
    )
    monkeypatch.setattr(RAGService, "search", AsyncMock(return_value={"needs_reindex": True}))
    with pytest.raises(BridgeError) as exc:
        await retrieve(
            store,
            "original-topic",
            Retrieval(objective_id="concept-original", source_id="kb-original", query="query"),
            "",
        )
    assert exc.value.code == "retrieval_unavailable"
