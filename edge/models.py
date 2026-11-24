"""Shared data contracts for the edge simulator."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Reading:
    """One sample from one node, with a flat telemetry dictionary."""

    node_id: str
    ts_ms: int
    values: dict[str, Any]


@dataclass
class NodeConfig:
    """Per-node identity, location and intrusion-event parameters."""

    node_id: str
    latitude: float
    longitude: float
    seed: int
    amplitude: float
    onset_offset_s: float


@dataclass
class EdgeConfig:
    """All runtime settings; defaults are the accelerated demo values."""

    nodes: list[NodeConfig] = field(
        default_factory=lambda: [
            NodeConfig("node-a", 27.7172, 85.3240, 1001, 500, 0.0),
            NodeConfig("node-b", 27.6790, 85.3190, 1002, 825, 20.0),
            NodeConfig("node-c", 27.7500, 85.3700, 1003, 1250, 40.0),
        ]
    )
    tick_seconds: float = 5.0
    batch_size: int = 50
    buffer_max_rows: int = 6048
    flow_zero_sustained_s: float = 30.0
    node_inactive_s: float = 90.0
    derivative_window_s: float = 50.0
    validity_fault_samples: int = 3
    db_path: Path = Path("edge.db")
    use_mqtt: bool = False
    tb_host: str = ""
    tb_ca_cert: str = ""
    tokens: dict[str, str] = field(
        default_factory=lambda: {"node-a": "", "node-b": "", "node-c": ""}
    )
    log_level: str = "INFO"
