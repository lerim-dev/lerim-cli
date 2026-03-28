"""Shared utilities for eval runners.

Contains the common eval config builder (deduplicated from all runners)
and rich-based display helpers for progress tracking and result tables.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

console = Console(stderr=True)


def configure_dspy_from_eval(config: dict, prefix: str = "lerim_eval_") -> tuple:
    """Build isolated eval config from an eval TOML dict.

    Validates required sections, builds role overrides, creates a temp dir
    with memory/index subdirs, and sets the config override.

    Returns (Config, temp_dir_path).
    """
    REQUIRED_SECTIONS = ("lead", "extraction", "summarization")
    missing = [s for s in REQUIRED_SECTIONS if s not in config]
    if missing:
        raise ValueError(
            f"Eval config missing required sections: {missing}. "
            f"All of {REQUIRED_SECTIONS} are required."
        )

    section_to_role = {
        "lead": "lead",
        "extraction": "extract",
        "summarization": "summarize",
    }
    roles_override = {
        role_name: config[section_name]
        for section_name, role_name in section_to_role.items()
    }

    temp_dir = Path(tempfile.mkdtemp(prefix=prefix))
    (temp_dir / "memory").mkdir()
    (temp_dir / "index").mkdir()

    from lerim.config.settings import build_eval_config, set_config_override

    eval_cfg = build_eval_config(roles_override, temp_dir)
    set_config_override(eval_cfg)
    return eval_cfg, temp_dir


def cleanup_eval(temp_dir: Path) -> None:
    """Reset config override and remove temp directory."""
    from lerim.config.settings import set_config_override

    set_config_override(None)
    shutil.rmtree(temp_dir, ignore_errors=True)


def make_progress() -> Progress:
    """Create a rich Progress bar configured for eval trace processing."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("[dim]|"),
        TimeElapsedColumn(),
        TextColumn("[dim]eta"),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )


def print_extraction_table(per_trace: list[dict], agg: dict) -> None:
    """Print rich table for extraction eval results."""
    table = Table(title="Extraction Results", show_lines=False)
    table.add_column("Trace", style="cyan", max_width=40)
    table.add_column("Schema", justify="center")
    table.add_column("Cands", justify="right")
    table.add_column("Compl", justify="right")
    table.add_column("Faith", justify="right")
    table.add_column("Clar", justify="right")
    table.add_column("Prec", justify="right")
    table.add_column("COMP", justify="right", style="bold")
    table.add_column("Time", justify="right")

    for t in per_trace:
        schema = "[green]ok[/]" if t["schema_ok"] else "[red]FAIL[/]"
        table.add_row(
            t["trace"],
            schema,
            str(t.get("candidate_count", 0)),
            f"{t['completeness']:.2f}",
            f"{t['faithfulness']:.2f}",
            f"{t['clarity']:.2f}",
            f"{t.get('precision', 0):.2f}",
            f"{t['composite']:.2f}",
            f"{t['wall_time_s']:.1f}s",
        )

    table.add_section()
    table.add_row(
        "[bold]AVERAGE[/]",
        f"{agg['schema_ok']:.2f}",
        "",
        f"{agg['completeness']:.2f}",
        f"{agg['faithfulness']:.2f}",
        f"{agg['clarity']:.2f}",
        f"{agg.get('precision', 0):.2f}",
        f"[bold]{agg['composite']:.2f}[/]",
        f"{agg['wall_time_s']:.1f}s",
        style="bold",
    )

    console.print(table)


def print_summarization_table(per_trace: list[dict], agg: dict) -> None:
    """Print rich table for summarization eval results."""
    table = Table(title="Summarization Results", show_lines=False)
    table.add_column("Trace", style="cyan", max_width=40)
    table.add_column("Fields", justify="center")
    table.add_column("Limit", justify="center")
    table.add_column("Compl", justify="right")
    table.add_column("Faith", justify="right")
    table.add_column("Clar", justify="right")
    table.add_column("COMP", justify="right", style="bold")
    table.add_column("Time", justify="right")

    for t in per_trace:
        fields = "[green]ok[/]" if t.get("fields_present") else "[red]FAIL[/]"
        limits = "[green]ok[/]" if t.get("word_limits") else "[red]FAIL[/]"
        table.add_row(
            t["trace"],
            fields,
            limits,
            f"{t['completeness']:.2f}",
            f"{t['faithfulness']:.2f}",
            f"{t['clarity']:.2f}",
            f"{t['composite']:.2f}",
            f"{t['wall_time_s']:.1f}s",
        )

    table.add_section()
    table.add_row(
        "[bold]AVERAGE[/]",
        f"{agg['fields_present']:.2f}",
        f"{agg['word_limits']:.2f}",
        f"{agg['completeness']:.2f}",
        f"{agg['faithfulness']:.2f}",
        f"{agg['clarity']:.2f}",
        f"[bold]{agg['composite']:.2f}[/]",
        f"{agg['wall_time_s']:.1f}s",
        style="bold",
    )

    console.print(table)


def print_lifecycle_table(
    sync_scores: list[dict],
    maintain_scores: list[dict],
    sync_composite: float,
    maintain_composite: float,
    overall_composite: float,
    total_wall: float,
) -> None:
    """Print rich tables for lifecycle eval results."""
    # Sync table
    sync_table = Table(title="Lifecycle Sync Results", show_lines=False)
    sync_table.add_column("Trace", style="cyan", max_width=35)
    sync_table.add_column("Mem", justify="right")
    sync_table.add_column("Add", justify="right")
    sync_table.add_column("Upd", justify="right")
    sync_table.add_column("Nop", justify="right")
    sync_table.add_column("COMP", justify="right", style="bold")
    sync_table.add_column("Time", justify="right")

    for s in sync_scores:
        c = s.get("counts", {})
        sync_table.add_row(
            s.get("trace", "?"),
            str(s.get("memory_count_before", 0)),
            str(c.get("add", 0)),
            str(c.get("update", 0)),
            str(c.get("no_op", 0)),
            f"{s.get('composite', 0):.2f}",
            f"{s.get('wall_time_s', 0):.0f}s",
        )

    console.print(sync_table)

    # Maintain table
    if maintain_scores:
        maint_table = Table(title="Lifecycle Maintain Results", show_lines=False)
        maint_table.add_column("After trace", style="cyan")
        maint_table.add_column("Before", justify="right")
        maint_table.add_column("After", justify="right")
        maint_table.add_column("Merged", justify="right")
        maint_table.add_column("Archived", justify="right")
        maint_table.add_column("COMP", justify="right", style="bold")
        maint_table.add_column("Time", justify="right")

        for m in maintain_scores:
            c = m.get("counts", {})
            maint_table.add_row(
                str(m.get("after_trace_index", "?")),
                str(m.get("memory_before", 0)),
                str(m.get("memory_after", 0)),
                str(c.get("merged", 0)),
                str(c.get("archived", 0)),
                f"{m.get('composite', 0):.2f}",
                f"{m.get('wall_time_s', 0):.0f}s",
            )

        console.print(maint_table)

    # Summary
    console.print()
    console.print(f"  Sync composite:     [bold]{sync_composite:.3f}[/]")
    console.print(f"  Maintain composite: [bold]{maintain_composite:.3f}[/]")
    console.print(f"  Overall composite:  [bold green]{overall_composite:.3f}[/]")
    console.print(f"  Total time:         {total_wall:.0f}s")


def print_compare_table(pipeline: str, runs: list[dict]) -> None:
    """Print rich comparison table for a pipeline across runs."""
    if pipeline == "lifecycle":
        table = Table(title=f"Comparison: {pipeline}", show_lines=False)
        table.add_column("Config", style="cyan", max_width=40)
        table.add_column("Sync", justify="right")
        table.add_column("Maint", justify="right")
        table.add_column("Overall", justify="right", style="bold")
        table.add_column("Time", justify="right")

        for run in runs:
            label = _model_label(run.get("config", {}))
            scores = run.get("scores", {})
            perf = run.get("performance", {})
            table.add_row(
                label,
                f"{scores.get('sync_composite', 0):.2f}",
                f"{scores.get('maintain_composite', 0):.2f}",
                f"{scores.get('overall_composite', 0):.2f}",
                f"{perf.get('total_wall_time_s', 0):.0f}s",
            )

    elif pipeline == "extraction":
        table = Table(title=f"Comparison: {pipeline}", show_lines=False)
        table.add_column("Config", style="cyan", max_width=40)
        table.add_column("Schema", justify="right")
        table.add_column("Compl", justify="right")
        table.add_column("Faith", justify="right")
        table.add_column("Clar", justify="right")
        table.add_column("COMP", justify="right", style="bold")
        table.add_column("Time/t", justify="right")

        for run in runs:
            label = _model_label(run.get("config", {}))
            scores = run.get("scores", {})
            perf = run.get("performance", {})
            table.add_row(
                label,
                f"{scores.get('schema_ok', 0):.2f}",
                f"{scores.get('completeness', 0):.2f}",
                f"{scores.get('faithfulness', 0):.2f}",
                f"{scores.get('clarity', 0):.2f}",
                f"{scores.get('composite', 0):.2f}",
                f"{perf.get('avg_time_per_trace_s', 0):.1f}s",
            )

    else:
        # Summarization or unknown
        table = Table(title=f"Comparison: {pipeline}", show_lines=False)
        table.add_column("Config", style="cyan", max_width=40)
        table.add_column("Fields", justify="right")
        table.add_column("Limits", justify="right")
        table.add_column("Compl", justify="right")
        table.add_column("Faith", justify="right")
        table.add_column("Clar", justify="right")
        table.add_column("COMP", justify="right", style="bold")
        table.add_column("Time/t", justify="right")

        for run in runs:
            label = _model_label(run.get("config", {}))
            scores = run.get("scores", {})
            perf = run.get("performance", {})
            table.add_row(
                label,
                f"{scores.get('fields_present', 0):.2f}",
                f"{scores.get('word_limits', 0):.2f}",
                f"{scores.get('completeness', 0):.2f}",
                f"{scores.get('faithfulness', 0):.2f}",
                f"{scores.get('clarity', 0):.2f}",
                f"{scores.get('composite', 0):.2f}",
                f"{perf.get('avg_time_per_trace_s', 0):.1f}s",
            )

    console.print(table)
    console.print()


def _model_label(config: dict) -> str:
    """Build a short label from config model name and provider."""
    if "model" in config:
        model = config.get("model", "unknown")
        provider = config.get("provider", "")
    else:
        section = config.get("extraction", config.get("lead", {}))
        model = section.get("model", "unknown")
        provider = section.get("provider", "")
    short_model = model.split("/")[-1] if "/" in model else model
    return f"{short_model} ({provider})" if provider else short_model
