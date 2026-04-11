from __future__ import annotations

import array
import json
import math
import os
import time
import wave
from pathlib import Path
from typing import Any, Dict

from app.services.audio_recorder import AudioRecorderService


class AudioCalibrationService:
    def __init__(
        self,
        recorder: AudioRecorderService,
        storage_dir: str = "/tmp/audio-controller-calibration",
    ) -> None:
        self.recorder = recorder
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def calibrate(self, device: Dict[str, Any], duration_sec: float = 4.0) -> Dict[str, Any]:
        stable_id = str(device.get("stable_id", "")).strip()
        source_name = str(device.get("technical_name", "")).strip()
        if stable_id == "" or source_name == "":
            return {"ok": False, "error": "invalid device"}

        recording = self.recorder.record_source(stable_id, source_name, duration_sec=max(3.0, min(5.0, duration_sec)))
        if not recording.ok:
            return {"ok": False, "error": recording.error}

        analysis = self._analyze_wav(Path(recording.file_path))
        recommendation = self._recommend(device, analysis)
        payload = {
            "success": True,
            "device_stable_id": stable_id,
            "recording_file_url": f"/api/audio/device/{stable_id}/calibration/latest.wav?ts={int(time.time())}",
            "analysis": analysis,
            "rating": recommendation["rating"],
            "message": recommendation["message"],
            "recommendation": recommendation["recommendation"],
            "created_ts": int(time.time()),
            "recording_file_path": recording.file_path,
        }
        self._save(stable_id, payload)

        return {"ok": True, "calibration": payload}

    def get_latest(self, stable_id: str) -> Dict[str, Any] | None:
        path = self._path_for(stable_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            return data
        except Exception:
            return None

    def get_latest_recording_file(self, stable_id: str) -> str | None:
        item = self.get_latest(stable_id)
        if not item:
            return None
        file_path = str(item.get("recording_file_path", "")).strip()
        if file_path == "" or not os.path.exists(file_path):
            return None
        return file_path

    def _save(self, stable_id: str, payload: Dict[str, Any]) -> None:
        path = self._path_for(stable_id)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _path_for(self, stable_id: str) -> Path:
        safe = "".join(ch for ch in stable_id if ch.isalnum() or ch in {"-", "_"})
        safe = safe or "unknown"
        return self.storage_dir / f"{safe}.json"

    def _analyze_wav(self, path: Path) -> Dict[str, Any]:
        with wave.open(str(path), "rb") as wf:
            sample_width = wf.getsampwidth()
            channels = wf.getnchannels()
            frame_rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())

        if sample_width != 2 or not frames:
            return {
                "rms_percent": 0,
                "peak_percent": 0,
                "rms_dbfs": -90.0,
                "peak_dbfs": -90.0,
                "silence_ratio": 1.0,
                "noise_floor_percent": 0,
                "duration_sec": 0.0,
                "channels": channels,
                "sample_rate": frame_rate,
            }

        samples = array.array("h")
        samples.frombytes(frames)
        if not samples:
            return {
                "rms_percent": 0,
                "peak_percent": 0,
                "rms_dbfs": -90.0,
                "peak_dbfs": -90.0,
                "silence_ratio": 1.0,
                "noise_floor_percent": 0,
                "duration_sec": 0.0,
                "channels": channels,
                "sample_rate": frame_rate,
            }

        abs_values = [abs(v) for v in samples]
        peak_lin = max(abs_values) / 32768.0
        rms_lin = math.sqrt(sum(float(v) * float(v) for v in samples) / len(samples)) / 32768.0
        silence_threshold = 300
        silence_ratio = round(sum(1 for v in abs_values if v <= silence_threshold) / len(abs_values), 4)

        # Low percentile as rough noise floor estimate.
        sorted_abs = sorted(abs_values)
        idx = max(0, int(len(sorted_abs) * 0.15) - 1)
        noise_floor_lin = sorted_abs[idx] / 32768.0

        rms_percent = int(max(0, min(100, round(rms_lin * 100))))
        peak_percent = int(max(0, min(100, round(peak_lin * 100))))
        rms_dbfs = round(20.0 * math.log10(max(rms_lin, 1e-6)), 2)
        peak_dbfs = round(20.0 * math.log10(max(peak_lin, 1e-6)), 2)
        noise_floor_percent = int(max(0, min(100, round(noise_floor_lin * 100))))
        duration_sec = round(len(samples) / float(frame_rate), 2) if frame_rate > 0 else 0.0

        return {
            "rms_percent": rms_percent,
            "peak_percent": peak_percent,
            "rms_dbfs": rms_dbfs,
            "peak_dbfs": peak_dbfs,
            "silence_ratio": silence_ratio,
            "noise_floor_percent": noise_floor_percent,
            "duration_sec": duration_sec,
            "channels": channels,
            "sample_rate": frame_rate,
        }

    def _recommend(self, device: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
        rms = int(analysis.get("rms_percent", 0))
        peak = int(analysis.get("peak_percent", 0))
        silence_ratio = float(analysis.get("silence_ratio", 1.0))
        noise_floor = int(analysis.get("noise_floor_percent", 0))

        source_volume = int(device.get("source_volume_percent_current") or 0)
        hw_available = bool(device.get("hardware_gain_available"))
        hw_percent = int(device.get("hardware_gain_percent") or 0)

        rating = "ok"
        message = "Signal ist gut eingepegelt."

        if silence_ratio > 0.94 and peak < 14:
            rating = "insufficient_sample"
            message = "Es wurde kaum Sprache erkannt. Bitte normal sprechen und erneut kalibrieren."
        elif peak >= 98:
            rating = "clipping_risk"
            message = "Signal ist sehr laut. Clipping ist wahrscheinlich."
        elif peak >= 92 or rms >= 55:
            rating = "too_loud"
            message = "Signal ist eher laut."
        elif peak < 28 or rms < 10:
            rating = "too_quiet"
            message = "Signal ist eher leise."
        elif peak < 40 or rms < 15:
            rating = "quiet"
            message = "Signal ist leicht zu leise."

        summary = "Keine automatische Anpassung empfohlen."
        suggest_source: int | None = None
        suggest_hw: int | None = None

        if rating in {"too_quiet", "quiet"}:
            if hw_available and hw_percent < 95:
                suggest_hw = min(100, hw_percent + (12 if rating == "too_quiet" else 6))
                summary = "Hardware Gain leicht erhöhen."
            elif source_volume < 125:
                suggest_source = min(130, source_volume + (12 if rating == "too_quiet" else 6))
                summary = "Mikrofon-Lautstärke (Source) leicht erhöhen."
            else:
                summary = "Verstärkung ist bereits hoch. Bitte Mikrofonabstand und Position prüfen."

        if rating in {"too_loud", "clipping_risk"}:
            if source_volume > 85:
                suggest_source = max(55, source_volume - (12 if rating == "clipping_risk" else 8))
                summary = "Mikrofon-Lautstärke (Source) reduzieren."
            if hw_available and hw_percent > 10 and rating == "clipping_risk":
                suggest_hw = max(0, hw_percent - 10)
                if suggest_source is not None:
                    summary = "Source-Lautstärke senken, danach Hardware Gain leicht reduzieren."
                else:
                    summary = "Hardware Gain leicht reduzieren."

        if rating == "ok":
            summary = "Signal ist gut. Keine Anpassung nötig."

        if noise_floor >= 8 and rms < 20:
            message += " Erhöhtes Grundrauschen erkannt."

        applicable = suggest_source is not None or suggest_hw is not None
        return {
            "rating": rating,
            "message": message,
            "recommendation": {
                "summary": summary,
                "suggest_source_volume_percent": suggest_source,
                "suggest_hardware_gain_percent": suggest_hw,
                "applicable": applicable,
            },
        }
