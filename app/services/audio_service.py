from __future__ import annotations

import hashlib
import json
import platform
import time
from pathlib import Path
from threading import Lock
from typing import Any, Dict

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
_METER_AUTOSTART_FILE = _CONFIG_DIR / "meter_autostart.json"


def _read_meter_autostart() -> bool:
    try:
        data = json.loads(_METER_AUTOSTART_FILE.read_text(encoding="utf-8"))
        return bool(data.get("enabled", False))
    except Exception:
        return False


def _write_meter_autostart(enabled: bool) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _METER_AUTOSTART_FILE.write_text(
        json.dumps({"enabled": enabled}, indent=2), encoding="utf-8"
    )

from app.services.audio_backend import AudioBackend
from app.services.audio_calibration import AudioCalibrationService
from app.services.audio_control import AudioControlService
from app.services.audio_diagnostics import build_diagnostics
from app.services.audio_meter import AudioMeterService
from app.services.audio_normalize import normalize_audio
from app.services.audio_recorder import AudioRecorderService


class AudioService:
    def __init__(self) -> None:
        self.backend = AudioBackend()
        self.control = AudioControlService(self.backend.runner)
        self.meter = AudioMeterService()
        self.recorder = AudioRecorderService()
        self.calibration = AudioCalibrationService(self.recorder)
        self._lock = Lock()
        self._snapshot_cache: Dict[str, Any] | None = None
        self._snapshot_cache_ts: float = 0.0
        self._snapshot_cache_ttl_sec: float = 1.5
        if _read_meter_autostart():
            self.meter.start()

    def build_snapshot(self, include_diagnostics: bool = False) -> Dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            if not include_diagnostics and self._snapshot_cache is not None and (now - self._snapshot_cache_ts) <= self._snapshot_cache_ttl_sec:
                return self._snapshot_cache

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
            else:
                self._snapshot_cache = base
                self._snapshot_cache_ts = now
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

    def _lookup_device(self, stable_id: str) -> Dict[str, Any] | None:
        snapshot = self.build_snapshot(include_diagnostics=False)
        return snapshot["device_lookup"].get(stable_id)

    def _lookup_allowed_alsa_control(self, device: Dict[str, Any], control_name: str) -> Dict[str, Any] | None:
        needle = (control_name or "").strip().lower()
        if needle == "":
            return None
        for control in device.get("alsa_controls", []) or []:
            name = str(control.get("name", "")).strip()
            if name.lower() == needle:
                return control
        return None

    def set_device_default(self, stable_id: str) -> Dict[str, Any]:
        device = self._lookup_device(stable_id)
        if not device:
            return {"ok": False, "error": "unknown device stable_id"}
        if device.get("device_class") not in {"output_device", "input_device"}:
            return {"ok": False, "error": "device class not settable"}
        ok, msg = self.control.set_default(device)
        return {"ok": ok, "error": "" if ok else msg}

    def set_output_volume(self, stable_id: str, volume_percent: int) -> Dict[str, Any]:
        device = self._lookup_device(stable_id)
        if not device:
            return {"ok": False, "error": "unknown device stable_id"}
        if device.get("device_class") != "output_device":
            return {"ok": False, "error": "not an output device"}
        ok, msg = self.control.set_output_volume(device, volume_percent)
        return {"ok": ok, "error": "" if ok else msg}

    def set_input_volume(self, stable_id: str, volume_percent: int) -> Dict[str, Any]:
        device = self._lookup_device(stable_id)
        if not device:
            return {"ok": False, "error": "unknown device stable_id"}
        if device.get("device_class") != "input_device":
            return {"ok": False, "error": "not an input device"}
        ok, msg = self.control.set_input_volume(device, volume_percent)
        return {"ok": ok, "error": "" if ok else msg}

    def set_output_mute(self, stable_id: str, mute: bool) -> Dict[str, Any]:
        device = self._lookup_device(stable_id)
        if not device:
            return {"ok": False, "error": "unknown device stable_id"}
        if device.get("device_class") != "output_device":
            return {"ok": False, "error": "not an output device"}
        ok, msg = self.control.set_output_mute(device, mute)
        return {"ok": ok, "error": "" if ok else msg}

    def set_input_mute(self, stable_id: str, mute: bool) -> Dict[str, Any]:
        device = self._lookup_device(stable_id)
        if not device:
            return {"ok": False, "error": "unknown device stable_id"}
        if device.get("device_class") != "input_device":
            return {"ok": False, "error": "not an input device"}
        ok, msg = self.control.set_input_mute(device, mute)
        return {"ok": ok, "error": "" if ok else msg}

    def set_capture_gain(self, stable_id: str, value_percent: int) -> Dict[str, Any]:
        device = self._lookup_device(stable_id)
        if not device:
            return {"ok": False, "error": "unknown device stable_id"}
        return self.set_hardware_gain(stable_id, value_percent=value_percent)

    def set_mic_boost(self, stable_id: str, value_percent: int) -> Dict[str, Any]:
        device = self._lookup_device(stable_id)
        if not device:
            return {"ok": False, "error": "unknown device stable_id"}
        control = device.get("mic_boost_control", "")
        if not control:
            return {"ok": False, "error": "mic boost not available"}
        return self.set_alsa_control(stable_id, control_name=control, value_percent=value_percent)

    def set_hardware_gain(self, stable_id: str, *, value_percent: int | None = None, raw_value: int | None = None) -> Dict[str, Any]:
        device = self._lookup_device(stable_id)
        if not device:
            return {"ok": False, "error": "unknown device stable_id"}
        if device.get("device_class") != "input_device":
            return {"ok": False, "error": "not an input device"}

        control_name = str(device.get("hardware_gain_name", "") or device.get("capture_gain_control", "")).strip()
        if not control_name:
            return {"ok": False, "error": "hardware gain not available"}
        return self.set_alsa_control(stable_id, control_name, value_percent=value_percent, raw_value=raw_value)

    def set_alsa_control(
        self,
        stable_id: str,
        control_name: str,
        *,
        value_percent: int | None = None,
        raw_value: int | None = None,
    ) -> Dict[str, Any]:
        device = self._lookup_device(stable_id)
        if not device:
            return {"ok": False, "error": "unknown device stable_id"}
        if device.get("device_class") != "input_device":
            return {"ok": False, "error": "not an input device"}

        control = self._lookup_allowed_alsa_control(device, control_name)
        if not control:
            return {"ok": False, "error": "control not allowed for device"}
        if not bool(control.get("available", False)):
            return {"ok": False, "error": "control unavailable"}
        if not bool(control.get("has_volume", False)):
            return {"ok": False, "error": "control has no volume"}

        min_raw = control.get("min_raw")
        max_raw = control.get("max_raw")
        ok, msg = self.control.set_alsa_control_value(
            device,
            str(control.get("name", control_name)),
            value_percent=value_percent,
            raw_value=raw_value,
            min_raw=int(min_raw) if isinstance(min_raw, int) else None,
            max_raw=int(max_raw) if isinstance(max_raw, int) else None,
        )
        if not ok:
            return {"ok": False, "error": msg}

        state = self.control.get_alsa_control_state(device, str(control.get("name", control_name)))
        return {"ok": True, "error": "", "control": state}

    def set_alsa_switch(self, stable_id: str, control_name: str, switch_on: bool) -> Dict[str, Any]:
        device = self._lookup_device(stable_id)
        if not device:
            return {"ok": False, "error": "unknown device stable_id"}
        if device.get("device_class") != "input_device":
            return {"ok": False, "error": "not an input device"}

        control = self._lookup_allowed_alsa_control(device, control_name)
        if not control:
            return {"ok": False, "error": "control not allowed for device"}
        if not bool(control.get("available", False)):
            return {"ok": False, "error": "control unavailable"}
        if not bool(control.get("has_switch", False)):
            return {"ok": False, "error": "control has no switch"}

        ok, msg = self.control.set_alsa_switch(device, str(control.get("name", control_name)), bool(switch_on))
        if not ok:
            return {"ok": False, "error": msg}

        state = self.control.get_alsa_control_state(device, str(control.get("name", control_name)))
        return {"ok": True, "error": "", "control": state}

    def test_record_input(self, stable_id: str, duration_sec: float) -> Dict[str, Any]:
        device = self._lookup_device(stable_id)
        if not device:
            return {"ok": False, "error": "unknown device stable_id"}
        if device.get("device_class") != "input_device":
            return {"ok": False, "error": "not an input device"}
        result = self.recorder.record_source(stable_id, device.get("technical_name", ""), duration_sec=duration_sec)
        if not result.ok:
            return {"ok": False, "error": result.error}
        latest = self.recorder.latest_for(stable_id) or {}
        return {
            "ok": True,
            "rms_percent": result.rms_percent,
            "peak_percent": result.peak_percent,
            "loudness_label": result.loudness_label,
            "duration_sec": result.duration_sec,
            "playback_url": f"/api/audio/device/{stable_id}/test-record/latest.wav?ts={latest.get('created_ts','')}",
        }

    def latest_recording(self, stable_id: str) -> Dict[str, Any] | None:
        return self.recorder.latest_for(stable_id)

    def set_stream_volume(self, stream_id: str, volume_percent: int) -> Dict[str, Any]:
        ok, msg = self.control.set_stream_volume(stream_id, volume_percent)
        return {"ok": ok, "error": "" if ok else msg}

    def calibrate_input(self, stable_id: str, duration_sec: float = 4.0) -> Dict[str, Any]:
        device = self._lookup_device(stable_id)
        if not device:
            return {"ok": False, "error": "unknown device stable_id"}
        if device.get("device_class") != "input_device":
            return {"ok": False, "error": "not an input device"}
        return self.calibration.calibrate(device, duration_sec=duration_sec)

    def get_input_calibration(self, stable_id: str) -> Dict[str, Any]:
        item = self.calibration.get_latest(stable_id)
        if item is None:
            return {"ok": False, "error": "no calibration"}
        return {"ok": True, "calibration": item}

    def get_calibration_recording_file(self, stable_id: str) -> str | None:
        return self.calibration.get_latest_recording_file(stable_id)

    def apply_calibration_recommendation(self, stable_id: str) -> Dict[str, Any]:
        device = self._lookup_device(stable_id)
        if not device:
            return {"ok": False, "error": "unknown device stable_id"}
        if device.get("device_class") != "input_device":
            return {"ok": False, "error": "not an input device"}

        latest = self.calibration.get_latest(stable_id)
        if latest is None:
            return {"ok": False, "error": "no calibration"}

        recommendation = latest.get("recommendation", {}) if isinstance(latest, dict) else {}
        if not isinstance(recommendation, dict):
            return {"ok": False, "error": "invalid recommendation"}
        if not bool(recommendation.get("applicable", False)):
            return {"ok": False, "error": "no applicable recommendation"}

        applied: Dict[str, Any] = {}
        errors: list[str] = []

        source_suggested = recommendation.get("suggest_source_volume_percent")
        if isinstance(source_suggested, int):
            result = self.set_input_volume(stable_id, source_suggested)
            if result.get("ok"):
                applied["source_volume_percent"] = source_suggested
            else:
                errors.append(str(result.get("error", "set_input_volume failed")))

        hw_suggested = recommendation.get("suggest_hardware_gain_percent")
        if isinstance(hw_suggested, int):
            result = self.set_hardware_gain(stable_id, value_percent=hw_suggested)
            if result.get("ok"):
                applied["hardware_gain_percent"] = hw_suggested
            else:
                errors.append(str(result.get("error", "set_hardware_gain failed")))

        if not applied:
            return {"ok": False, "error": "nothing applied", "errors": errors}

        return {"ok": True, "applied": applied, "errors": errors}

    # ── Meter runtime control ──────────────────────────────────────────────────

    def meter_status(self) -> Dict[str, Any]:
        s = self.meter.status()
        return {"ok": True, "running": s["running"], "worker_count": s["worker_count"], "autostart": _read_meter_autostart()}

    def meter_start(self) -> Dict[str, Any]:
        self.meter.start()
        return {"ok": True, "running": True}

    def meter_stop(self) -> Dict[str, Any]:
        self.meter.stop()
        return {"ok": True, "running": False}

    def meter_autostart_enable(self) -> Dict[str, Any]:
        _write_meter_autostart(True)
        return {"ok": True, "autostart": True}

    def meter_autostart_disable(self) -> Dict[str, Any]:
        _write_meter_autostart(False)
        return {"ok": True, "autostart": False}

    # Backward-compatible wrappers
    def set_device_volume(self, stable_id: str, volume_percent: int) -> Dict[str, Any]:
        device = self._lookup_device(stable_id)
        if not device:
            return {"ok": False, "error": "unknown device stable_id"}
        if device.get("device_class") == "input_device":
            return self.set_input_volume(stable_id, volume_percent)
        return self.set_output_volume(stable_id, volume_percent)

    def set_device_mute(self, stable_id: str, mute: bool) -> Dict[str, Any]:
        device = self._lookup_device(stable_id)
        if not device:
            return {"ok": False, "error": "unknown device stable_id"}
        if device.get("device_class") == "input_device":
            return self.set_input_mute(stable_id, mute)
        return self.set_output_mute(stable_id, mute)
