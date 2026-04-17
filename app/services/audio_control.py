from __future__ import annotations

import logging
import re
from typing import Any, Dict, Tuple

from app.services.command_runner import CommandRunner

logger = logging.getLogger(__name__)


class AudioControlService:
    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or CommandRunner()

    def set_default(self, device: Dict[str, Any]) -> Tuple[bool, str]:
        technical_name = device.get("technical_name", "")
        if not technical_name:
            return False, "missing technical_name"

        if device.get("device_class") == "input_device":
            res = self.runner.run(["pactl", "set-default-source", technical_name], timeout=4)
        else:
            res = self.runner.run(["pactl", "set-default-sink", technical_name], timeout=4)

        if res.success:
            return True, "ok"
        return False, res.error or "command failed"

    def set_output_volume(self, device: Dict[str, Any], volume_percent: int) -> Tuple[bool, str]:
        technical_name = device.get("technical_name", "")
        if not technical_name:
            return False, "missing technical_name"
        volume_percent = max(0, min(150, int(volume_percent)))
        if not self._is_alsa_fallback_device(device):
            res = self.runner.run(["pactl", "set-sink-volume", technical_name, f"{volume_percent}%"], timeout=4)
            if res.success:
                logger.info("set output volume %s -> %s%%", technical_name, volume_percent)
                return True, "ok"
            logger.warning("set output volume failed %s: %s", technical_name, res.error)

        # ALSA fallback for systems without working Pulse/PipeWire session.
        ok, msg = self._set_alsa_volume(device, volume_percent=volume_percent, input_device=False)
        if ok:
            return True, "ok"
        return False, msg

    def set_input_volume(self, device: Dict[str, Any], volume_percent: int) -> Tuple[bool, str]:
        technical_name = device.get("technical_name", "")
        if not technical_name:
            return False, "missing technical_name"
        volume_percent = max(0, min(150, int(volume_percent)))
        if not self._is_alsa_fallback_device(device):
            res = self.runner.run(["pactl", "set-source-volume", technical_name, f"{volume_percent}%"], timeout=4)
            if res.success:
                logger.info("set input source volume %s -> %s%%", technical_name, volume_percent)
                return True, "ok"
            logger.warning("set input source volume failed %s: %s", technical_name, res.error)

        ok, msg = self._set_alsa_volume(device, volume_percent=volume_percent, input_device=True)
        if ok:
            return True, "ok"
        return False, msg

    def set_output_mute(self, device: Dict[str, Any], mute: bool) -> Tuple[bool, str]:
        technical_name = device.get("technical_name", "")
        if not technical_name:
            return False, "missing technical_name"
        res = self.runner.run(["pactl", "set-sink-mute", technical_name, "1" if mute else "0"], timeout=4)
        if res.success:
            return True, "ok"
        return False, res.error or "command failed"

    def set_input_mute(self, device: Dict[str, Any], mute: bool) -> Tuple[bool, str]:
        technical_name = device.get("technical_name", "")
        if not technical_name:
            return False, "missing technical_name"
        res = self.runner.run(["pactl", "set-source-mute", technical_name, "1" if mute else "0"], timeout=4)
        if res.success:
            return True, "ok"
        return False, res.error or "command failed"

    def set_input_hw_gain(self, device: Dict[str, Any], volume_percent: int, control_name: str) -> Tuple[bool, str]:
        return self.set_alsa_control_value(device, control_name, value_percent=volume_percent)

    def set_alsa_control_value(
        self,
        device: Dict[str, Any],
        control_name: str,
        *,
        value_percent: int | None = None,
        raw_value: int | None = None,
        min_raw: int | None = None,
        max_raw: int | None = None,
    ) -> Tuple[bool, str]:
        card_index = device.get("card_index")
        if card_index is None:
            return False, "no card_index available"
        if not control_name:
            return False, "missing control_name"

        value_token = ""
        if raw_value is not None:
            if min_raw is not None:
                raw_value = max(min_raw, raw_value)
            if max_raw is not None:
                raw_value = min(max_raw, raw_value)
            value_token = str(int(raw_value))
        elif value_percent is not None:
            value_percent = max(0, min(100, int(value_percent)))
            value_token = f"{value_percent}%"
        else:
            return False, "value required"

        res = self.runner.run(["amixer", "-c", str(card_index), "sset", control_name, value_token], timeout=4)
        if res.success:
            logger.info("set alsa control %s card=%s -> %s", control_name, card_index, value_token)
            return True, "ok"
        logger.warning("set alsa control failed %s card=%s: %s", control_name, card_index, res.error)
        return False, res.error or "command failed"

    def set_alsa_switch(self, device: Dict[str, Any], control_name: str, switch_on: bool) -> Tuple[bool, str]:
        card_index = device.get("card_index")
        if card_index is None:
            return False, "no card_index available"
        if not control_name:
            return False, "missing control_name"
        value_token = "on" if switch_on else "off"
        res = self.runner.run(["amixer", "-c", str(card_index), "sset", control_name, value_token], timeout=4)
        if res.success:
            logger.info("set alsa switch %s card=%s -> %s", control_name, card_index, value_token)
            return True, "ok"
        logger.warning("set alsa switch failed %s card=%s: %s", control_name, card_index, res.error)
        return False, res.error or "command failed"

    def get_alsa_control_state(self, device: Dict[str, Any], control_name: str) -> Dict[str, Any]:
        card_index = device.get("card_index")
        if card_index is None or not control_name:
            return {}
        res = self.runner.run(["amixer", "-c", str(card_index), "sget", control_name], timeout=4)
        if not res.success:
            return {}
        return self._parse_amixer_sget(control_name, res.output)

    def set_stream_volume(self, stream_id: str, volume_percent: int) -> Tuple[bool, str]:
        if not stream_id.startswith("sink-input-"):
            return False, "unsupported stream id"
        index = stream_id.replace("sink-input-", "", 1)
        if not index.isdigit():
            return False, "invalid stream id"
        volume_percent = max(0, min(150, int(volume_percent)))
        res = self.runner.run(["pactl", "set-sink-input-volume", index, f"{volume_percent}%"], timeout=4)
        if res.success:
            return True, "ok"
        return False, res.error or "command failed"

    def _parse_amixer_sget(self, control_name: str, text: str) -> Dict[str, Any]:
        pct_re = re.compile(r"\[(\d+)%\]")
        db_re = re.compile(r"\[(-?\d+(?:\.\d+)?)dB\]")
        raw_re = re.compile(r"\bCapture\s+(-?\d+)\b", re.IGNORECASE)
        limits_re = re.compile(r"^\s*(?:Limits|Grenzen):\s*(?:Capture|Playback)?\s*(-?\d+)\s*-\s*(-?\d+)\s*$", re.IGNORECASE)
        cap_re = re.compile(r"^\s*(?:Capabilities|Fähigkeiten):\s*(.+)$", re.IGNORECASE)
        channel_re = re.compile(r"^\s*(Capture channels|Playback channels|Aufnahmekanäle|Wiedergabekanäle):\s*(.+)$", re.IGNORECASE)

        caps: list[str] = []
        mode = ""
        min_raw: int | None = None
        max_raw: int | None = None
        raws: list[int] = []
        pcts: list[int] = []
        dbs: list[float] = []
        switch_on: bool | None = None

        for line in (text or "").splitlines():
            s = line.strip()
            if not s:
                continue
            m = cap_re.match(s)
            if m:
                caps = [t.strip().lower() for t in m.group(1).split() if t.strip()]
                continue
            m = channel_re.match(s)
            if m:
                mode = m.group(2).strip()
                continue
            m = limits_re.match(s)
            if m:
                min_raw = int(m.group(1))
                max_raw = int(m.group(2))
                continue
            for rm in raw_re.finditer(s):
                raws.append(int(rm.group(1)))
            for pm in pct_re.finditer(s):
                pcts.append(int(pm.group(1)))
            for dm in db_re.finditer(s):
                dbs.append(float(dm.group(1)))
            if "[on]" in s:
                switch_on = True
            if "[off]" in s:
                switch_on = False

        has_volume = any(token in caps for token in ["cvolume", "pvolume", "volume", "cvolume-joined", "pvolume-joined", "volume-joined"])
        has_switch = any(token in caps for token in ["cswitch", "pswitch", "switch", "cswitch-joined", "pswitch-joined"])

        raw_value = int(round(sum(raws) / len(raws))) if raws else None
        percent = int(round(sum(pcts) / len(pcts))) if pcts else None
        db = round(sum(dbs) / len(dbs), 2) if dbs else None

        return {
            "name": control_name,
            "available": bool(has_volume or has_switch),
            "has_volume": has_volume,
            "has_switch": has_switch,
            "min_raw": min_raw,
            "max_raw": max_raw,
            "raw_value": raw_value,
            "percent": percent,
            "db": db,
            "switch_on": switch_on,
            "channel_mode": mode,
            "capabilities": caps,
        }

    def _is_alsa_fallback_device(self, device: Dict[str, Any]) -> bool:
        technical_name = str(device.get("technical_name", ""))
        if technical_name.startswith("alsa:hw:"):
            return True
        return "alsa_fallback_only" in (device.get("diagnostic_flags", []) or [])

    def _device_card_index(self, device: Dict[str, Any]) -> int | None:
        card_index = device.get("card_index")
        if isinstance(card_index, int):
            return card_index
        technical_name = str(device.get("technical_name", ""))
        m = re.match(r"^alsa:hw:(\d+),(\d+)$", technical_name)
        if not m:
            return None
        return int(m.group(1))

    def _set_alsa_volume(self, device: Dict[str, Any], *, volume_percent: int, input_device: bool) -> Tuple[bool, str]:
        card_index = self._device_card_index(device)
        if card_index is None:
            return False, "no card_index available"
        alsa_percent = max(0, min(100, int(volume_percent)))
        names = self._candidate_alsa_controls(device, input_device=input_device)
        if not names:
            return False, "no suitable alsa control found"

        last_error = "command failed"
        for control_name in names:
            res = self.runner.run(["amixer", "-c", str(card_index), "sset", control_name, f"{alsa_percent}%"], timeout=4)
            if res.success:
                logger.info(
                    "set alsa %s volume card=%s control=%s -> %s%%",
                    "input" if input_device else "output",
                    card_index,
                    control_name,
                    alsa_percent,
                )
                return True, "ok"
            if res.error:
                last_error = res.error
        logger.warning(
            "set alsa %s volume failed card=%s controls=%s err=%s",
            "input" if input_device else "output",
            card_index,
            ",".join(names),
            last_error,
        )
        return False, last_error

    def _candidate_alsa_controls(self, device: Dict[str, Any], *, input_device: bool) -> list[str]:
        controls = device.get("hw_controls", []) or device.get("alsa_controls", []) or []
        candidates: list[str] = []
        seen: set[str] = set()

        def add(name: str) -> None:
            n = str(name).strip()
            if not n:
                return
            key = n.lower()
            if key in seen:
                return
            seen.add(key)
            candidates.append(n)

        if input_device:
            add(str(device.get("hardware_gain_name", "")))
            add(str(device.get("capture_gain_control", "")))
            add(str(device.get("mic_boost_control", "")))
        for c in controls:
            if not bool(c.get("has_volume", False)):
                continue
            name = str(c.get("name", "")).strip()
            if not name:
                continue
            lname = name.lower()
            if input_device:
                score_keys = ["capture", "mic", "input gain", "digital", "boost", "line"]
            else:
                score_keys = ["master", "pcm", "speaker", "headphone", "digital", "lineout", "line out"]
            if any(k in lname for k in score_keys):
                add(name)
        if input_device:
            for n in ["Capture", "Mic", "Input Gain", "Digital", "Mic Boost", "Line"]:
                add(n)
        else:
            for n in ["Master", "PCM", "Speaker", "Headphone", "Digital", "Line Out", "Lineout"]:
                add(n)
        return candidates
