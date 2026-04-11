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
            "amixer_scontrols": ["amixer", "scontrols"],
            "amixer_scontents": ["amixer", "scontents"],
        }
        results: Dict[str, CommandResult] = {}
        for key, cmd in commands.items():
            timeout = 6 if key.startswith("pactl_") else 4
            results[key] = self.runner.run(cmd, timeout=timeout)

        pactl_short_sinks = self._parse_pactl_short(results["pactl_short_sinks"].output)
        pactl_short_sources = self._parse_pactl_short(results["pactl_short_sources"].output)

        # Per-device explicit get-volume/get-mute as primary truth fallback
        sink_probe: Dict[str, Dict[str, str]] = {}
        for row in pactl_short_sinks:
            name = row.get("name", "")
            if not name:
                continue
            vol = self.runner.run(["pactl", "get-sink-volume", name], timeout=4)
            mute = self.runner.run(["pactl", "get-sink-mute", name], timeout=4)
            sink_probe[name] = {"volume": vol.output, "mute": mute.output}

        source_probe: Dict[str, Dict[str, str]] = {}
        for row in pactl_short_sources:
            name = row.get("name", "")
            if not name:
                continue
            vol = self.runner.run(["pactl", "get-source-volume", name], timeout=4)
            mute = self.runner.run(["pactl", "get-source-mute", name], timeout=4)
            source_probe[name] = {"volume": vol.output, "mute": mute.output}

        alsa_playback_hw = self._parse_alsa_hw(results["aplay_l"].output)
        alsa_capture_hw = self._parse_alsa_hw(results["arecord_l"].output)
        card_indexes = sorted({row["card_index"] for row in [*alsa_playback_hw, *alsa_capture_hw]})

        amixer_per_card: Dict[str, Dict[str, Any]] = {}
        for card_idx in card_indexes:
            sc = self.runner.run(["amixer", "-c", str(card_idx), "scontrols"], timeout=4)
            scont = self.runner.run(["amixer", "-c", str(card_idx), "scontents"], timeout=5)
            amixer_per_card[str(card_idx)] = {
                "scontrols": sc.output,
                "scontents": scont.output,
                "parsed_controls": self._parse_amixer_scontents(scont.output),
            }

        parsed = {
            "pactl_short_sinks": pactl_short_sinks,
            "pactl_short_sources": pactl_short_sources,
            "pactl_sinks": self._parse_pactl_blocks(results["pactl_sinks"].output, ["Sink", "Senke"]),
            "pactl_sources": self._parse_pactl_blocks(results["pactl_sources"].output, ["Source", "Quelle"]),
            "pactl_sink_inputs": self._parse_pactl_blocks(
                results["pactl_sink_inputs"].output, ["Sink Input", "Wiedergabe-Stream"]
            ),
            "pactl_source_outputs": self._parse_pactl_blocks(
                results["pactl_source_outputs"].output, ["Source Output", "Aufnahme-Stream"]
            ),
            "alsa_playback_hw": alsa_playback_hw,
            "alsa_capture_hw": alsa_capture_hw,
            "wpctl_nodes": self._parse_wpctl_nodes(results["wpctl_status"].output),
            "sink_probe": sink_probe,
            "source_probe": source_probe,
            "amixer_per_card": amixer_per_card,
            "amixer_global_controls": self._parse_amixer_scontents(results["amixer_scontents"].output),
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

    def _parse_pactl_blocks(self, text: str, block_labels: List[str]) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        current: Dict[str, Any] | None = None
        section = ""

        labels = "|".join(re.escape(lbl) for lbl in block_labels)
        block_re = re.compile(rf"^\s*(?:{labels})\s+#(\d+)\s*$", re.IGNORECASE)
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

            if stripped in {"Properties:", "Eigenschaften:"}:
                section = "properties"
                continue
            if stripped in {"Ports:", "Anschl\u00fcsse:"}:
                section = "ports"
                continue
            if re.match(r"^[A-Za-z\u00c4\u00d6\u00dc\u00e4\u00f6\u00fc][A-Za-z\u00c4\u00d6\u00dc\u00e4\u00f6\u00fc \-]+:$", stripped):
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
                key = self._canonical_pactl_key(m.group(1))
                current[key] = m.group(2).strip()

        if current:
            blocks.append(current)
        return blocks

    def _canonical_pactl_key(self, raw_key: str) -> str:
        key = raw_key.strip().lower().replace(" ", "_").replace("-", "_")
        key = key.replace("__", "_")
        mapping = {
            "name": "name",
            "beschreibung": "description",
            "description": "description",
            "treiber": "driver",
            "driver": "driver",
            "status": "state",
            "zustand": "state",
            "state": "state",
            "stumm": "mute",
            "stummgeschaltet": "mute",
            "mute": "mute",
            "lautst\u00e4rke": "volume",
            "volume": "volume",
            "basis_lautst\u00e4rke": "base_volume",
            "basislautst\u00e4rke": "base_volume",
            "basis_lautstaerke": "base_volume",
            "basislautstaerke": "base_volume",
            "base_volume": "base_volume",
            "flags": "flags",
            "merkmale": "flags",
            "abtastspezifikation": "sample_spec",
            "sample_specification": "sample_spec",
            "sample_spec": "sample_spec",
            "kanalzuordnung": "channel_map",
            "channel_map": "channel_map",
            "monitor_von_senke": "monitor_of_sink",
            "monitor_of_sink": "monitor_of_sink",
            "monitor_quelle": "monitor_source",
            "monitor_source": "monitor_source",
            "aktiver_port": "active_port",
            "active_port": "active_port",
            "senke": "sink",
            "sink": "sink",
            "quelle": "source",
            "source": "source",
        }
        return mapping.get(key, key)

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

    def _parse_amixer_scontents(self, text: str) -> List[Dict[str, Any]]:
        controls: List[Dict[str, Any]] = []
        current: Dict[str, Any] | None = None
        start_re = re.compile(r"^Simple mixer control '(.+?)',\d+")
        pct_re = re.compile(r"\[(\d+)%\]")
        db_re = re.compile(r"\[(-?\d+(?:\.\d+)?)dB\]")

        for line in text.splitlines():
            m = start_re.match(line.strip())
            if m:
                if current:
                    controls.append(current)
                name = m.group(1)
                current = {
                    "name": name,
                    "kind": self._classify_amixer_control(name),
                    "percent": None,
                    "db": None,
                    "switch": None,
                    "raw": [line],
                }
                continue
            if not current:
                continue
            current["raw"].append(line)
            p = pct_re.search(line)
            if p and current["percent"] is None:
                current["percent"] = int(p.group(1))
            d = db_re.search(line)
            if d and current["db"] is None:
                current["db"] = float(d.group(1))
            if "[on]" in line and current["switch"] is None:
                current["switch"] = True
            elif "[off]" in line and current["switch"] is None:
                current["switch"] = False

        if current:
            controls.append(current)
        return controls

    def _classify_amixer_control(self, name: str) -> str:
        n = name.lower()
        if "boost" in n:
            return "mic_boost"
        if "capture" in n or "input gain" in n or "digital" in n or "mic" in n:
            return "capture_gain"
        return "unknown_input_control"
