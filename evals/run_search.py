"""Run search relevance eval (NDCG@5) against golden datasets.

Pure deterministic evaluation -- no LLM judge needed. Indexes a golden
memory store, runs queries through MemoryIndex.find_similar(), and
computes NDCG@5 against known relevant memory IDs.

Usage: PYTHONPATH=. python evals/run_search.py \
  --golden-dir path/to/golden/search/
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from lerim.config.logging import logger

from evals.common import console, make_progress
from evals.scores import compute_ndcg


EVALS_DIR = Path(__file__).parent
RESULTS_DIR = EVALS_DIR / "results"


def _copy_memory_store(source: Path, dest: Path) -> None:
	"""Copy a golden memory_store directory into the eval temp memory root."""
	for subdir in ("decisions", "learnings"):
		src = source / subdir
		dst = dest / subdir
		if src.is_dir():
			shutil.copytree(src, dst, dirs_exist_ok=True)


def run_search_eval(
	golden_dir: Path,
	limit: int = 0,
	k: int = 5,
) -> dict:
	"""Run search relevance eval across golden cases and return results dict."""
	cases = sorted(
		d for d in golden_dir.iterdir()
		if d.is_dir() and (d / "input" / "queries.json").exists()
	)
	if limit and limit > 0:
		cases = cases[:limit]

	if not cases:
		logger.warning("No golden search cases found in {}", golden_dir)
		return {}

	per_case: list[dict] = []
	total_start = time.time()

	with make_progress() as progress:
		task = progress.add_task("Search eval", total=len(cases))

		for i, case_dir in enumerate(cases, 1):
			progress.update(task, description=f"[search] {case_dir.name}")
			t0 = time.time()

			# Set up temp dir with memory store and index
			temp_dir = Path(tempfile.mkdtemp(prefix="lerim_eval_search_"))
			memory_root = temp_dir / "memory"
			memory_root.mkdir()
			for subdir in ("decisions", "learnings"):
				(memory_root / subdir).mkdir()

			db_path = temp_dir / "index" / "memories.sqlite3"
			db_path.parent.mkdir(parents=True, exist_ok=True)

			try:
				# Copy golden memory store
				input_store = case_dir / "input" / "memory_store"
				if input_store.is_dir():
					_copy_memory_store(input_store, memory_root)

				# Load queries
				queries = json.loads(
					(case_dir / "input" / "queries.json").read_text(encoding="utf-8")
				)

				# Index memory store
				from lerim.memory.memory_index import MemoryIndex

				index = MemoryIndex(db_path)
				index.ensure_schema()
				reindex_stats = index.reindex_directory(memory_root)
				logger.info(
					"[{}/{}] Indexed {} memories",
					i, len(cases), reindex_stats.get("indexed", 0),
				)

				# Run queries and compute NDCG@k
				query_scores: list[dict] = []
				for q in queries:
					query_text = q["query"]
					relevant_ids = set(q["relevant_memory_ids"])

					results = index.find_similar(query_text, "", limit=k)
					ranked_ids = [r.get("memory_id", "") for r in results]
					ndcg = compute_ndcg(ranked_ids, relevant_ids, k=k)

					query_scores.append({
						"query": query_text,
						"ndcg": round(ndcg, 4),
						"relevant_count": len(relevant_ids),
						"returned_count": len(ranked_ids),
						"hits": [rid for rid in ranked_ids if rid in relevant_ids],
					})

				wall_time = time.time() - t0
				avg_ndcg = (
					sum(qs["ndcg"] for qs in query_scores) / len(query_scores)
					if query_scores else 0.0
				)

				logger.success(
					"[{}/{}] avg_ndcg@{}={:.3f} queries={} time={:.1f}s",
					i, len(cases), k, avg_ndcg, len(query_scores), wall_time,
				)

				per_case.append({
					"case": case_dir.name,
					"avg_ndcg": round(avg_ndcg, 4),
					"query_count": len(query_scores),
					"query_scores": query_scores,
					"wall_time_s": round(wall_time, 2),
				})

			finally:
				shutil.rmtree(temp_dir, ignore_errors=True)

			progress.advance(task)

	total_wall = time.time() - total_start

	# Aggregate
	n = len(per_case) or 1
	agg = {
		"avg_ndcg": round(
			sum(c.get("avg_ndcg", 0) for c in per_case) / n, 4
		),
		"total_queries": sum(c.get("query_count", 0) for c in per_case),
	}

	result = {
		"timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
		"pipeline": "search",
		"k": k,
		"performance": {
			"total_wall_time_s": round(total_wall, 2),
			"case_count": len(cases),
		},
		"scores": agg,
		"per_case": per_case,
	}

	RESULTS_DIR.mkdir(parents=True, exist_ok=True)
	ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
	out_path = RESULTS_DIR / f"search_{ts}.json"
	out_path.write_text(
		json.dumps(result, indent=2, ensure_ascii=False) + "\n",
		encoding="utf-8",
	)
	console.print(f"\nResults saved to: [bold]{out_path}[/]")
	console.print(
		f"  Avg NDCG@{k}: [bold]{agg['avg_ndcg']:.4f}[/]  "
		f"Queries: {agg['total_queries']}  "
		f"Time: {total_wall:.1f}s"
	)

	return result


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="Run search relevance eval (NDCG@k)")
	parser.add_argument(
		"--golden-dir",
		required=True,
		help="Path to golden dataset directory with case subdirectories",
	)
	parser.add_argument("--limit", type=int, default=0, help="Max cases (0=all)")
	parser.add_argument("--k", type=int, default=5, help="NDCG cutoff (default: 5)")
	args = parser.parse_args()
	run_search_eval(Path(args.golden_dir), limit=args.limit, k=args.k)
