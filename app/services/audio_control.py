from __future__ import annotations

import logging
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
        res = self.runner.run(["pactl", "set-sink-volume", technical_name, f"{volume_percent}%"], timeout=4)
        if res.success:
            logger.info("set output volume %s -> %s%%", technical_name, volume_percent)
            return True, "ok"
        logger.warning("set output volume failed %s: %s", technical_name, res.error)
        return False, res.error or "command failed"

    def set_input_volume(self, device: Dict[str, Any], volume_percent: int) -> Tuple[bool, str]:
        technical_name = device.get("technical_name", "")
        if not technical_name:
            return False, "missing technical_name"
        volume_percent = max(0, min(150, int(volume_percent)))
        res = self.runner.run(["pactl", "set-source-volume", technical_name, f"{volume_percent}%"], timeout=4)
        if res.success:
            logger.info("set input source volume %s -> %s%%", technical_name, volume_percent)
            return True, "ok"
        logger.warning("set input source volume failed %s: %s", technical_name, res.error)
        return False, res.error or "command failed"

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
        card_index = device.get("card_index")
        if card_index is None:
            return False, "no card_index available"
        if not control_name:
            return False, "no gain control available"
        volume_percent = max(0, min(100, int(volume_percent)))
        res = self.runner.run(["amixer", "-c", str(card_index), "sset", control_name, f"{volume_percent}%"], timeout=4)
        if res.success:
            logger.info("set capture gain %s card=%s -> %s%%", control_name, card_index, volume_percent)
            return True, "ok"
        logger.warning("set capture gain failed %s card=%s: %s", control_name, card_index, res.error)
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
