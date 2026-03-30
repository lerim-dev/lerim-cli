"""Tests for MemoryIndex hybrid FTS5 + vector search over memory files."""

from __future__ import annotations

from pathlib import Path

import pytest

from lerim.memory.memory_index import MemoryIndex


def _write_memory(path: Path, *, memory_id: str, title: str, body: str,
				   primitive: str = "learning", kind: str | None = None,
				   confidence: float = 0.8, tags: list[str] | None = None) -> Path:
	"""Write a minimal memory markdown file with YAML frontmatter."""
	tags = tags or []
	tag_lines = "\n".join(f"- {t}" for t in tags)
	tag_block = f"tags:\n{tag_lines}" if tags else "tags: []"
	kind_line = f"kind: {kind}\n" if kind else ""

	content = f"""---
id: {memory_id}
title: {title}
{tag_block}
confidence: {confidence}
{kind_line}created: '2026-03-27T00:00:00+00:00'
updated: '2026-03-27T00:00:00+00:00'
source: test-run
---

{body}
"""
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(content, encoding="utf-8")
	return path


@pytest.fixture()
def index(tmp_path: Path) -> MemoryIndex:
	"""Create a fresh MemoryIndex backed by a temp database."""
	db = tmp_path / "index" / "memories.sqlite3"
	idx = MemoryIndex(db)
	idx.ensure_schema()
	return idx


@pytest.fixture()
def memory_root(tmp_path: Path) -> Path:
	"""Return a temp memory root with decisions/ and learnings/ dirs."""
	root = tmp_path / "memory"
	(root / "decisions").mkdir(parents=True)
	(root / "learnings").mkdir(parents=True)
	return root


# ── test_index_file ──────────────────────────────────────────────────────

def test_index_file(index: MemoryIndex, tmp_path: Path) -> None:
	"""Index a memory file and verify it appears in the database."""
	f = _write_memory(
		tmp_path / "memory" / "decisions" / "20260327-use-postgres.md",
		memory_id="use-postgres",
		title="Use PostgreSQL for persistence",
		body="We chose PostgreSQL over SQLite for production workloads.",
		primitive="decision",
		tags=["database", "infrastructure"],
	)
	result = index.index_file(f)
	assert result is True

	rows = index.scan_all()
	assert len(rows) == 1
	assert rows[0]["memory_id"] == "use-postgres"
	assert rows[0]["title"] == "Use PostgreSQL for persistence"
	assert rows[0]["primitive"] == "decision"
	assert "database" in rows[0]["tags"]


# ── test_search ──────────────────────────────────────────────────────────

def test_search(index: MemoryIndex, memory_root: Path) -> None:
	"""Index 5 files, search by keyword, verify ranked results."""
	files = [
		("decisions", "deploy-strategy", "Blue-green deployment strategy",
		 "Use blue-green deploys for zero-downtime releases.",
		 ["deployment", "infrastructure"]),
		("learnings", "pytest-fixtures", "Pytest fixtures are powerful",
		 "Learned that pytest fixtures reduce test boilerplate significantly.",
		 ["testing", "python"]),
		("learnings", "docker-layers", "Docker layer caching",
		 "Docker builds are faster when layers are ordered by change frequency.",
		 ["docker", "performance"]),
		("decisions", "use-fastapi", "Use FastAPI for REST endpoints",
		 "FastAPI chosen for async support and automatic OpenAPI docs.",
		 ["api", "python"]),
		("learnings", "sqlite-fts5", "SQLite FTS5 for search",
		 "FTS5 provides efficient full-text search with BM25 ranking.",
		 ["search", "database"]),
	]
	for subdir, mid, title, body, tags in files:
		_write_memory(
			memory_root / subdir / f"20260327-{mid}.md",
			memory_id=mid,
			title=title,
			body=body,
			primitive="decision" if subdir == "decisions" else "learning",
			tags=tags,
		)
		index.index_file(memory_root / subdir / f"20260327-{mid}.md")

	# Search for "deployment" should rank the deploy-strategy file highest.
	results = index.search("deployment")
	assert len(results) >= 1
	assert results[0]["memory_id"] == "deploy-strategy"
	assert results[0]["score"] > 0

	# Search with primitive filter.
	learning_results = index.search("python", primitive="learning")
	for r in learning_results:
		assert r["primitive"] == "learning"

	# Empty query returns nothing.
	assert index.search("") == []


# ── test_find_similar ────────────────────────────────────────────────────

def test_find_similar(index: MemoryIndex, memory_root: Path) -> None:
	"""find_similar for a 'deploy' candidate should rank deployment higher."""
	# Index a deployment-related memory and a testing-related memory.
	_write_memory(
		memory_root / "decisions" / "20260327-deploy-k8s.md",
		memory_id="deploy-k8s",
		title="Kubernetes deployment pipeline",
		body="Deploy services to Kubernetes using Helm charts and ArgoCD.",
		primitive="decision",
		tags=["deployment", "kubernetes"],
	)
	index.index_file(memory_root / "decisions" / "20260327-deploy-k8s.md")

	_write_memory(
		memory_root / "learnings" / "20260327-unit-testing.md",
		memory_id="unit-testing",
		title="Unit testing best practices",
		body="Write focused tests that cover edge cases and boundaries.",
		primitive="learning",
		tags=["testing", "quality"],
	)
	index.index_file(memory_root / "learnings" / "20260327-unit-testing.md")

	# find_similar with a deployment-flavored candidate.
	results = index.find_similar(
		title="Container deployment automation",
		body="Automate the deployment of containers to production clusters.",
		tags="deployment, containers",
	)
	assert len(results) >= 1
	assert results[0]["memory_id"] == "deploy-k8s"
	assert "fused_score" in results[0]
	assert "similarity" in results[0]
	assert "lexical_similarity" in results[0]


# ── test_incremental_skip ────────────────────────────────────────────────

def test_incremental_skip(index: MemoryIndex, tmp_path: Path) -> None:
	"""Index same file twice; second call returns False (unchanged)."""
	f = _write_memory(
		tmp_path / "memory" / "learnings" / "20260327-caching.md",
		memory_id="caching-insight",
		title="Redis caching patterns",
		body="Cache-aside pattern works well for read-heavy workloads.",
		primitive="learning",
		tags=["caching", "redis"],
	)

	first = index.index_file(f)
	assert first is True

	second = index.index_file(f)
	assert second is False

	# Modify the file and re-index: should return True.
	content = f.read_text(encoding="utf-8")
	f.write_text(content + "\nAdditional insight about TTL strategies.", encoding="utf-8")

	third = index.index_file(f)
	assert third is True


# ── test_reindex_directory ───────────────────────────────────────────────

def test_reindex_directory(index: MemoryIndex, memory_root: Path) -> None:
	"""Reindex a temp dir with 3 files, verify counts."""
	_write_memory(
		memory_root / "decisions" / "20260327-use-grpc.md",
		memory_id="use-grpc",
		title="Use gRPC for inter-service communication",
		body="gRPC chosen for type safety and performance.",
		primitive="decision",
		tags=["grpc", "api"],
	)
	_write_memory(
		memory_root / "learnings" / "20260327-logging.md",
		memory_id="structured-logging",
		title="Structured logging is essential",
		body="JSON logs with correlation IDs enable distributed tracing.",
		primitive="learning",
		tags=["logging", "observability"],
	)
	_write_memory(
		memory_root / "learnings" / "20260327-retries.md",
		memory_id="retry-backoff",
		title="Exponential backoff for retries",
		body="Use jitter with exponential backoff to avoid thundering herd.",
		primitive="learning",
		tags=["reliability", "patterns"],
	)

	stats = index.reindex_directory(memory_root)
	assert stats["indexed"] == 3
	assert stats["unchanged"] == 0
	assert stats["removed"] == 0

	# Reindex again: all unchanged.
	stats2 = index.reindex_directory(memory_root)
	assert stats2["indexed"] == 0
	assert stats2["unchanged"] == 3
	assert stats2["removed"] == 0

	# Delete a file and reindex: removed should be 1.
	(memory_root / "learnings" / "20260327-retries.md").unlink()
	stats3 = index.reindex_directory(memory_root)
	assert stats3["indexed"] == 0
	assert stats3["unchanged"] == 2
	assert stats3["removed"] == 1


# ── test_scan_all ────────────────────────────────────────────────────────

def test_scan_all(index: MemoryIndex, memory_root: Path) -> None:
	"""scan_all returns all indexed memories with correct metadata."""
	_write_memory(
		memory_root / "decisions" / "20260327-api-versioning.md",
		memory_id="api-versioning",
		title="URL-based API versioning",
		body="Use /v1/, /v2/ prefixes for API versioning.",
		primitive="decision",
		confidence=0.9,
		tags=["api", "versioning"],
	)
	_write_memory(
		memory_root / "learnings" / "20260327-typing.md",
		memory_id="strict-typing",
		title="Strict typing catches bugs early",
		body="Type annotations with mypy found several latent bugs.",
		primitive="learning",
		kind="insight",
		confidence=0.85,
		tags=["python", "typing"],
	)
	index.index_file(memory_root / "decisions" / "20260327-api-versioning.md")
	index.index_file(memory_root / "learnings" / "20260327-typing.md")

	# Scan all.
	all_rows = index.scan_all()
	assert len(all_rows) == 2
	ids = {r["memory_id"] for r in all_rows}
	assert ids == {"api-versioning", "strict-typing"}

	# Scan filtered by primitive.
	decisions = index.scan_all(primitive="decision")
	assert len(decisions) == 1
	assert decisions[0]["memory_id"] == "api-versioning"

	learnings = index.scan_all(primitive="learning")
	assert len(learnings) == 1
	assert learnings[0]["memory_id"] == "strict-typing"
	assert learnings[0]["kind"] == "insight"


# ── test_remove_file ─────────────────────────────────────────────────────

def test_remove_file(index: MemoryIndex, tmp_path: Path) -> None:
	"""remove_file removes a previously indexed file."""
	f = _write_memory(
		tmp_path / "memory" / "decisions" / "20260327-remove-me.md",
		memory_id="remove-me",
		title="Temporary decision",
		body="This will be removed.",
		primitive="decision",
	)
	index.index_file(f)
	assert len(index.scan_all()) == 1

	removed = index.remove_file(f)
	assert removed is True
	assert len(index.scan_all()) == 0

	# Removing again returns False.
	removed2 = index.remove_file(f)
	assert removed2 is False


# ── test_vector_embedding_stored ────────────────────────────────────────

def test_vector_embedding_stored(index: MemoryIndex, tmp_path: Path) -> None:
	"""index_file stores an embedding in vec_memories alongside the FTS entry."""
	import sqlite3
	import sqlite_vec

	f = _write_memory(
		tmp_path / "memory" / "decisions" / "20260327-vec-test.md",
		memory_id="vec-test",
		title="Vector storage test",
		body="This memory should have an embedding in vec_memories.",
		primitive="decision",
		tags=["vector", "test"],
	)
	index.index_file(f)

	# Directly query vec_memories count.
	conn = sqlite3.connect(index._db_path)
	conn.enable_load_extension(True)
	sqlite_vec.load(conn)
	conn.enable_load_extension(False)
	row = conn.execute("SELECT count(*) AS cnt FROM vec_memories").fetchone()
	assert row[0] == 1
	conn.close()


# ── test_remove_file_cleans_vec ─────────────────────────────────────────

def test_remove_file_cleans_vec(index: MemoryIndex, tmp_path: Path) -> None:
	"""remove_file also removes the embedding from vec_memories."""
	import sqlite3
	import sqlite_vec

	f = _write_memory(
		tmp_path / "memory" / "decisions" / "20260327-vec-remove.md",
		memory_id="vec-remove",
		title="Will be removed",
		body="This embedding should be cleaned up on remove.",
		primitive="decision",
	)
	index.index_file(f)
	index.remove_file(f)

	conn = sqlite3.connect(index._db_path)
	conn.enable_load_extension(True)
	sqlite_vec.load(conn)
	conn.enable_load_extension(False)
	row = conn.execute("SELECT count(*) AS cnt FROM vec_memories").fetchone()
	assert row[0] == 0
	conn.close()


# ── test_hybrid_find_similar_semantic ───────────────────────────────────

def test_hybrid_find_similar_semantic(index: MemoryIndex, memory_root: Path) -> None:
	"""Hybrid search finds semantically related memories even without keyword overlap."""
	# "ML model training" and "neural network optimization" are semantically similar
	# but share few keywords.
	_write_memory(
		memory_root / "learnings" / "20260327-ml-training.md",
		memory_id="ml-training",
		title="Machine learning model training pipeline",
		body="Set up a training pipeline for deep learning models using PyTorch and GPU clusters.",
		primitive="learning",
		tags=["machine-learning", "training"],
	)
	index.index_file(memory_root / "learnings" / "20260327-ml-training.md")

	_write_memory(
		memory_root / "decisions" / "20260327-use-redis.md",
		memory_id="use-redis",
		title="Use Redis for caching",
		body="Redis chosen as the caching layer for low-latency key-value lookups.",
		primitive="decision",
		tags=["caching", "redis"],
	)
	index.index_file(memory_root / "decisions" / "20260327-use-redis.md")

	# Query with semantically related but keyword-different text.
	results = index.find_similar(
		title="Neural network optimization strategies",
		body="Optimize deep learning model performance with better hyperparameter tuning.",
		tags="deep-learning, optimization",
	)
	assert len(results) >= 1
	# The ML training memory should appear (semantically close).
	result_ids = [r["memory_id"] for r in results]
	assert "ml-training" in result_ids


# ── test_reindex_embeddings ─────────────────────────────────────────────

def test_reindex_embeddings(index: MemoryIndex, memory_root: Path) -> None:
	"""reindex_embeddings backfills vectors for memories missing from vec_memories."""
	import sqlite3
	import sqlite_vec

	# Index a file normally (creates both FTS and vec entry).
	_write_memory(
		memory_root / "learnings" / "20260327-already-embedded.md",
		memory_id="already-embedded",
		title="Already has embedding",
		body="This was indexed normally.",
		primitive="learning",
	)
	index.index_file(memory_root / "learnings" / "20260327-already-embedded.md")

	# Manually insert a memory_docs row without a vec_memories entry to simulate
	# a pre-vector memory.
	conn = sqlite3.connect(index._db_path)
	conn.enable_load_extension(True)
	sqlite_vec.load(conn)
	conn.enable_load_extension(False)
	conn.execute("""
		INSERT INTO memory_docs (memory_id, title, tags, body, primitive, file_path, content_hash, indexed_at)
		VALUES ('missing-vec', 'No embedding yet', 'test', 'Body text for embedding.', 'learning', '/fake/path.md', 'abc', '2026-03-27')
	""")
	conn.commit()
	pre_count = conn.execute("SELECT count(*) FROM vec_memories").fetchone()[0]
	assert pre_count == 1  # Only the normally indexed one.
	conn.close()

	# Reindex embeddings should backfill the missing one.
	count = index.reindex_embeddings(memory_root)
	assert count == 1

	# Now both should have embeddings.
	conn = sqlite3.connect(index._db_path)
	conn.enable_load_extension(True)
	sqlite_vec.load(conn)
	conn.enable_load_extension(False)
	post_count = conn.execute("SELECT count(*) FROM vec_memories").fetchone()[0]
	assert post_count == 2
	conn.close()

	# Running again should return 0 (nothing to backfill).
	assert index.reindex_embeddings(memory_root) == 0


# ── test_vector_search_returns_metadata ─────────────────────────────────

def test_vector_search_returns_metadata(index: MemoryIndex, memory_root: Path) -> None:
	"""_vector_search results include full metadata from memory_docs."""
	from lerim.memory.memory_index import _embed_texts

	_write_memory(
		memory_root / "decisions" / "20260327-meta-test.md",
		memory_id="meta-test",
		title="Metadata completeness test",
		body="Verifying that vector search results carry all metadata fields.",
		primitive="decision",
		kind="architectural",
		confidence=0.95,
		tags=["meta", "test"],
	)
	index.index_file(memory_root / "decisions" / "20260327-meta-test.md")

	query_vec = _embed_texts(["metadata completeness test"])[0]
	results = index._vector_search(query_vec, limit=5)
	assert len(results) == 1

	r = results[0]
	assert r["memory_id"] == "meta-test"
	assert r["title"] == "Metadata completeness test"
	assert r["primitive"] == "decision"
	assert r["kind"] == "architectural"
	assert r["confidence"] == 0.95
	assert "distance" in r
	assert "similarity" in r


# ── test_tag_edges_built ────────────────────────────────────────────────

def test_tag_edges_built(index: MemoryIndex, memory_root: Path) -> None:
	"""Index 3 files with overlapping tags, verify edges are created."""
	import sqlite3
	import sqlite_vec

	_write_memory(
		memory_root / "learnings" / "20260327-api-design.md",
		memory_id="api-design",
		title="REST API design patterns",
		body="Use consistent naming in REST endpoints.",
		primitive="learning",
		tags=["api", "design", "rest"],
	)
	_write_memory(
		memory_root / "decisions" / "20260327-api-versioning.md",
		memory_id="api-versioning",
		title="API versioning strategy",
		body="Use URL-based versioning for APIs.",
		primitive="decision",
		tags=["api", "versioning"],
	)
	_write_memory(
		memory_root / "learnings" / "20260327-caching-layer.md",
		memory_id="caching-layer",
		title="Caching at the API layer",
		body="Cache GET responses at the API gateway.",
		primitive="learning",
		tags=["api", "caching", "design"],
	)

	index.reindex_directory(memory_root)

	# Verify edges exist in the database.
	conn = sqlite3.connect(index._db_path)
	conn.enable_load_extension(True)
	sqlite_vec.load(conn)
	conn.enable_load_extension(False)
	edge_count = conn.execute(
		"SELECT count(*) FROM memory_edges WHERE edge_type = 'shared_tag'"
	).fetchone()[0]
	conn.close()

	# 3 pairs: (api-design, api-versioning), (api-design, caching-layer),
	# (api-versioning, caching-layer). Each pair creates 2 directional edges = 6 total.
	assert edge_count == 6


# ── test_find_related ───────────────────────────────────────────────────

def test_find_related(index: MemoryIndex, memory_root: Path) -> None:
	"""Verify 2-hop traversal finds connected memories."""
	# Chain: A --shared_tag--> B --shared_tag--> C
	# A shares "python" with B, B shares "testing" with C, but A and C share nothing directly.
	_write_memory(
		memory_root / "learnings" / "20260327-python-typing.md",
		memory_id="python-typing",
		title="Python type hints",
		body="Use type hints for better IDE support.",
		primitive="learning",
		tags=["python", "code-quality"],
	)
	_write_memory(
		memory_root / "learnings" / "20260327-python-testing.md",
		memory_id="python-testing",
		title="Python testing patterns",
		body="Use pytest for Python testing.",
		primitive="learning",
		tags=["python", "testing"],
	)
	_write_memory(
		memory_root / "learnings" / "20260327-integration-tests.md",
		memory_id="integration-tests",
		title="Integration testing strategy",
		body="Test at API boundaries for integration tests.",
		primitive="learning",
		tags=["testing", "integration"],
	)

	index.reindex_directory(memory_root)

	# From python-typing, hop 1 reaches python-testing (shared "python").
	# From python-testing, hop 2 reaches integration-tests (shared "testing").
	related = index.find_related("python-typing", hops=2, limit=10)
	related_ids = [r["mid"] for r in related]
	assert "python-testing" in related_ids
	assert "integration-tests" in related_ids

	# With hops=1, only direct neighbors.
	related_1hop = index.find_related("python-typing", hops=1, limit=10)
	related_1hop_ids = [r["mid"] for r in related_1hop]
	assert "python-testing" in related_1hop_ids
	# integration-tests is NOT directly connected to python-typing.
	assert "integration-tests" not in related_1hop_ids


# ── test_find_clusters ──────────────────────────────────────────────────

def test_find_clusters(index: MemoryIndex, memory_root: Path) -> None:
	"""Verify cluster detection groups related memories."""
	# Cluster 1: 3 memories sharing "deployment" tag
	_write_memory(
		memory_root / "decisions" / "20260327-deploy-k8s.md",
		memory_id="deploy-k8s",
		title="Kubernetes deployment",
		body="Deploy to Kubernetes.",
		primitive="decision",
		tags=["deployment", "kubernetes"],
	)
	_write_memory(
		memory_root / "decisions" / "20260327-deploy-strategy.md",
		memory_id="deploy-strategy",
		title="Blue-green deployment",
		body="Use blue-green deploys.",
		primitive="decision",
		tags=["deployment", "infrastructure"],
	)
	_write_memory(
		memory_root / "learnings" / "20260327-deploy-rollback.md",
		memory_id="deploy-rollback",
		title="Deployment rollback procedures",
		body="Always have a rollback plan.",
		primitive="learning",
		tags=["deployment", "reliability"],
	)

	# Isolated memory (no shared tags with the deployment cluster)
	_write_memory(
		memory_root / "learnings" / "20260327-regex-tips.md",
		memory_id="regex-tips",
		title="Regex optimization tips",
		body="Use non-greedy quantifiers.",
		primitive="learning",
		tags=["regex", "performance"],
	)

	index.reindex_directory(memory_root)

	clusters = index.find_clusters(min_cluster_size=3)
	# Should have exactly 1 cluster (the 3 deployment memories).
	assert len(clusters) >= 1
	# Find the deployment cluster.
	deploy_cluster = None
	for c in clusters:
		ids = {m["memory_id"] for m in c}
		if "deploy-k8s" in ids:
			deploy_cluster = c
			break
	assert deploy_cluster is not None
	deploy_ids = {m["memory_id"] for m in deploy_cluster}
	assert deploy_ids == {"deploy-k8s", "deploy-strategy", "deploy-rollback"}
	# regex-tips should NOT be in any cluster (isolated).
	all_clustered_ids = {m["memory_id"] for c in clusters for m in c}
	assert "regex-tips" not in all_clustered_ids
