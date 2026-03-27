"""Ollama model load/unload lifecycle manager.

Provides a context manager that loads Ollama models before a sync/maintain
cycle and unloads them immediately after, freeing RAM between cycles.

Only activates when at least one configured role uses provider="ollama".
For cloud providers the context manager is a no-op.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Generator

    from lerim.config.settings import Config


def _ollama_models(config: Config) -> list[tuple[str, str]]:
    """Return deduplicated (base_url, model) pairs for all ollama roles."""
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []

    default_base = config.provider_api_bases.get("ollama", "http://127.0.0.1:11434")

    for role in (config.lead_role,):
        if role.provider == "ollama":
            base = role.api_base or default_base
            key = (base, role.model)
            if key not in seen:
                seen.add(key)
                pairs.append(key)

    for role in (config.extract_role, config.summarize_role):
        if role.provider == "ollama":
            base = role.api_base or default_base
            key = (base, role.model)
            if key not in seen:
                seen.add(key)
                pairs.append(key)

    return pairs


def _is_ollama_reachable(base_url: str, timeout: float = 5.0) -> bool:
    """Check if Ollama is reachable at the given base URL."""
    try:
        resp = httpx.get(f"{base_url}/api/tags", timeout=timeout)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        return False


def _load_model(base_url: str, model: str, timeout: float = 120.0) -> None:
    """Warm-load an Ollama model by sending a minimal generation request."""
    httpx.post(
        f"{base_url}/api/generate",
        json={"model": model, "prompt": "hi", "options": {"num_predict": 1}},
        timeout=timeout,
    )


def _unload_model(base_url: str, model: str, timeout: float = 30.0) -> None:
    """Unload an Ollama model by setting keep_alive to 0."""
    httpx.post(
        f"{base_url}/api/generate",
        json={"model": model, "keep_alive": 0},
        timeout=timeout,
    )


@contextmanager
def ollama_lifecycle(config: Config) -> Generator[None, None, None]:
    """Context manager that loads Ollama models on enter and unloads on exit.

    No-op when no roles use provider="ollama" or when auto_unload is False.
    Logs warnings on failure but never raises — the daemon must not crash
    because of lifecycle issues.
    """
    from lerim.config.logging import logger

    models = _ollama_models(config)

    if not models:
        yield
        return

    # Group models by base_url for a single reachability check per server.
    bases = {base for base, _ in models}

    reachable_bases: set[str] = set()
    for base in bases:
        if _is_ollama_reachable(base):
            reachable_bases.add(base)
        else:
            logger.warning("ollama not reachable at {}, skipping lifecycle", base)

    # Warm-load models on reachable servers.
    for base, model in models:
        if base not in reachable_bases:
            continue
        try:
            logger.info("loading ollama model {}/{}", base, model)
            _load_model(base, model)
        except Exception as exc:
            logger.warning("failed to warm-load {}/{}: {}", base, model, exc)

    try:
        yield
    finally:
        if not config.auto_unload:
            return

        for base, model in models:
            if base not in reachable_bases:
                continue
            try:
                logger.info("unloading ollama model {}/{}", base, model)
                _unload_model(base, model)
            except Exception as exc:
                logger.warning("failed to unload {}/{}: {}", base, model, exc)
