"""Central config loading from layered TOML files with role-based LLM settings.

Layers (low to high priority):
1. lerim/config/default.toml
2. ~/.lerim/config.toml
3. LERIM_CONFIG env path (optional explicit override)

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

from lerim.config.project_scope import resolve_data_dirs

PACKAGE_DIR = Path(__file__).parent
DEFAULT_CONFIG_PATH = PACKAGE_DIR / "default.toml"
USER_CONFIG_PATH = Path.home() / ".lerim" / "config.toml"
GLOBAL_DATA_DIR = Path.home() / ".lerim"

_LAST_CONFIG_SOURCES: list[dict[str, str]] = []


@dataclass(frozen=True)
class RoleConfig:
	"""Configuration for any LLM role (lead, extract).

	All fields have defaults so the same class works for lead (uses max_iters_*),
	extract (uses max_window_tokens), or any future role.
	"""

	provider: str
	model: str
	api_base: str = ""
	fallback_models: tuple[str, ...] = ()
	timeout_seconds: int = 300
	openrouter_provider_order: tuple[str, ...] = ()
	thinking: bool = True
	max_tokens: int = 32000
	# Lead-specific
	max_iterations: int = 10
	max_iters_sync: int = 15
	max_iters_maintain: int = 30
	max_iters_ask: int = 30
	# DSPy-specific
	max_window_tokens: int = 100000
	window_overlap_tokens: int = 5000


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


def _ensure_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
	"""Get a dict value from data, returning empty dict if missing or wrong type."""
	val = data.get(key, {})
	return val if isinstance(val, dict) else {}


def _require_int(raw: dict[str, Any], key: str, minimum: int = 0) -> int:
    """Read a required integer from config dict. Raises if missing from config."""
    value = raw.get(key)
    if value is None:
        raise ValueError(
            f"missing required config key: {key} (set it in default.toml or user config)"
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"config key {key} must be an integer, got: {value!r}")
    return max(minimum, parsed)


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
    sessions_db_path: Path
    platforms_path: Path

    memory_scope: str
    memory_project_dir_name: str

    server_host: str
    server_port: int
    sync_interval_minutes: int
    maintain_interval_minutes: int
    sync_window_days: int
    sync_max_sessions: int
    parallel_pipelines: bool

    lead_role: RoleConfig
    extract_role: RoleConfig

    tracing_enabled: bool
    tracing_include_httpx: bool
    tracing_include_content: bool

    anthropic_api_key: str | None
    openai_api_key: str | None
    zai_api_key: str | None
    openrouter_api_key: str | None
    minimax_api_key: str | None
    opencode_api_key: str | None

    provider_api_bases: dict[str, str]
    auto_unload: bool

    cloud_endpoint: str
    cloud_token: str | None

    agents: dict[str, str]
    projects: dict[str, str]

    def public_dict(self) -> dict[str, Any]:
        """Return safe serialized config for CLI/dashboard visibility."""
        return {
            "data_dir": str(self.data_dir),
            "global_data_dir": str(self.global_data_dir),
            "memory_dir": str(self.memory_dir),
            "index_dir": str(self.index_dir),
            "sessions_db_path": str(self.sessions_db_path),
            "platforms_path": str(self.platforms_path),
            "memory_scope": self.memory_scope,
            "memory_project_dir_name": self.memory_project_dir_name,
            "server_host": self.server_host,
            "server_port": self.server_port,
            "sync_interval_minutes": self.sync_interval_minutes,
            "maintain_interval_minutes": self.maintain_interval_minutes,
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
            "extract_role": {
                "provider": self.extract_role.provider,
                "model": self.extract_role.model,
                "api_base": self.extract_role.api_base,
                "timeout_seconds": self.extract_role.timeout_seconds,
                "max_window_tokens": self.extract_role.max_window_tokens,
                "window_overlap_tokens": self.extract_role.window_overlap_tokens,
                "openrouter_provider_order": list(
                    self.extract_role.openrouter_provider_order
                ),
            },
            "parallel_pipelines": self.parallel_pipelines,
            "tracing_enabled": self.tracing_enabled,
            "tracing_include_httpx": self.tracing_include_httpx,
            "tracing_include_content": self.tracing_include_content,
            "provider_api_bases": dict(self.provider_api_bases),
            "auto_unload": self.auto_unload,
            "cloud_endpoint": self.cloud_endpoint,
            "cloud_authenticated": self.cloud_token is not None,
            "agents": dict(self.agents),
            "projects": dict(self.projects),
        }


def _to_string_tuple(value: Any) -> tuple[str, ...]:
    """Normalize a TOML list/string into a tuple of non-empty strings."""
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str):
        parts = [item.strip() for item in value.split(",")]
        return tuple(item for item in parts if item)
    return ()


def _build_role(
	raw: dict[str, Any], *, default_provider: str, default_model: str
) -> RoleConfig:
	"""Build a role config from TOML payload."""
	from lerim.config.providers import normalize_model_name

	provider = _to_non_empty_string(raw.get("provider")) or default_provider
	model = _to_non_empty_string(raw.get("model")) or default_model
	model = normalize_model_name(provider, model)
	return RoleConfig(
		provider=provider,
		model=model,
		api_base=_to_non_empty_string(raw.get("api_base")),
		fallback_models=_to_fallback_models(raw.get("fallback_models")),
		timeout_seconds=_require_int(raw, "timeout_seconds", minimum=10),
		openrouter_provider_order=_to_string_tuple(raw.get("openrouter_provider_order")),
		thinking=bool(raw.get("thinking", True)),
		max_tokens=int(raw.get("max_tokens", 32000)),
		max_iterations=int(raw.get("max_iterations", 10)),
		max_iters_sync=int(raw.get("max_iters_sync", 15)),
		max_iters_maintain=int(raw.get("max_iters_maintain", 30)),
		max_iters_ask=int(raw.get("max_iters_ask", 30)),
		max_window_tokens=int(raw.get("max_window_tokens", 100000)),
		window_overlap_tokens=int(raw.get("window_overlap_tokens", 5000)),
	)


def _build_all_roles(roles: dict[str, Any]) -> tuple[RoleConfig, RoleConfig]:
	"""Build lead and extract role configs from TOML roles section."""
	lead = _build_role(
		_ensure_dict(roles, "lead"),
		default_provider="openrouter",
		default_model="qwen/qwen3-coder-30b-a3b-instruct",
	)
	extract = _build_role(
		_ensure_dict(roles, "extract"),
		default_provider="ollama",
		default_model="qwen3:8b",
	)
	return lead, extract


def _parse_string_table(raw: dict[str, Any]) -> dict[str, str]:
    """Parse a TOML table of ``name = "path"`` or ``name = {path = "..."}`` entries."""
    result: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            text = str(value.get("path", "")).strip()
        else:
            text = str(value).strip() if value is not None else ""
        if text:
            result[key] = text
    return result


def _migrate_platforms_json(platforms_path: Path) -> dict[str, str]:
    """Read platforms.json and return agent name->path mapping for migration."""
    import json

    try:
        data = json.loads(platforms_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    platforms = data.get("platforms", {})
    if not isinstance(platforms, dict):
        return {}
    agents: dict[str, str] = {}
    for name, info in platforms.items():
        path = info.get("path") if isinstance(info, dict) else None
        if path:
            agents[str(name)] = str(path)
    return agents


@lru_cache(maxsize=1)
def load_config() -> Config:
    """Load effective config from TOML layers plus env API keys."""
    # Always load ~/.lerim/.env (CWD-independent), then CWD .env as override
    _lerim_env = Path.home() / ".lerim" / ".env"
    if _lerim_env.is_file():
        load_dotenv(_lerim_env)
    load_dotenv()  # CWD search (overrides ~/.lerim/.env if both exist)
    ensure_user_config_exists()
    toml_data, sources = _load_layers()

    global _LAST_CONFIG_SOURCES
    _LAST_CONFIG_SOURCES = sources

    data = toml_data.get("data", {})
    memory = toml_data.get("memory", {})
    server = toml_data.get("server", {})
    roles = _ensure_dict(toml_data, "roles")
    tracing = _ensure_dict(toml_data, "tracing")

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

    # Lazy import: structural circular dependency (settings -> memory_repo -> memory_record -> settings)
    from lerim.memory.repo import build_memory_paths, ensure_memory_paths

    for data_root in scope.ordered_data_dirs:
        ensure_memory_paths(build_memory_paths(data_root))

    lead_role, extract_role = _build_all_roles(roles)

    port = _require_int(server, "port", minimum=1)
    if port > 65535:
        port = 8765

    cloud = _ensure_dict(toml_data, "cloud")

    agents = _parse_string_table(_ensure_dict(toml_data, "agents"))
    projects = _parse_string_table(_ensure_dict(toml_data, "projects"))

    # Migrate platforms.json -> [agents] if agents section is empty
    platforms_path = global_data_dir / "platforms.json"
    if not agents and platforms_path.exists():
        agents = _migrate_platforms_json(platforms_path)

    cloud_endpoint = (
        _to_non_empty_string(os.environ.get("LERIM_CLOUD_ENDPOINT"))
        or _to_non_empty_string(cloud.get("endpoint"))
        or "https://api.lerim.dev"
    )
    cloud_token = (
        _to_non_empty_string(os.environ.get("LERIM_CLOUD_TOKEN"))
        or _to_non_empty_string(cloud.get("token"))
        or None
    )

    return Config(
        data_dir=primary,
        global_data_dir=global_data_dir,
        memory_dir=memory_dir,
        index_dir=index_dir,
        sessions_db_path=global_data_dir / "index" / "sessions.sqlite3",
        platforms_path=global_data_dir / "platforms.json",
        memory_scope=memory_scope,
        memory_project_dir_name=memory_project_dir_name,
        server_host=_to_non_empty_string(server.get("host")) or "127.0.0.1",
        server_port=port,
        sync_interval_minutes=_require_int(server, "sync_interval_minutes", minimum=1),
        maintain_interval_minutes=_require_int(
            server, "maintain_interval_minutes", minimum=1
        ),
        sync_window_days=_require_int(server, "sync_window_days", minimum=1),
        sync_max_sessions=_require_int(server, "sync_max_sessions", minimum=1),
        parallel_pipelines=bool(server.get("parallel_pipelines", True)),
        lead_role=lead_role,
        extract_role=extract_role,
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
        minimax_api_key=_to_non_empty_string(os.environ.get("MINIMAX_API_KEY")) or None,
        opencode_api_key=_to_non_empty_string(os.environ.get("OPENCODE_API_KEY"))
        or None,
        provider_api_bases=_parse_string_table(_ensure_dict(toml_data, "providers")),
        auto_unload=bool(_ensure_dict(toml_data, "providers").get("auto_unload", True)),
        cloud_endpoint=cloud_endpoint,
        cloud_token=cloud_token,
        agents=agents,
        projects=projects,
    )


def get_config() -> Config:
    """Return cached config from TOML layers + env."""
    return load_config()


def reload_config() -> Config:
    """Clear config cache and return reloaded configuration."""
    load_config.cache_clear()
    return load_config()


def _toml_value(value: Any) -> str:
    """Serialize a Python value to TOML literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (list, tuple)):
        items = ", ".join(_toml_value(item) for item in value)
        return f"[{items}]"
    return f'"{value}"'


def _toml_write_dict(lines: list[str], data: dict[str, Any], prefix: str) -> None:
    """Write a dict as TOML lines. Handles nested tables and basic types."""
    scalars = {}
    tables = {}
    for key, value in data.items():
        if isinstance(value, dict):
            tables[key] = value
        else:
            scalars[key] = value
    for key, value in scalars.items():
        lines.append(f"{key} = {_toml_value(value)}\n")
    for key, value in tables.items():
        section = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        lines.append(f"\n[{section}]\n")
        _toml_write_dict(lines, value, section)


def save_config_patch(patch: dict[str, Any]) -> Config:
    """Apply config patch to user config TOML and return reloaded Config.

    Reads existing ~/.lerim/config.toml, deep-merges the patch, writes back,
    then reloads the cached config.
    """
    user_path = USER_CONFIG_PATH
    user_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, Any] = {}
    if user_path.exists():
        existing = load_toml_file(user_path)

    merged = _deep_merge(existing, patch)
    return _write_config_full(merged)


def _write_config_full(data: dict[str, Any]) -> Config:
    """Write complete config dict to user TOML and return reloaded Config."""
    user_path = USER_CONFIG_PATH
    user_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Lerim user config\n"]
    _toml_write_dict(lines, data, prefix="")
    user_path.write_text("".join(lines), encoding="utf-8")
    return reload_config()


if __name__ == "__main__":
    """Run a real-path config smoke test and role validation checks."""
    cfg = load_config()
    assert cfg.data_dir
    assert cfg.memory_dir
    assert cfg.index_dir
    assert cfg.sessions_db_path.name == "sessions.sqlite3"
    assert cfg.lead_role.provider
    assert cfg.lead_role.model
    assert isinstance(cfg.lead_role.fallback_models, tuple)
    assert cfg.extract_role.provider
    assert cfg.extract_role.max_window_tokens >= 1000
    assert cfg.extract_role.window_overlap_tokens >= 0
    assert isinstance(cfg.tracing_enabled, bool)
    assert isinstance(cfg.tracing_include_httpx, bool)
    assert isinstance(cfg.tracing_include_content, bool)
    assert isinstance(cfg.agents, dict)
    assert isinstance(cfg.projects, dict)
    payload = cfg.public_dict()
    assert "lead_role" in payload
    assert "extract_role" in payload
    assert "agents" in payload
    assert "projects" in payload
    print(
        f"""\
Config loaded: \
data_dir={cfg.data_dir}, \
lead={cfg.lead_role.provider}/{cfg.lead_role.model}, \
extract={cfg.extract_role.provider}/{cfg.extract_role.model}, \
agents={list(cfg.agents.keys())}, \
projects={list(cfg.projects.keys())}"""
    )
