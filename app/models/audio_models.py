from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass(slots=True)
class AudioDevice:
    stable_id: str
    device_class: str
    display_name: str
    technical_name: str
    backend_ids: Dict[str, str] = field(default_factory=dict)
    card_name: str = ""
    card_index: int | None = None
    device_index: int | None = None
    bus_type: str = "unknown"
    connection_label: str = ""
    profile: str = ""
    ports: List[str] = field(default_factory=list)
    state: str = "unknown"
    default: bool = False
    muted: bool = False
    volume_percent: int | None = None
    channels: str = ""
    sample_rate: str = ""
    description: str = ""
    hardware_present: bool = False
    physical_likely: bool = False
    monitor_source_name: str = ""
    diagnostic_flags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AudioStream:
    stream_id: str
    direction: str
    app_name: str
    process_name: str
    process_id: str
    target_device_stable_id: str
    target_device_name: str
    muted: bool = False
    volume_percent: int | None = None
    state: str = "running"
    technical_name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
