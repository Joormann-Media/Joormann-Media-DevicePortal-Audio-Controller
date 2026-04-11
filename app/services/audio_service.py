from __future__ import annotations

import hashlib
import json
import platform
from threading import Lock
from typing import Any, Dict

from app.services.audio_backend import AudioBackend
from app.services.audio_control import AudioControlService
from app.services.audio_diagnostics import build_diagnostics
from app.services.audio_meter import AudioMeterService
from app.services.audio_normalize import normalize_audio


class AudioService:
    def __init__(self) -> None:
        self.backend = AudioBackend()
        self.control = AudioControlService(self.backend.runner)
        self.meter = AudioMeterService()
        self._lock = Lock()

    def build_snapshot(self, include_diagnostics: bool = False) -> Dict[str, Any]:
        with self._lock:
            raw = self.backend.collect_raw()
            normalized = normalize_audio(raw)
            summary = self._build_summary(raw, normalized)
            base: Dict[str, Any] = {
                "timestamp_utc": raw["timestamp_utc"],
                "summary": summary,
                "devices": {
                    "outputs": normalized["output_devices"],
                    "inputs": normalized["input_devices"],
                    "monitors": normalized["monitor_devices"],
                    "virtual": normalized["virtual_devices"],
                },
                "streams": normalized["streams"],
                "defaults": normalized["defaults"],
                "snapshot_hash": self._snapshot_hash(summary, normalized),
                "device_lookup": normalized["device_lookup"],
            }
            if include_diagnostics:
                base["diagnostics"] = build_diagnostics(raw, normalized)
                base["hidden_diagnostic_only"] = normalized["hidden_diagnostic_only"]
            return base

    def _build_summary(self, raw: Dict[str, Any], normalized: Dict[str, Any]) -> Dict[str, Any]:
        defaults = normalized["defaults"]
        default_out = next((d for d in normalized["output_devices"] if d["technical_name"] == defaults.get("sink")), None)
        default_in = next((d for d in normalized["input_devices"] if d["technical_name"] == defaults.get("source")), None)
        commands = raw["commands"]
        return {
            "hostname": raw["hostname"],
            "platform": platform.platform(),
            "backend_detected": {
                "pipewire_wpctl": commands["wpctl_status"]["success"],
                "pulse_pactl": commands["pactl_sinks"]["success"] and commands["pactl_sources"]["success"],
                "alsa": commands["aplay_l"]["success"] or commands["arecord_l"]["success"],
            },
            "outputs_count": len(normalized["output_devices"]),
            "inputs_count": len(normalized["input_devices"]),
            "streams_count": len(normalized["streams"]),
            "default_output": default_out["display_name"] if default_out else defaults.get("sink", ""),
            "default_input": default_in["display_name"] if default_in else defaults.get("source", ""),
            "default_output_stable_id": default_out["stable_id"] if default_out else "",
            "default_input_stable_id": default_in["stable_id"] if default_in else "",
            "wireplumber_status": "ok" if commands["wpctl_status"]["success"] else "unavailable",
        }

    def _snapshot_hash(self, summary: Dict[str, Any], normalized: Dict[str, Any]) -> str:
        payload = {
            "summary": summary,
            "outputs": normalized["output_devices"],
            "inputs": normalized["input_devices"],
            "streams": normalized["streams"],
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get_meters(self) -> Dict[str, Any]:
        snapshot = self.build_snapshot(include_diagnostics=False)
        meter_devices = [*snapshot["devices"]["outputs"], *snapshot["devices"]["inputs"]]
        return {
            "timestamp_utc": snapshot["timestamp_utc"],
            "snapshot_hash": snapshot["snapshot_hash"],
            "meters": self.meter.get_meters(meter_devices),
        }

    def set_device_default(self, stable_id: str) -> Dict[str, Any]:
        snapshot = self.build_snapshot(include_diagnostics=False)
        device = snapshot["device_lookup"].get(stable_id)
        if not device:
            return {"ok": False, "error": "unknown device stable_id"}
        if device.get("device_class") not in {"output_device", "input_device"}:
            return {"ok": False, "error": "device class not settable"}
        ok, msg = self.control.set_default(device)
        return {"ok": ok, "error": "" if ok else msg}

    def set_device_volume(self, stable_id: str, volume_percent: int) -> Dict[str, Any]:
        snapshot = self.build_snapshot(include_diagnostics=False)
        device = snapshot["device_lookup"].get(stable_id)
        if not device:
            return {"ok": False, "error": "unknown device stable_id"}
        if device.get("device_class") not in {"output_device", "input_device"}:
            return {"ok": False, "error": "device class not supported"}
        ok, msg = self.control.set_volume(device, volume_percent)
        return {"ok": ok, "error": "" if ok else msg}

    def set_device_mute(self, stable_id: str, mute: bool) -> Dict[str, Any]:
        snapshot = self.build_snapshot(include_diagnostics=False)
        device = snapshot["device_lookup"].get(stable_id)
        if not device:
            return {"ok": False, "error": "unknown device stable_id"}
        if device.get("device_class") not in {"output_device", "input_device"}:
            return {"ok": False, "error": "device class not supported"}
        ok, msg = self.control.set_mute(device, bool(mute))
        return {"ok": ok, "error": "" if ok else msg}

    def set_stream_volume(self, stream_id: str, volume_percent: int) -> Dict[str, Any]:
        ok, msg = self.control.set_stream_volume(stream_id, volume_percent)
        return {"ok": ok, "error": "" if ok else msg}
