from __future__ import annotations

import array
import math
import shutil
import subprocess
import time
from threading import Lock
from typing import Any, Dict, List


class AudioMeterService:
    def __init__(self) -> None:
        self._cache: Dict[str, Any] = {"timestamp": 0.0, "meters": {}}
        self._lock = Lock()

    def get_meters(self, devices: List[Dict[str, Any]], ttl_seconds: float = 0.9) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            if now - float(self._cache.get("timestamp", 0.0)) < ttl_seconds:
                return self._cache["meters"]

            meters: Dict[str, Any] = {}
            for device in devices[:8]:
                source_name = self._source_for_meter(device)
                if not source_name:
                    meters[device["stable_id"]] = {
                        "available": False,
                        "reason": "no_source",
                        "rms_percent": 0,
                        "peak_percent": 0,
                    }
                    continue
                meters[device["stable_id"]] = self._measure_source(source_name)

            self._cache = {"timestamp": now, "meters": meters}
            return meters

    def _source_for_meter(self, device: Dict[str, Any]) -> str:
        dclass = device.get("device_class")
        if dclass == "input_device":
            return device.get("technical_name", "")
        if dclass == "output_device":
            return device.get("monitor_source_name", "")
        return ""

    def _measure_source(self, source_name: str) -> Dict[str, Any]:
        if shutil.which("ffmpeg") is None:
            return {
                "available": False,
                "reason": "ffmpeg_not_found",
                "rms_percent": 0,
                "peak_percent": 0,
            }

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "pulse",
            "-i",
            source_name,
            "-t",
            "0.20",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "s16le",
            "-",
        ]

        try:
            completed = subprocess.run(cmd, capture_output=True, timeout=2.2, check=False)
        except Exception as exc:  # pragma: no cover
            return {
                "available": False,
                "reason": str(exc),
                "rms_percent": 0,
                "peak_percent": 0,
            }

        if completed.returncode != 0 or not completed.stdout:
            return {
                "available": False,
                "reason": (completed.stderr or b"").decode("utf-8", errors="ignore").strip()[:140] or "capture_failed",
                "rms_percent": 0,
                "peak_percent": 0,
            }

        samples = array.array("h")
        samples.frombytes(completed.stdout)
        if not samples:
            return {
                "available": False,
                "reason": "no_samples",
                "rms_percent": 0,
                "peak_percent": 0,
            }

        peak = max(abs(v) for v in samples) / 32768.0
        rms = math.sqrt(sum(float(v) * float(v) for v in samples) / len(samples)) / 32768.0
        return {
            "available": True,
            "reason": "ok",
            "rms_percent": int(max(0, min(100, round(rms * 100)))),
            "peak_percent": int(max(0, min(100, round(peak * 100)))),
        }
