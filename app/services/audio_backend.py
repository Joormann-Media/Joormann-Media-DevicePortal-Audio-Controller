from __future__ import annotations

import re
import socket
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.services.command_runner import CommandResult, CommandRunner


class AudioBackend:
    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or CommandRunner()

    def collect_raw(self) -> Dict[str, Any]:
        commands = {
            "wpctl_status": ["wpctl", "status"],
            "pactl_short_sinks": ["pactl", "list", "short", "sinks"],
            "pactl_short_sources": ["pactl", "list", "short", "sources"],
            "pactl_sinks": ["pactl", "list", "sinks"],
            "pactl_sources": ["pactl", "list", "sources"],
            "pactl_sink_inputs": ["pactl", "list", "sink-inputs"],
            "pactl_source_outputs": ["pactl", "list", "source-outputs"],
            "pactl_default_sink": ["pactl", "get-default-sink"],
            "pactl_default_source": ["pactl", "get-default-source"],
            "aplay_l": ["aplay", "-l"],
            "arecord_l": ["arecord", "-l"],
            "aplay_L": ["aplay", "-L"],
            "arecord_L": ["arecord", "-L"],
            "proc_asound_cards": ["cat", "/proc/asound/cards"],
        }
        results: Dict[str, CommandResult] = {}
        for key, cmd in commands.items():
            timeout = 5 if key.startswith("pactl_") else 4
            results[key] = self.runner.run(cmd, timeout=timeout)

        parsed = {
            "pactl_short_sinks": self._parse_pactl_short(results["pactl_short_sinks"].output),
            "pactl_short_sources": self._parse_pactl_short(results["pactl_short_sources"].output),
            "pactl_sinks": self._parse_pactl_blocks(results["pactl_sinks"].output, "Sink"),
            "pactl_sources": self._parse_pactl_blocks(results["pactl_sources"].output, "Source"),
            "pactl_sink_inputs": self._parse_pactl_blocks(results["pactl_sink_inputs"].output, "Sink Input"),
            "pactl_source_outputs": self._parse_pactl_blocks(results["pactl_source_outputs"].output, "Source Output"),
            "alsa_playback_hw": self._parse_alsa_hw(results["aplay_l"].output),
            "alsa_capture_hw": self._parse_alsa_hw(results["arecord_l"].output),
            "wpctl_nodes": self._parse_wpctl_nodes(results["wpctl_status"].output),
        }

        defaults = {
            "sink": results["pactl_default_sink"].output.strip(),
            "source": results["pactl_default_source"].output.strip(),
        }

        return {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "hostname": socket.gethostname(),
            "defaults": defaults,
            "parsed": parsed,
            "commands": {
                name: {
                    "command": result.command,
                    "success": result.success,
                    "return_code": result.return_code,
                    "error": result.error,
                    "has_output": bool(result.output),
                    "output": result.output,
                }
                for name, result in results.items()
            },
        }

    def _parse_pactl_short(self, text: str) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            cols = [c.strip() for c in line.split("\t")]
            if len(cols) < 2:
                continue
            out.append(
                {
                    "index": cols[0],
                    "name": cols[1],
                    "driver": cols[2] if len(cols) > 2 else "",
                    "sample_spec": cols[3] if len(cols) > 3 else "",
                    "state": cols[4] if len(cols) > 4 else "",
                    "raw": line,
                }
            )
        return out

    def _parse_pactl_blocks(self, text: str, block_label: str) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        current: Dict[str, Any] | None = None
        section = ""

        block_re = re.compile(rf"^\s*{re.escape(block_label)}\s+#(\d+)\s*$")
        key_value_re = re.compile(r"^\s*([^:]+):\s*(.*)$")
        prop_re = re.compile(r'^\s*([^=]+)=\s*"?(.*?)"?\s*$')
        port_re = re.compile(r"^\s*([^:]+):\s*(.+)$")

        for line in text.splitlines():
            block_match = block_re.match(line)
            if block_match:
                if current:
                    blocks.append(current)
                current = {
                    "index": block_match.group(1),
                    "properties": {},
                    "ports": {},
                    "raw_lines": [line],
                }
                section = ""
                continue

            if current is None:
                continue

            current["raw_lines"].append(line)
            stripped = line.strip()
            if not stripped:
                continue

            if stripped == "Properties:":
                section = "properties"
                continue
            if stripped == "Ports:":
                section = "ports"
                continue
            if re.match(r"^[A-Za-z][A-Za-z \-]+:$", stripped):
                section = ""

            if section == "properties":
                m = prop_re.match(stripped)
                if m:
                    current["properties"][m.group(1).strip()] = m.group(2).strip()
                continue

            if section == "ports":
                m = port_re.match(stripped)
                if m:
                    current["ports"][m.group(1).strip()] = m.group(2).strip()
                continue

            m = key_value_re.match(line)
            if m:
                key = m.group(1).strip().lower().replace(" ", "_")
                current[key] = m.group(2).strip()

        if current:
            blocks.append(current)
        return blocks

    def _parse_alsa_hw(self, text: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        pattern = re.compile(
            r"card\s+(\d+):\s+([^\[]+)\[([^\]]+)\],\s+device\s+(\d+):\s+([^\[]+)\[([^\]]+)\]",
            re.IGNORECASE,
        )
        for line in text.splitlines():
            m = pattern.search(line)
            if not m:
                continue
            card_idx, card_short, card_name, dev_idx, dev_short, dev_name = [x.strip() for x in m.groups()]
            out.append(
                {
                    "card_index": int(card_idx),
                    "card_short": card_short,
                    "card_name": card_name,
                    "device_index": int(dev_idx),
                    "device_short": dev_short,
                    "device_name": dev_name,
                    "raw": line.strip(),
                }
            )
        return out

    def _parse_wpctl_nodes(self, text: str) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        section = ""
        node_re = re.compile(r"^\s*(?:\*\s*)?(\d+)\.\s+(.+?)(?:\s+\[vol:.*)?$")
        for raw in text.splitlines():
            line = raw.strip()
            if "Sinks:" in line:
                section = "sink"
                continue
            if "Sources:" in line:
                section = "source"
                continue
            if not section:
                continue
            clean = re.sub(r"^[\|\s├─└]+", "", raw).strip()
            m = node_re.match(clean)
            if not m:
                continue
            out.append({"wpctl_id": m.group(1), "name": m.group(2).strip(), "kind": section})
        return out
