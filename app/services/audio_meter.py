from __future__ import annotations

import array
import math
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, List


@dataclass(slots=True)
class _CaptureWorker:
    source_name: str
    process: subprocess.Popen[bytes]
    last_rms_percent: int = 0
    last_peak_percent: int = 0
    last_error: str = ""
    last_sample_ts: float = 0.0
    last_requested_ts: float = field(default_factory=time.time)


class AudioMeterService:
    def __init__(self) -> None:
        self._lock = Lock()
        self._workers: Dict[str, _CaptureWorker] = {}
        self._chunk_bytes = 3200  # 16000 Hz, s16 mono, ~100ms
        self._stale_seconds = 6.0
        self._max_sources = 6

    def get_meters(self, devices: List[Dict[str, Any]]) -> Dict[str, Any]:
        with self._lock:
            self._cleanup_stale_workers()
            if shutil.which("ffmpeg") is None:
                return {
                    device["stable_id"]: {
                        "available": False,
                        "reason": "ffmpeg_not_found",
                        "rms_percent": 0,
                        "peak_percent": 0,
                    }
                    for device in devices
                }

            meters: Dict[str, Any] = {}
            selected_sources: List[str] = []
            for device in devices:
                source_name = self._source_for_meter(device)
                if not source_name:
                    meters[device["stable_id"]] = {
                        "available": False,
                        "reason": "no_source",
                        "rms_percent": 0,
                        "peak_percent": 0,
                    }
                    continue
                if source_name not in selected_sources:
                    selected_sources.append(source_name)
                if len(selected_sources) >= self._max_sources:
                    break

            for source_name in selected_sources:
                self._ensure_worker(source_name)

            for device in devices:
                source_name = self._source_for_meter(device)
                if not source_name:
                    continue
                worker = self._workers.get(source_name)
                if worker is None:
                    meters[device["stable_id"]] = {
                        "available": False,
                        "reason": "worker_unavailable",
                        "rms_percent": 0,
                        "peak_percent": 0,
                    }
                    continue
                worker.last_requested_ts = time.time()
                age = time.time() - worker.last_sample_ts
                if worker.last_sample_ts <= 0 or age > 2.5:
                    meters[device["stable_id"]] = {
                        "available": False,
                        "reason": worker.last_error or "no_live_samples",
                        "rms_percent": 0,
                        "peak_percent": 0,
                    }
                    continue
                meters[device["stable_id"]] = {
                    "available": True,
                    "reason": "ok",
                    "rms_percent": worker.last_rms_percent,
                    "peak_percent": worker.last_peak_percent,
                }

            return meters

    def _source_for_meter(self, device: Dict[str, Any]) -> str:
        dclass = device.get("device_class")
        if dclass == "input_device":
            return device.get("technical_name", "")
        if dclass == "output_device":
            return device.get("monitor_source_name", "")
        return ""

    def _ensure_worker(self, source_name: str) -> None:
        worker = self._workers.get(source_name)
        if worker is None or worker.process.poll() is not None:
            self._stop_worker(source_name)
            self._workers[source_name] = self._start_worker(source_name)
            worker = self._workers[source_name]

        self._read_worker_chunk(worker)

    def _start_worker(self, source_name: str) -> _CaptureWorker:
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostats",
            "-f",
            "pulse",
            "-i",
            source_name,
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "s16le",
            "-",
        ]
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            bufsize=0,
        )
        if process.stdout is not None:
            os.set_blocking(process.stdout.fileno(), False)
        return _CaptureWorker(source_name=source_name, process=process)

    def _read_worker_chunk(self, worker: _CaptureWorker) -> None:
        stdout = worker.process.stdout
        if stdout is None:
            worker.last_error = "no_stdout"
            return

        try:
            fd = stdout.fileno()
            data = os.read(fd, self._chunk_bytes)
        except BlockingIOError:
            return
        except Exception as exc:  # pragma: no cover
            worker.last_error = str(exc)[:120]
            return

        if not data:
            err = b""
            if worker.process.stderr is not None:
                try:
                    err = worker.process.stderr.read() or b""
                except Exception:
                    err = b""
            worker.last_error = err.decode("utf-8", errors="ignore").strip()[:140] or "capture_eof"
            return

        samples = array.array("h")
        samples.frombytes(data)
        if not samples:
            worker.last_error = "no_samples"
            return

        peak = max(abs(v) for v in samples) / 32768.0
        rms = math.sqrt(sum(float(v) * float(v) for v in samples) / len(samples)) / 32768.0
        worker.last_peak_percent = int(max(0, min(100, round(peak * 100))))
        worker.last_rms_percent = int(max(0, min(100, round(rms * 100))))
        worker.last_sample_ts = time.time()
        worker.last_error = ""

    def _cleanup_stale_workers(self) -> None:
        now = time.time()
        stale = [name for name, worker in self._workers.items() if (now - worker.last_requested_ts) > self._stale_seconds]
        for name in stale:
            self._stop_worker(name)

    def _stop_worker(self, source_name: str) -> None:
        worker = self._workers.pop(source_name, None)
        if worker is None:
            return
        try:
            worker.process.terminate()
            worker.process.wait(timeout=0.35)
        except Exception:
            try:
                worker.process.kill()
            except Exception:
                pass
