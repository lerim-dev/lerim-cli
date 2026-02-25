"""Central config loading from layered TOML files with role-based LLM settings.

Layers (low to high priority):
1. lerim/config/default.toml
2. ~/.lerim/config.toml
3. <repo>/.lerim/config.toml
4. LERIM_CONFIG env path (optional explicit override)

API keys are read from environment variables only.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from lerim.config.project_scope import git_root_for, resolve_data_dirs

PACKAGE_DIR = Path(__file__).parent
DEFAULT_CONFIG_PATH = PACKAGE_DIR / "default.toml"
USER_CONFIG_PATH = Path.home() / ".lerim" / "config.toml"
GLOBAL_DATA_DIR = Path.home() / ".lerim"

_LAST_CONFIG_SOURCES: list[dict[str, str]] = []


@dataclass(frozen=True)
class LLMRoleConfig:
    """Role config for PydanticAI orchestration agents."""

    provider: str
    model: str
    api_base: str
    fallback_models: tuple[str, ...]
    timeout_seconds: int
    max_iterations: int
    openrouter_provider_order: tuple[str, ...]


@dataclass(frozen=True)
class DSPyRoleConfig:
    """Role config for DSPy extraction and summarization pipelines.

    sub_provider/sub_model configure the cheaper LM used for RLM sub-LLM calls
    (llm_query / llm_query_batched).  When empty they fall back to provider/model.
    """

    provider: str
    model: str
    api_base: str
    timeout_seconds: int
    max_iterations: int
    max_llm_calls: int
    sub_provider: str
    sub_model: str
    openrouter_provider_order: tuple[str, ...]


def load_toml_file(path: Path | None) -> dict[str, Any]:
    """Load TOML file into a dict; return empty dict on failures."""
    if not path or not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge dict values with override precedence."""
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def _expand(value: Any, default: Path) -> Path:
    """Expand user path with fallback to default path."""
    if value in (None, ""):
        return default
    try:
        return Path(str(value)).expanduser()
    except (TypeError, OSError, ValueError):
        return default


def _to_non_empty_string(value: Any) -> str:
    """Convert value to stripped string, defaulting to empty string."""
    if value is None:
        return ""
    return str(value).strip()


def _to_int(value: Any, default: int, minimum: int = 1) -> int:
    """Convert value to bounded integer with fallback default."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def _to_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    """Convert value to bounded float with fallback default."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _to_fallback_models(value: Any) -> tuple[str, ...]:
    """Normalize fallback model list from TOML list/string values."""
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str):
        parts = [item.strip() for item in value.split(",")]
        return tuple(item for item in parts if item)
    return ()


def get_user_config_path() -> Path:
    """Return canonical user config path."""
    return USER_CONFIG_PATH


def ensure_user_config_exists() -> Path:
    """Create user config scaffold outside pytest if it does not exist."""
    path = USER_CONFIG_PATH
    if path.exists() or os.getenv("PYTEST_CURRENT_TEST"):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """\
# Lerim user overrides
# Override only keys you need.

# [roles.lead]
# provider = "openrouter"
# model = "qwen/qwen3-coder-30b-a3b-instruct"

# [roles.extract]
# provider = "ollama"
# model = "qwen3:8b"
# sub_model = "qwen3:4b"
""",
        encoding="utf-8",
    )
    return path


def _ensure_project_config_exists(data_root: Path) -> Path:
    """Create project config scaffold under a resolved data root when missing."""
    path = data_root / "config.toml"
    if path.exists() or os.getenv("PYTEST_CURRENT_TEST"):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """\
# Lerim project overrides
# Only set what this repo needs.

# [memory]
# scope = "project_fallback_global"

# [roles.lead]
# model = "qwen/qwen3-coder-30b-a3b-instruct"
""",
        encoding="utf-8",
    )
    gitignore = data_root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            """\
# Lerim data (not committed by default)
memory/
index/
workspace/
meta/
""",
            encoding="utf-8",
        )
    return path


def _load_layers() -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Load and merge all configuration layers in precedence order."""
    merged: dict[str, Any] = {}
    sources: list[dict[str, str]] = []

    layers: list[tuple[str, Path]] = [
        ("package_default", DEFAULT_CONFIG_PATH),
        ("user", USER_CONFIG_PATH),
    ]

    project_root = git_root_for(Path.cwd())
    if project_root:
        project_dir = project_root / ".lerim"
        layers.append(("project", project_dir / "config.toml"))

    explicit = os.getenv("LERIM_CONFIG")
    if explicit:
        layers.append(("explicit", Path(explicit).expanduser()))

    for source_name, path in layers:
        payload = load_toml_file(path)
        if payload:
            merged = _deep_merge(merged, payload)
            sources.append({"source": source_name, "path": str(path)})

    return merged, sources


def get_config_sources() -> list[dict[str, str]]:
    """Return last-computed config source list."""
    return [dict(item) for item in _LAST_CONFIG_SOURCES]


@dataclass(frozen=True)
class Config:
    """Effective runtime configuration from TOML layers and environment."""

    data_dir: Path
    global_data_dir: Path
    memory_dir: Path
    index_dir: Path
    memories_db_path: Path
    graph_db_path: Path
    sessions_db_path: Path
    platforms_path: Path

    memory_scope: str
    memory_project_dir_name: str

    decay_enabled: bool
    decay_days: int
    decay_min_confidence_floor: float
    decay_archive_threshold: float
    decay_recent_access_grace_days: int

    server_host: str
    server_port: int
    poll_interval_minutes: int

    lead_role: LLMRoleConfig
    explorer_role: LLMRoleConfig
    extract_role: DSPyRoleConfig
    summarize_role: DSPyRoleConfig

    tracing_enabled: bool
    tracing_include_httpx: bool
    tracing_include_content: bool

    anthropic_api_key: str | None
    openai_api_key: str | None
    zai_api_key: str | None
    openrouter_api_key: str | None

    @property
    def provider(self) -> str:
        """Backward-compatible shortcut to lead role provider."""
        return self.lead_role.provider

    @property
    def agent_model(self) -> str:
        """Backward-compatible shortcut to lead role model."""
        return self.lead_role.model

    @property
    def agent_timeout(self) -> int:
        """Backward-compatible shortcut to lead role timeout."""
        return self.lead_role.timeout_seconds

    @property
    def dspy_provider(self) -> str:
        """Backward-compatible shortcut to extract role provider."""
        return self.extract_role.provider

    @property
    def dspy_model(self) -> str:
        """Backward-compatible shortcut to extract role model."""
        return self.extract_role.model

    @property
    def dspy_api_base(self) -> str:
        """Backward-compatible shortcut to extract role API base."""
        return self.extract_role.api_base

    @property
    def dspy_rlm_max_iterations(self) -> int:
        """Backward-compatible shortcut to extract role max iterations."""
        return self.extract_role.max_iterations

    @property
    def dspy_rlm_max_llm_calls(self) -> int:
        """Backward-compatible shortcut to extract role max call budget."""
        return self.extract_role.max_llm_calls

    def public_dict(self) -> dict[str, Any]:
        """Return safe serialized config for CLI/dashboard visibility."""
        return {
            "data_dir": str(self.data_dir),
            "global_data_dir": str(self.global_data_dir),
            "memory_dir": str(self.memory_dir),
            "index_dir": str(self.index_dir),
            "memories_db_path": str(self.memories_db_path),
            "graph_db_path": str(self.graph_db_path),
            "sessions_db_path": str(self.sessions_db_path),
            "platforms_path": str(self.platforms_path),
            "memory_scope": self.memory_scope,
            "memory_project_dir_name": self.memory_project_dir_name,
            "server_host": self.server_host,
            "server_port": self.server_port,
            "poll_interval_minutes": self.poll_interval_minutes,
            "lead_role": {
                "provider": self.lead_role.provider,
                "model": self.lead_role.model,
                "api_base": self.lead_role.api_base,
                "fallback_models": list(self.lead_role.fallback_models),
                "timeout_seconds": self.lead_role.timeout_seconds,
                "max_iterations": self.lead_role.max_iterations,
                "openrouter_provider_order": list(
                    self.lead_role.openrouter_provider_order
                ),
            },
            "explorer_role": {
                "provider": self.explorer_role.provider,
                "model": self.explorer_role.model,
                "api_base": self.explorer_role.api_base,
                "fallback_models": list(self.explorer_role.fallback_models),
                "timeout_seconds": self.explorer_role.timeout_seconds,
                "max_iterations": self.explorer_role.max_iterations,
                "openrouter_provider_order": list(
                    self.explorer_role.openrouter_provider_order
                ),
            },
            "extract_role": {
                "provider": self.extract_role.provider,
                "model": self.extract_role.model,
                "api_base": self.extract_role.api_base,
                "timeout_seconds": self.extract_role.timeout_seconds,
                "max_iterations": self.extract_role.max_iterations,
                "max_llm_calls": self.extract_role.max_llm_calls,
                "sub_provider": self.extract_role.sub_provider,
                "sub_model": self.extract_role.sub_model,
                "openrouter_provider_order": list(
                    self.extract_role.openrouter_provider_order
                ),
            },
            "summarize_role": {
                "provider": self.summarize_role.provider,
                "model": self.summarize_role.model,
                "api_base": self.summarize_role.api_base,
                "timeout_seconds": self.summarize_role.timeout_seconds,
                "max_iterations": self.summarize_role.max_iterations,
                "max_llm_calls": self.summarize_role.max_llm_calls,
                "sub_provider": self.summarize_role.sub_provider,
                "sub_model": self.summarize_role.sub_model,
                "openrouter_provider_order": list(
                    self.summarize_role.openrouter_provider_order
                ),
            },
            "decay_enabled": self.decay_enabled,
            "decay_days": self.decay_days,
            "decay_min_confidence_floor": self.decay_min_confidence_floor,
            "decay_archive_threshold": self.decay_archive_threshold,
            "decay_recent_access_grace_days": self.decay_recent_access_grace_days,
            "tracing_enabled": self.tracing_enabled,
            "tracing_include_httpx": self.tracing_include_httpx,
            "tracing_include_content": self.tracing_include_content,
        }


def _to_string_tuple(value: Any) -> tuple[str, ...]:
    """Normalize a TOML list/string into a tuple of non-empty strings."""
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str):
        parts = [item.strip() for item in value.split(",")]
        return tuple(item for item in parts if item)
    return ()


def _build_llm_role(
    raw: dict[str, Any], *, default_provider: str, default_model: str
) -> LLMRoleConfig:
    """Build one orchestration role config from TOML payload."""
    provider = _to_non_empty_string(raw.get("provider")) or default_provider
    model = _to_non_empty_string(raw.get("model")) or default_model
    return LLMRoleConfig(
        provider=provider,
        model=model,
        api_base=_to_non_empty_string(raw.get("api_base")),
        fallback_models=_to_fallback_models(raw.get("fallback_models")),
        timeout_seconds=_to_int(raw.get("timeout_seconds"), 300, minimum=30),
        max_iterations=_to_int(raw.get("max_iterations"), 24, minimum=1),
        openrouter_provider_order=_to_string_tuple(
            raw.get("openrouter_provider_order")
        ),
    )


def _build_dspy_role(
    raw: dict[str, Any], *, default_provider: str, default_model: str
) -> DSPyRoleConfig:
    """Build one DSPy role config from TOML payload."""
    provider = _to_non_empty_string(raw.get("provider")) or default_provider
    model = _to_non_empty_string(raw.get("model")) or default_model
    sub_provider = _to_non_empty_string(raw.get("sub_provider")) or provider
    sub_model = _to_non_empty_string(raw.get("sub_model")) or model
    return DSPyRoleConfig(
        provider=provider,
        model=model,
        api_base=_to_non_empty_string(raw.get("api_base")),
        timeout_seconds=_to_int(raw.get("timeout_seconds"), 120, minimum=10),
        max_iterations=_to_int(raw.get("max_iterations"), 24, minimum=1),
        max_llm_calls=_to_int(raw.get("max_llm_calls"), 24, minimum=1),
        sub_provider=sub_provider,
        sub_model=sub_model,
        openrouter_provider_order=_to_string_tuple(
            raw.get("openrouter_provider_order")
        ),
    )


@lru_cache(maxsize=1)
def load_config() -> Config:
    """Load effective config from TOML layers plus env API keys."""
    load_dotenv()
    ensure_user_config_exists()
    toml_data, sources = _load_layers()

    global _LAST_CONFIG_SOURCES
    _LAST_CONFIG_SOURCES = sources

    data = toml_data.get("data", {})
    memory = toml_data.get("memory", {})
    decay = memory.get("decay", {}) if isinstance(memory.get("decay", {}), dict) else {}
    server = toml_data.get("server", {})
    roles = (
        toml_data.get("roles", {})
        if isinstance(toml_data.get("roles", {}), dict)
        else {}
    )
    runtime = (
        toml_data.get("runtime", {})
        if isinstance(toml_data.get("runtime", {}), dict)
        else {}
    )
    tracing = (
        toml_data.get("tracing", {})
        if isinstance(toml_data.get("tracing", {}), dict)
        else {}
    )

    global_data_dir = _expand(data.get("dir"), GLOBAL_DATA_DIR)

    memory_scope = (
        _to_non_empty_string(memory.get("scope")).lower() or "project_fallback_global"
    )
    memory_project_dir_name = (
        _to_non_empty_string(memory.get("project_dir_name")) or ".lerim"
    )

    scope = resolve_data_dirs(
        scope=memory_scope,
        project_dir_name=memory_project_dir_name,
        global_data_dir=global_data_dir,
        repo_path=Path.cwd(),
    )
    primary = scope.ordered_data_dirs[0] if scope.ordered_data_dirs else global_data_dir

    memory_dir = primary / "memory"
    index_dir = primary / "index"

    # Lazy import: structural circular dependency (settings -> memory_repo -> memory_record -> extract_pipeline -> settings)
    from lerim.memory.memory_repo import build_memory_paths, ensure_memory_paths

    for data_root in scope.ordered_data_dirs:
        ensure_memory_paths(build_memory_paths(data_root))
        _ensure_project_config_exists(data_root)

    lead_role = _build_llm_role(
        roles.get("lead", {}) if isinstance(roles.get("lead", {}), dict) else {},
        default_provider="openrouter",
        default_model="qwen/qwen3-coder-30b-a3b-instruct",
    )
    explorer_role = _build_llm_role(
        roles.get("explorer", {})
        if isinstance(roles.get("explorer", {}), dict)
        else {},
        default_provider=lead_role.provider,
        default_model=lead_role.model,
    )
    extract_role = _build_dspy_role(
        roles.get("extract", {}) if isinstance(roles.get("extract", {}), dict) else {},
        default_provider="ollama",
        default_model="qwen3:8b",
    )
    summarize_role = _build_dspy_role(
        roles.get("summarize", {})
        if isinstance(roles.get("summarize", {}), dict)
        else {},
        default_provider=extract_role.provider,
        default_model=extract_role.model,
    )

    port = _to_int(server.get("port"), 8765, minimum=1)
    if port > 65535:
        port = 8765

    return Config(
        data_dir=primary,
        global_data_dir=global_data_dir,
        memory_dir=memory_dir,
        index_dir=index_dir,
        memories_db_path=index_dir / "memories.sqlite3",
        graph_db_path=index_dir / "graph.sqlite3",
        sessions_db_path=global_data_dir / "index" / "sessions.sqlite3",
        platforms_path=global_data_dir / "platforms.json",
        memory_scope=memory_scope,
        memory_project_dir_name=memory_project_dir_name,
        decay_enabled=bool(decay.get("enabled", True)),
        decay_days=_to_int(decay.get("decay_days"), 180, minimum=30),
        decay_min_confidence_floor=_to_float(
            decay.get("min_confidence_floor"), 0.1, minimum=0.0, maximum=1.0
        ),
        decay_archive_threshold=_to_float(
            decay.get("archive_threshold"), 0.2, minimum=0.0, maximum=1.0
        ),
        decay_recent_access_grace_days=_to_int(
            decay.get("recent_access_grace_days"), 30, minimum=0
        ),
        server_host=_to_non_empty_string(server.get("host")) or "127.0.0.1",
        server_port=port,
        poll_interval_minutes=_to_int(
            server.get("poll_interval_minutes"), 5, minimum=1
        ),
        lead_role=lead_role,
        explorer_role=explorer_role,
        extract_role=extract_role,
        summarize_role=summarize_role,
        tracing_enabled=bool(tracing.get("enabled", False))
        or os.getenv("LERIM_TRACING", "").strip().lower() in ("1", "true", "yes", "on"),
        tracing_include_httpx=bool(tracing.get("include_httpx", False)),
        tracing_include_content=bool(tracing.get("include_content", True)),
        anthropic_api_key=_to_non_empty_string(os.environ.get("ANTHROPIC_API_KEY"))
        or None,
        openai_api_key=_to_non_empty_string(os.environ.get("OPENAI_API_KEY")) or None,
        zai_api_key=_to_non_empty_string(os.environ.get("ZAI_API_KEY")) or None,
        openrouter_api_key=_to_non_empty_string(os.environ.get("OPENROUTER_API_KEY"))
        or None,
    )


def get_config() -> Config:
    """Return cached effective configuration."""
    return load_config()


def reload_config() -> Config:
    """Clear config cache and return reloaded configuration."""
    load_config.cache_clear()
    return load_config()


if __name__ == "__main__":
    """Run a real-path config smoke test and role validation checks."""
    cfg = load_config()
    assert cfg.data_dir
    assert cfg.memory_dir
    assert cfg.index_dir
    assert cfg.memories_db_path.name == "memories.sqlite3"
    assert cfg.lead_role.provider
    assert cfg.lead_role.model
    assert isinstance(cfg.lead_role.fallback_models, tuple)
    assert cfg.explorer_role.provider
    assert cfg.extract_role.provider
    assert cfg.extract_role.max_iterations >= 1
    assert cfg.extract_role.max_llm_calls >= 1
    assert cfg.summarize_role.provider
    assert cfg.summarize_role.max_iterations >= 1
    assert cfg.summarize_role.max_llm_calls >= 1
    assert isinstance(cfg.tracing_enabled, bool)
    assert isinstance(cfg.tracing_include_httpx, bool)
    assert isinstance(cfg.tracing_include_content, bool)
    payload = cfg.public_dict()
    assert "lead_role" in payload
    assert "explorer_role" in payload
    assert "extract_role" in payload
    assert "summarize_role" in payload
    assert "decay_enabled" in payload
    print(
        f"""\
Config loaded: \
data_dir={cfg.data_dir}, \
lead={cfg.lead_role.provider}/{cfg.lead_role.model}, \
extract={cfg.extract_role.provider}/{cfg.extract_role.model}"""
    )
