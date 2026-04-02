"""Maintain agent: review, merge, archive, and consolidate memories.

memory store -> dspy.ReAct(MaintainSignature, tools) -> optimized memory store.
The ReAct agent loop and its internal predictors are optimizable by
MIPROv2, BootstrapFewShot, BootstrapFinetune, etc.
"""

from __future__ import annotations

import dspy

from lerim.agents.context import RuntimeContext
from lerim.agents.tools import make_maintain_tools


class MaintainSignature(dspy.Signature):
    """Run Lerim memory maintenance -- a reflective pass over memory files.

    Tool reference:
    - scan_memory_manifest() -- compact list of all memories (name, description, type, filename)
    - list_files(directory, pattern) / read_file(file_path) -- read files in detail
    - archive_memory(file_path) -- soft-delete to archived/
    - edit_memory(file_path, new_content) -- replace file content (must start with ---)
    - write_memory(type, name, description, body) -- create new memories
    - update_memory_index(content) -- write MEMORY.md index
    - write_report(file_path, content) -- write final JSON report

    ## Phase 1 -- Orient
    - Call scan_memory_manifest() to see all existing memories
    - Read MEMORY.md if it exists
    - Read recent session summaries for context (list_files on summaries/)

    ## Phase 2 -- Gather signal
    - Check summaries for topics in 3+ sessions with no corresponding memory
    - Look for memories that contradict current summaries
    - Note any memories that seem stale or outdated

    ## Phase 3 -- Consolidate
    - Merge near-duplicate memories (keep the richer version, archive the other)
    - Update memories with new information from summaries via edit_memory()
    - Archive memories that are: contradicted, trivially obvious, or content-free
    - Convert relative dates to absolute dates
    - When 3+ small memories cover same topic, combine into one via write_memory()

    ## Phase 4 -- Prune and index
    - Call update_memory_index() with a fresh index:
      One line per memory, format: `- [Name](filename.md) -- description`
      Max 200 lines / 25KB. Never put memory content in the index.
    - Remove pointers to archived/deleted memories
    - Add pointers to new/updated memories

    Write report to maintain_actions_path:
    {
      "run_id": "<name of run_folder>",
      "actions": [{"action": str, "source_path": str, "target_path": str, "reason": str}],
      "counts": {"merged": N, "archived": N, "consolidated": N, "unchanged": N}
    }

    Constraints:
    - ONLY read/write under memory_root and run_folder.
    - Summaries (memory_root/summaries/) are read-only.
    - Do NOT delete files. ALWAYS use archive_memory() for soft-delete.
    - When unsure whether to merge or archive, leave unchanged.

    Return one short plain-text completion line.
    """

    memory_root: str = dspy.InputField(
        desc="Absolute path to the memory root directory"
    )
    run_folder: str = dspy.InputField(
        desc="Absolute path to the run workspace folder"
    )
    maintain_actions_path: str = dspy.InputField(
        desc="Path where write_report writes the actions JSON"
    )
    memory_index_path: str = dspy.InputField(
        desc="Path to MEMORY.md index file"
    )
    completion_summary: str = dspy.OutputField(
        desc="Short plain-text completion summary"
    )


class MaintainAgent(dspy.Module):
    """DSPy ReAct module for the maintain flow. Independently optimizable."""

    def __init__(self, ctx: RuntimeContext):
        super().__init__()
        self.react = dspy.ReAct(
            MaintainSignature,
            tools=make_maintain_tools(ctx),
            max_iters=ctx.config.lead_role.max_iters_maintain,
        )

    def forward(
        self,
        memory_root: str,
        run_folder: str,
        maintain_actions_path: str,
        memory_index_path: str,
    ) -> dspy.Prediction:
        return self.react(
            memory_root=memory_root,
            run_folder=run_folder,
            maintain_actions_path=maintain_actions_path,
            memory_index_path=memory_index_path,
        )
