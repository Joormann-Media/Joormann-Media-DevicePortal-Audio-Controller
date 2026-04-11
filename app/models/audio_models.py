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
    channels: str = ""
    sample_rate: str = ""
    description: str = ""
    hardware_present: bool = False
    physical_likely: bool = False
    monitor_source_name: str = ""
    active_port: str = ""

    # Output volume model
    volume_percent_current: int | None = None
    volume_db_current: float | None = None
    volume_raw_current: int | None = None
    base_volume_percent: int | None = None
    base_volume_db: float | None = None

    # Input/source volume model
    source_volume_percent_current: int | None = None
    source_volume_db_current: float | None = None
    source_volume_raw_current: int | None = None

    has_hw_volume: bool = False
    has_hw_mute: bool = False

    # Input capture gain model
    has_capture_gain: bool = False
    capture_gain_percent: int | None = None
    capture_gain_db: float | None = None
    capture_gain_control: str = ""

    mic_boost_available: bool = False
    mic_boost_percent: int | None = None
    mic_boost_db: float | None = None
    mic_boost_control: str = ""

    channel_volumes: List[Dict[str, Any]] = field(default_factory=list)
    diagnostic_flags: List[str] = field(default_factory=list)
    hw_controls: List[Dict[str, Any]] = field(default_factory=list)

    # Meter fields (filled by API meter endpoint / live updates)
    rms_percent: int | None = None
    peak_percent: int | None = None

    # Backward compatibility for older UI paths
    volume_percent: int | None = None

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
