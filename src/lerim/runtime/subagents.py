"""Read-only PydanticAI explorer subagent for evidence gathering."""

from __future__ import annotations

from tempfile import TemporaryDirectory

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.test import TestModel

from lerim.runtime.contracts import ExplorerEnvelope
from lerim.runtime.providers import build_orchestration_model
from lerim.runtime.tools import (
    RuntimeToolContext,
    build_tool_context,
    glob_files_tool,
    grep_files_tool,
    read_file_tool,
)


def _build_explorer(model=None) -> Agent[RuntimeToolContext, ExplorerEnvelope]:
    """Build read-only explorer subagent with glob/read/grep tools."""
    agent = Agent[RuntimeToolContext, ExplorerEnvelope](
        model=model or build_orchestration_model("explorer"),
        output_type=ExplorerEnvelope,
        deps_type=RuntimeToolContext,
        name="lerim-explorer",
        instructions="""\
You are a read-only explorer for Lerim memories and workspace artifacts. \
Use glob/read/grep tools to gather evidence and return structured items.""",
        retries=1,
    )

    @agent.tool
    def glob(
        ctx: RunContext[RuntimeToolContext],
        pattern: str,
        base_path: str | None = None,
    ) -> list[str]:
        """Find files by glob pattern."""
        return glob_files_tool(context=ctx.deps, pattern=pattern, base_path=base_path)

    @agent.tool
    def read(
        ctx: RunContext[RuntimeToolContext],
        file_path: str,
        offset: int = 1,
        limit: int = 2000,
    ) -> str:
        """Read file content with line numbers."""
        return read_file_tool(
            context=ctx.deps,
            file_path=file_path,
            offset=offset,
            limit=limit,
        )

    @agent.tool
    def grep(
        ctx: RunContext[RuntimeToolContext],
        pattern: str,
        base_path: str | None = None,
        include: str = "*.md",
        max_hits: int = 200,
    ) -> list[str]:
        """Search files with regular expression."""
        return grep_files_tool(
            context=ctx.deps,
            pattern=pattern,
            base_path=base_path,
            include=include,
            max_hits=max_hits,
        )

    return agent


_explorer_singleton: Agent[RuntimeToolContext, ExplorerEnvelope] | None = None


def get_explorer_agent() -> Agent[RuntimeToolContext, ExplorerEnvelope]:
    """Return the module-level explorer singleton (lazy-init on first call)."""
    global _explorer_singleton
    if _explorer_singleton is None:
        _explorer_singleton = _build_explorer()
    return _explorer_singleton


if __name__ == "__main__":
    """Run read-only subagent construction smoke test."""
    with TemporaryDirectory() as tmp_dir:
        context = build_tool_context(repo_root=tmp_dir, run_folder=tmp_dir)
        explorer = _build_explorer(model=TestModel())

        explorer_tools = set(explorer._function_toolset.tools.keys())

        assert {"glob", "read", "grep"}.issubset(explorer_tools)
        assert "write" not in explorer_tools
        assert context.repo_root.exists()

    print("runtime subagents: self-test passed")
