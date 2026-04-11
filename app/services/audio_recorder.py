from __future__ import annotations

import array
import math
import os
import shutil
import subprocess
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass(slots=True)
class RecordingResult:
    ok: bool
    error: str
    file_path: str
    duration_sec: float
    rms_percent: int
    peak_percent: int
    loudness_label: str


class AudioRecorderService:
    def __init__(self, storage_dir: str = "/tmp/audio-controller-recordings") -> None:
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._latest: Dict[str, Dict[str, Any]] = {}

    def record_source(self, stable_id: str, source_name: str, duration_sec: float = 3.0) -> RecordingResult:
        if not source_name:
            return RecordingResult(False, "missing source name", "", 0.0, 0, 0, "unbekannt")
        if shutil.which("ffmpeg") is None:
            return RecordingResult(False, "ffmpeg not found", "", 0.0, 0, 0, "unbekannt")

        duration_sec = max(1.0, min(10.0, float(duration_sec)))
        timestamp = int(time.time())
        filename = f"{stable_id}-{timestamp}.wav"
        out_path = self.storage_dir / filename

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "pulse",
            "-i",
            source_name,
            "-t",
            f"{duration_sec}",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(out_path),
        ]

        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=int(duration_sec + 4), check=False)
        if completed.returncode != 0 or not out_path.exists():
            return RecordingResult(
                False,
                completed.stderr.strip() or "recording failed",
                "",
                0.0,
                0,
                0,
                "unbekannt",
            )

        rms, peak = self._analyze_wav(out_path)
        label = self._label(rms, peak)
        record = RecordingResult(True, "", str(out_path), duration_sec, rms, peak, label)
        self._latest[stable_id] = {
            "file_path": str(out_path),
            "filename": filename,
            "duration_sec": duration_sec,
            "rms_percent": rms,
            "peak_percent": peak,
            "loudness_label": label,
            "created_ts": timestamp,
        }
        return record

    def latest_for(self, stable_id: str) -> Dict[str, Any] | None:
        item = self._latest.get(stable_id)
        if not item:
            return None
        if not os.path.exists(item.get("file_path", "")):
            return None
        return item

    def _analyze_wav(self, path: Path) -> tuple[int, int]:
        with wave.open(str(path), "rb") as wf:
            if wf.getsampwidth() != 2:
                return 0, 0
            frames = wf.readframes(wf.getnframes())
        if not frames:
            return 0, 0
        samples = array.array("h")
        samples.frombytes(frames)
        if not samples:
            return 0, 0
        peak = max(abs(v) for v in samples) / 32768.0
        rms = math.sqrt(sum(float(v) * float(v) for v in samples) / len(samples)) / 32768.0
        return int(max(0, min(100, round(rms * 100)))), int(max(0, min(100, round(peak * 100))))

    def _label(self, rms: int, peak: int) -> str:
        if peak >= 95:
            return "potenziell clipping-gefahrdet"
        if rms < 5 and peak < 15:
            return "sehr leise"
        if rms < 18:
            return "okay"
        return "laut"
