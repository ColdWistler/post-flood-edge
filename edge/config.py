"""Load edge settings from a simple .env file, the environment and overrides."""

from __future__ import annotations

import os
from dataclasses import fields
from pathlib import Path
from typing import Any, Callable

from .models import EdgeConfig

_ENV_FIELDS: tuple[tuple[str, str, Callable[[str], Any]], ...] = (
    ("EDGE_TICK_SECONDS", "tick_seconds", float),
    ("EDGE_BATCH_SIZE", "batch_size", int),
    ("EDGE_BUFFER_MAX_ROWS", "buffer_max_rows", int),
    ("EDGE_FLOW_ZERO_SUSTAINED_S", "flow_zero_sustained_s", float),
    ("EDGE_NODE_INACTIVE_S", "node_inactive_s", float),
    ("EDGE_DERIVATIVE_WINDOW_S", "derivative_window_s", float),
    ("EDGE_VALIDITY_FAULT_SAMPLES", "validity_fault_samples", int),
    ("EDGE_DB_PATH", "db_path", Path),
    ("EDGE_LOG_LEVEL", "log_level", str),
    ("TB_HOST", "tb_host", str),
    ("TB_CA_CERT", "tb_ca_cert", str),
)

_TOKEN_PREFIX = "TB_TOKEN_"


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines, ignoring blanks and comments."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_config(env_path: Path | None = None, **overrides: Any) -> EdgeConfig:
    """Build an EdgeConfig: defaults, then env file, then environment, then overrides."""
    path = Path(".env") if env_path is None else Path(env_path)
    raw = _parse_env_file(path)
    for env_key, _, _ in _ENV_FIELDS:
        if env_key in os.environ:
            raw[env_key] = os.environ[env_key]
    for key, value in os.environ.items():
        if key.startswith(_TOKEN_PREFIX):
            raw[key] = value

    cfg = EdgeConfig()
    for env_key, attr, convert in _ENV_FIELDS:
        if env_key in raw:
            setattr(cfg, attr, convert(raw[env_key]))
    cfg.tokens = {
        node.node_id: raw.get(
            _TOKEN_PREFIX + node.node_id.upper().replace("-", "_"), ""
        )
        for node in cfg.nodes
    }

    known = {f.name for f in fields(cfg)}
    unknown = set(overrides) - known
    if unknown:
        raise TypeError(f"unknown config overrides: {', '.join(sorted(unknown))}")
    for name, value in overrides.items():
        setattr(cfg, name, value)
    return cfg
