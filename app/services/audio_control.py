from __future__ import annotations

from typing import Any, Dict, Tuple

from app.services.command_runner import CommandRunner


class AudioControlService:
    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or CommandRunner()

    def set_default(self, device: Dict[str, Any]) -> Tuple[bool, str]:
        technical_name = device.get("technical_name", "")
        if not technical_name:
            return False, "missing technical_name"

        # Prefer pactl by stable technical name; fallback to wpctl defaults only if needed.
        if device.get("device_class") == "input_device":
            res = self.runner.run(["pactl", "set-default-source", technical_name], timeout=4)
        else:
            res = self.runner.run(["pactl", "set-default-sink", technical_name], timeout=4)

        if res.success:
            return True, "ok"
        return False, res.error or "command failed"

    def set_volume(self, device: Dict[str, Any], volume_percent: int) -> Tuple[bool, str]:
        technical_name = device.get("technical_name", "")
        if not technical_name:
            return False, "missing technical_name"
        volume_percent = max(0, min(150, int(volume_percent)))
        if device.get("device_class") == "input_device":
            res = self.runner.run(["pactl", "set-source-volume", technical_name, f"{volume_percent}%"], timeout=4)
        else:
            res = self.runner.run(["pactl", "set-sink-volume", technical_name, f"{volume_percent}%"], timeout=4)
        if res.success:
            return True, "ok"
        return False, res.error or "command failed"

    def set_mute(self, device: Dict[str, Any], mute: bool) -> Tuple[bool, str]:
        technical_name = device.get("technical_name", "")
        if not technical_name:
            return False, "missing technical_name"
        token = "1" if mute else "0"
        if device.get("device_class") == "input_device":
            res = self.runner.run(["pactl", "set-source-mute", technical_name, token], timeout=4)
        else:
            res = self.runner.run(["pactl", "set-sink-mute", technical_name, token], timeout=4)
        if res.success:
            return True, "ok"
        return False, res.error or "command failed"

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
