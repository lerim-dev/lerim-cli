"""Hybrid FTS5 + vector search index over memory files.

Provides BM25-ranked keyword search, embedding-based semantic search,
and hybrid retrieval (RRF fusion) for decision and learning memory files
on disk.  The .md files remain canonical; this module maintains a derived
SQLite index for fast queries.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter
import sqlite_vec


# ── helpers ──────────────────────────────────────────────────────────────

def _dict_row(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
	"""Convert SQLite row tuples into dictionary rows."""
	return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def _iso_now() -> str:
	"""Return current UTC datetime as ISO8601 text."""
	return datetime.now(timezone.utc).isoformat()


def _serialize_vec(vec: list[float]) -> bytes:
	"""Serialize a float vector to bytes for sqlite-vec storage."""
	return struct.pack(f"{len(vec)}f", *vec)


# Minimal English stopwords for find_similar term extraction.
_STOPWORDS = frozenset({
	"a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
	"of", "with", "by", "from", "is", "it", "as", "was", "be", "are",
	"been", "being", "have", "has", "had", "do", "does", "did", "will",
	"would", "could", "should", "may", "might", "shall", "can", "not",
	"no", "so", "if", "then", "than", "that", "this", "these", "those",
	"we", "they", "he", "she", "you", "i", "me", "my", "your", "its",
	"our", "their", "what", "which", "who", "when", "where", "how",
	"all", "each", "every", "both", "few", "more", "most", "other",
	"some", "such", "only", "own", "same", "also", "just", "about",
	"up", "out", "into", "over", "after", "before", "between", "under",
	"again", "further", "once", "here", "there", "any", "very", "too",
})

_WORD_RE = re.compile(r"[a-zA-Z]{3,}")


def _extract_terms(text: str, max_terms: int = 10) -> list[str]:
	"""Extract top content words from text, skipping stopwords."""
	words = _WORD_RE.findall(text.lower())
	seen: set[str] = set()
	terms: list[str] = []
	for w in words:
		if w in _STOPWORDS or w in seen:
			continue
		seen.add(w)
		terms.append(w)
		if len(terms) >= max_terms:
			break
	return terms


def _normalize_similarity(value: float) -> float:
	"""Clamp a similarity-like value into the 0.0-1.0 range."""
	return max(0.0, min(1.0, float(value)))


def _cosine_similarity_from_distance(distance: float | None) -> float | None:
	"""Convert sqlite-vec cosine distance into normalized similarity."""
	if distance is None:
		return None
	try:
		return _normalize_similarity(1.0 - float(distance))
	except (TypeError, ValueError):
		return None


def _term_set(text: str) -> set[str]:
	"""Return normalized content terms for overlap scoring."""
	return set(_extract_terms(text, max_terms=32))


def _token_overlap_similarity(left: str, right: str) -> float:
	"""Cheap lexical similarity using Jaccard overlap over content terms."""
	left_terms = _term_set(left)
	right_terms = _term_set(right)
	if not left_terms or not right_terms:
		return 0.0
	union = left_terms | right_terms
	if not union:
		return 0.0
	return _normalize_similarity(len(left_terms & right_terms) / len(union))


# ── embedding helpers ────────────────────────────────────────────────────

_EMBED_MODEL = None


def _get_embed_model():
	"""Lazy-load the fastembed model singleton (BAAI/bge-small-en-v1.5, 384 dims)."""
	global _EMBED_MODEL
	if _EMBED_MODEL is None:
		from fastembed import TextEmbedding
		_EMBED_MODEL = TextEmbedding("BAAI/bge-small-en-v1.5")
	return _EMBED_MODEL


def _embed_texts(texts: list[str]) -> list[list[float]]:
	"""Embed a batch of texts. Returns list of 384-dim float vectors."""
	model = _get_embed_model()
	return [list(v) for v in model.embed(texts)]


# ── schema SQL ───────────────────────────────────────────────────────────

_CREATE_MEMORY_DOCS = """
CREATE TABLE IF NOT EXISTS memory_docs (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	memory_id TEXT UNIQUE NOT NULL,
	title TEXT NOT NULL,
	tags TEXT,
	body TEXT,
	primitive TEXT,
	kind TEXT,
	confidence REAL,
	file_path TEXT NOT NULL,
	content_hash TEXT,
	indexed_at TEXT NOT NULL
)
"""

_CREATE_MEMORY_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
	title, tags, body,
	content='memory_docs',
	content_rowid='id',
	tokenize='porter unicode61'
)
"""

_TRIGGER_AI = """
CREATE TRIGGER IF NOT EXISTS memory_docs_ai AFTER INSERT ON memory_docs BEGIN
	INSERT INTO memory_fts(rowid, title, tags, body)
	VALUES (new.id, new.title, new.tags, new.body);
END
"""

_TRIGGER_AD = """
CREATE TRIGGER IF NOT EXISTS memory_docs_ad AFTER DELETE ON memory_docs BEGIN
	INSERT INTO memory_fts(memory_fts, rowid, title, tags, body)
	VALUES ('delete', old.id, old.title, old.tags, old.body);
END
"""

_TRIGGER_AU = """
CREATE TRIGGER IF NOT EXISTS memory_docs_au AFTER UPDATE ON memory_docs BEGIN
	INSERT INTO memory_fts(memory_fts, rowid, title, tags, body)
	VALUES ('delete', old.id, old.title, old.tags, old.body);
	INSERT INTO memory_fts(rowid, title, tags, body)
	VALUES (new.id, new.title, new.tags, new.body);
END
"""


# ── main class ───────────────────────────────────────────────────────────

class MemoryIndex:
	"""Hybrid FTS5 + vector search index over memory markdown files."""

	def __init__(self, db_path: Path):
		"""Initialize with path to memories.sqlite3."""
		self._db_path = db_path
		self._initialized = False

	def _connect(self) -> sqlite3.Connection:
		"""Open SQLite connection with sqlite-vec loaded and dict row factory."""
		self._db_path.parent.mkdir(parents=True, exist_ok=True)
		conn = sqlite3.connect(self._db_path)
		conn.enable_load_extension(True)
		sqlite_vec.load(conn)
		conn.enable_load_extension(False)
		conn.row_factory = _dict_row
		return conn

	def _ensure_initialized(self) -> None:
		"""Create schema once per instance lifetime."""
		if self._initialized and self._db_path.exists():
			return
		self.ensure_schema()

	def ensure_schema(self) -> None:
		"""Create FTS5 tables, vec0 vector table, and sync triggers if they don't exist."""
		with self._connect() as conn:
			conn.execute("PRAGMA journal_mode=WAL;")
			conn.execute(_CREATE_MEMORY_DOCS)
			conn.execute(_CREATE_MEMORY_FTS)
			conn.execute(_TRIGGER_AI)
			conn.execute(_TRIGGER_AD)
			conn.execute(_TRIGGER_AU)
			conn.execute("""
				CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(
					memory_id TEXT PRIMARY KEY,
					embedding float[384] distance_metric=cosine
				)
			""")
			conn.execute("""
				CREATE TABLE IF NOT EXISTS memory_edges (
					source_id TEXT NOT NULL,
					target_id TEXT NOT NULL,
					edge_type TEXT NOT NULL,
					weight REAL DEFAULT 1.0,
					PRIMARY KEY (source_id, target_id, edge_type)
				)
			""")
		self._initialized = True

	# ── indexing ─────────────────────────────────────────────────────────

	def index_file(self, file_path: Path) -> bool:
		"""Parse a memory .md file and upsert into FTS + vector index.

		Returns True if indexed (new or changed), False if unchanged.
		"""
		self._ensure_initialized()

		content = file_path.read_text(encoding="utf-8")
		content_hash = hashlib.md5(content.encode()).hexdigest()

		post = frontmatter.loads(content)
		meta = post.metadata

		memory_id = str(meta.get("id", file_path.stem))
		title = str(meta.get("title", ""))
		raw_tags = meta.get("tags", [])
		tags = ", ".join(str(t) for t in raw_tags) if isinstance(raw_tags, list) else str(raw_tags or "")
		body = post.content
		primitive = str(meta.get("primitive", ""))
		kind = str(meta.get("kind", "")) or None
		confidence = meta.get("confidence")
		if confidence is not None:
			try:
				confidence = float(confidence)
			except (TypeError, ValueError):
				confidence = None

		# Infer primitive from file path if not in frontmatter.
		if not primitive:
			path_str = str(file_path)
			if "/decisions/" in path_str:
				primitive = "decision"
			elif "/learnings/" in path_str:
				primitive = "learning"

		with self._connect() as conn:
			# Check existing hash for incremental skip.
			existing = conn.execute(
				"SELECT content_hash FROM memory_docs WHERE memory_id = ?",
				(memory_id,),
			).fetchone()
			if existing and existing["content_hash"] == content_hash:
				return False

			# Upsert: delete + insert so triggers fire correctly.
			conn.execute("DELETE FROM memory_docs WHERE memory_id = ?", (memory_id,))
			conn.execute(
				"""
				INSERT INTO memory_docs
					(memory_id, title, tags, body, primitive, kind, confidence,
					 file_path, content_hash, indexed_at)
				VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
				""",
				(
					memory_id, title, tags, body, primitive, kind, confidence,
					str(file_path), content_hash, _iso_now(),
				),
			)

			# Compute embedding and upsert into vec_memories.
			embed_text = f"{title}\n{tags}\n{body[:500]}"
			embedding = _embed_texts([embed_text])[0]
			conn.execute("DELETE FROM vec_memories WHERE memory_id = ?", (memory_id,))
			conn.execute(
				"INSERT INTO vec_memories(memory_id, embedding) VALUES (?, ?)",
				(memory_id, _serialize_vec(embedding)),
			)
		return True

	def remove_file(self, file_path: Path) -> bool:
		"""Remove a memory from FTS and vector index by file path.

		Returns True if a row was removed, False if nothing matched.
		"""
		self._ensure_initialized()
		with self._connect() as conn:
			# Fetch memory_id before deleting so we can clean vec_memories too.
			row = conn.execute(
				"SELECT memory_id FROM memory_docs WHERE file_path = ?",
				(str(file_path),),
			).fetchone()
			if not row:
				return False
			conn.execute("DELETE FROM memory_docs WHERE file_path = ?", (str(file_path),))
			conn.execute("DELETE FROM vec_memories WHERE memory_id = ?", (row["memory_id"],))
			return True

	def reindex_directory(self, memory_root: Path) -> dict[str, int]:
		"""Full reindex of decisions/ and learnings/ directories.

		Returns {"indexed": N, "unchanged": N, "removed": N}.
		"""
		self._ensure_initialized()
		stats = {"indexed": 0, "unchanged": 0, "removed": 0}
		seen_paths: set[str] = set()

		for subdir in ("decisions", "learnings"):
			folder = memory_root / subdir
			if not folder.is_dir():
				continue
			for md_file in sorted(folder.glob("*.md")):
				seen_paths.add(str(md_file))
				changed = self.index_file(md_file)
				if changed:
					stats["indexed"] += 1
				else:
					stats["unchanged"] += 1

		# Remove stale entries whose files no longer exist on disk.
		with self._connect() as conn:
			rows = conn.execute("SELECT file_path, memory_id FROM memory_docs").fetchall()
			for row in rows:
				if row["file_path"] not in seen_paths:
					conn.execute(
						"DELETE FROM memory_docs WHERE file_path = ?",
						(row["file_path"],),
					)
					conn.execute(
						"DELETE FROM vec_memories WHERE memory_id = ?",
						(row["memory_id"],),
					)
					stats["removed"] += 1

		self._build_tag_edges()
		return stats

	def reindex_embeddings(self, memory_root: Path) -> int:
		"""Compute embeddings for all indexed memories that don't have them yet.

		Returns the number of memories that were newly embedded.
		"""
		self._ensure_initialized()
		with self._connect() as conn:
			rows = conn.execute("""
				SELECT memory_id, title, tags, body FROM memory_docs
				WHERE memory_id NOT IN (SELECT memory_id FROM vec_memories)
			""").fetchall()
			if not rows:
				return 0

			texts = [f"{r['title']}\n{r['tags'] or ''}\n{(r['body'] or '')[:500]}" for r in rows]
			embeddings = _embed_texts(texts)

			for row, emb in zip(rows, embeddings):
				conn.execute(
					"INSERT INTO vec_memories(memory_id, embedding) VALUES (?, ?)",
					(row["memory_id"], _serialize_vec(emb)),
				)
		return len(rows)

	# ── tag graph ────────────────────────────────────────────────────────

	def _build_tag_edges(self) -> int:
		"""Build edges between memories that share tags.

		Weight = number of shared tags.
		Returns the number of edges created.
		"""
		self._ensure_initialized()
		with self._connect() as conn:
			conn.execute("DELETE FROM memory_edges WHERE edge_type = 'shared_tag'")
			rows = conn.execute(
				"SELECT memory_id, tags FROM memory_docs WHERE tags IS NOT NULL AND tags != ''"
			).fetchall()
			# Parse tags into sets per memory.
			parsed: list[tuple[str, set[str]]] = []
			for r in rows:
				tag_set = {t.strip().lower() for t in r["tags"].split(",") if t.strip()}
				if tag_set:
					parsed.append((r["memory_id"], tag_set))
			# Build edges for all pairs with overlap.
			edge_count = 0
			for i in range(len(parsed)):
				mid_a, tags_a = parsed[i]
				for j in range(i + 1, len(parsed)):
					mid_b, tags_b = parsed[j]
					overlap = len(tags_a & tags_b)
					if overlap > 0:
						conn.execute(
							"INSERT OR REPLACE INTO memory_edges (source_id, target_id, edge_type, weight) VALUES (?, ?, 'shared_tag', ?)",
							(mid_a, mid_b, float(overlap)),
						)
						conn.execute(
							"INSERT OR REPLACE INTO memory_edges (source_id, target_id, edge_type, weight) VALUES (?, ?, 'shared_tag', ?)",
							(mid_b, mid_a, float(overlap)),
						)
						edge_count += 1
		return edge_count

	def find_related(self, memory_id: str, hops: int = 2, limit: int = 10) -> list[dict[str, Any]]:
		"""Find memories related to memory_id within N hops through the tag graph."""
		self._ensure_initialized()
		with self._connect() as conn:
			rows = conn.execute("""
				WITH RECURSIVE related AS (
					SELECT target_id AS mid, 1 AS depth, weight
					FROM memory_edges WHERE source_id = ?
					UNION
					SELECT e.target_id, r.depth + 1, e.weight
					FROM memory_edges e JOIN related r ON e.source_id = r.mid
					WHERE r.depth < ?
				)
				SELECT DISTINCT r.mid, MIN(r.depth) as min_depth,
					   SUM(r.weight) as total_weight,
					   d.title, d.tags, d.confidence, d.primitive, d.kind, d.file_path
				FROM related r
				JOIN memory_docs d ON d.memory_id = r.mid
				WHERE r.mid != ?
				GROUP BY r.mid
				ORDER BY total_weight DESC
				LIMIT ?
			""", (memory_id, hops, memory_id, limit)).fetchall()
		return rows

	def find_clusters(self, min_cluster_size: int = 3) -> list[list[dict[str, Any]]]:
		"""Find clusters of related memories using connected components.

		Uses a simple union-find over shared_tag edges to group memories
		that are transitively connected through shared tags.
		Returns list of clusters (each a list of memory dicts), filtered
		to clusters with at least min_cluster_size members.
		"""
		self._ensure_initialized()
		with self._connect() as conn:
			edges = conn.execute(
				"SELECT DISTINCT source_id, target_id FROM memory_edges WHERE edge_type = 'shared_tag'"
			).fetchall()
			all_mems = conn.execute(
				"SELECT memory_id, title, tags, confidence, primitive, kind, file_path FROM memory_docs"
			).fetchall()
		# Build union-find.
		parent: dict[str, str] = {}
		for m in all_mems:
			parent[m["memory_id"]] = m["memory_id"]

		def find(x: str) -> str:
			while parent[x] != x:
				parent[x] = parent[parent[x]]
				x = parent[x]
			return x

		def union(a: str, b: str) -> None:
			ra, rb = find(a), find(b)
			if ra != rb:
				parent[ra] = rb

		for e in edges:
			if e["source_id"] in parent and e["target_id"] in parent:
				union(e["source_id"], e["target_id"])

		# Group by root.
		groups: dict[str, list[dict[str, Any]]] = {}
		meta_by_id = {m["memory_id"]: m for m in all_mems}
		for mid in parent:
			root = find(mid)
			groups.setdefault(root, []).append(meta_by_id[mid])

		return [g for g in groups.values() if len(g) >= min_cluster_size]

	# ── search ───────────────────────────────────────────────────────────

	def search(
		self,
		query: str,
		limit: int = 10,
		primitive: str | None = None,
	) -> list[dict[str, Any]]:
		"""BM25-ranked full-text search.

		Weights: title 10x, tags 5x, body 1x.
		Returns list of dicts with memory_id, title, tags, confidence,
		primitive, kind, file_path, score, snippet.
		"""
		self._ensure_initialized()
		if not query or not query.strip():
			return []

		sql = """
			SELECT
				d.memory_id,
				d.title,
				d.tags,
				d.body,
				d.confidence,
				d.primitive,
				d.kind,
				d.file_path,
				-bm25(memory_fts, 10.0, 5.0, 1.0) AS score,
				snippet(memory_fts, 2, '<b>', '</b>', '...', 32) AS snippet
			FROM memory_fts f
			JOIN memory_docs d ON d.id = f.rowid
			WHERE memory_fts MATCH ?
		"""
		params: list[Any] = [query]

		if primitive:
			sql += " AND d.primitive = ?"
			params.append(primitive)

		sql += " ORDER BY score DESC LIMIT ?"
		params.append(limit)

		with self._connect() as conn:
			return conn.execute(sql, params).fetchall()

	def _fts_search(self, title: str, body: str, tags: str = "", limit: int = 10) -> list[dict[str, Any]]:
		"""Keyword search via FTS5 using extracted terms."""
		text = f"{title} {tags} {body[:200]}"
		terms = _extract_terms(text, max_terms=10)
		if not terms:
			return []
		fts_query = " OR ".join(terms)
		return self.search(fts_query, limit=limit)

	def _vector_search(self, query_vec: list[float], limit: int = 10) -> list[dict[str, Any]]:
		"""Nearest-neighbor search over vec_memories, joined with memory_docs metadata."""
		with self._connect() as conn:
			# vec0 KNN queries require k=? in WHERE; JOIN in same query not supported.
			vec_rows = conn.execute(
				"SELECT memory_id, distance FROM vec_memories WHERE embedding MATCH ? AND k = ?",
				(_serialize_vec(query_vec), limit),
			).fetchall()
			if not vec_rows:
				return []
			# Fetch metadata for matched memory_ids.
			ids = [r["memory_id"] for r in vec_rows]
			placeholders = ", ".join("?" for _ in ids)
			meta_rows = conn.execute(
				f"""
				SELECT memory_id, title, tags, body, confidence, primitive, kind, file_path
				FROM memory_docs WHERE memory_id IN ({placeholders})
				""",
				ids,
			).fetchall()
			meta_by_id = {r["memory_id"]: r for r in meta_rows}
			# Merge distance into metadata, preserving vec distance order.
			results = []
			for vr in vec_rows:
				mid = vr["memory_id"]
				if mid in meta_by_id:
					row = dict(meta_by_id[mid])
					row["distance"] = vr["distance"]
					row["similarity"] = _cosine_similarity_from_distance(vr.get("distance"))
					results.append(row)
			return results

	def _has_vec_data(self) -> bool:
		"""Check whether vec_memories contains any rows."""
		with self._connect() as conn:
			row = conn.execute("SELECT count(*) AS cnt FROM vec_memories").fetchone()
			return row["cnt"] > 0

	def find_similar(
		self,
		title: str,
		body: str,
		tags: str = "",
		limit: int = 5,
	) -> list[dict[str, Any]]:
		"""Find memories similar to a candidate using hybrid FTS5 + vector search.

		Uses Reciprocal Rank Fusion (RRF) to merge keyword and semantic results.
		Falls back to FTS5-only when no embeddings are indexed yet.
		"""
		self._ensure_initialized()

		# If no vectors indexed, fall back to FTS5-only.
		if not self._has_vec_data():
			return self._fts_search(title, body, tags=tags, limit=limit)

		pool = limit * 2  # Fetch wider candidate pools for fusion.

		# 1. FTS5 keyword results.
		fts_results = self._fts_search(title, body, tags=tags, limit=pool)

		# 2. Vector semantic results.
		query_text = f"{title}\n{tags}\n{body[:500]}"
		query_vec = _embed_texts([query_text])[0]
		vec_results = self._vector_search(query_vec, limit=pool)

		# 3. Reciprocal Rank Fusion (k=60).
		k = 60
		rrf_scores: dict[str, float] = {}
		data: dict[str, dict[str, Any]] = {}
		candidate_text = f"{title}\n{tags}\n{body}"

		for rank, r in enumerate(fts_results):
			mid = r["memory_id"]
			rrf_scores[mid] = rrf_scores.get(mid, 0) + 1 / (k + rank)
			row = dict(r)
			existing_text = f"{row.get('title', '')}\n{row.get('tags', '')}\n{row.get('body', '')}"
			row["lexical_similarity"] = _token_overlap_similarity(candidate_text, existing_text)
			data.setdefault(mid, row)

		for rank, r in enumerate(vec_results):
			mid = r["memory_id"]
			rrf_scores[mid] = rrf_scores.get(mid, 0) + 1 / (k + rank)
			row = dict(r)
			existing_text = f"{row.get('title', '')}\n{row.get('tags', '')}\n{row.get('body', '')}"
			row["lexical_similarity"] = _token_overlap_similarity(candidate_text, existing_text)
			if mid in data:
				# Merge vector cosine similarity into the existing FTS row
				# so dual-hit results preserve both signals.
				data[mid]["similarity"] = row.get("similarity")
			else:
				data[mid] = row

		# Sort by fused score descending, return top-limit.
		ranked = sorted(rrf_scores, key=lambda mid: rrf_scores[mid], reverse=True)[:limit]
		results: list[dict[str, Any]] = []
		for mid in ranked:
			row = dict(data[mid])
			row["fused_score"] = round(rrf_scores[mid], 6)
			# Use the strongest similarity signal available.
			row["similarity"] = _normalize_similarity(max(
				float(row.get("similarity") or 0.0),
				float(row.get("lexical_similarity") or 0.0),
			))
			results.append(row)
		return results

	# ── scan ─────────────────────────────────────────────────────────────

	def scan_all(self, primitive: str | None = None) -> list[dict[str, Any]]:
		"""Return compact metadata for all indexed memories.

		Returns list of dicts with memory_id, title, tags, confidence,
		primitive, kind, file_path.
		"""
		self._ensure_initialized()
		sql = """
			SELECT memory_id, title, tags, confidence, primitive, kind, file_path
			FROM memory_docs
		"""
		params: list[Any] = []
		if primitive:
			sql += " WHERE primitive = ?"
			params.append(primitive)
		sql += " ORDER BY title"

		with self._connect() as conn:
			return conn.execute(sql, params).fetchall()
