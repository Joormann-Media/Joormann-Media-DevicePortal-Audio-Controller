from __future__ import annotations

from typing import Any, Dict


def build_diagnostics(raw: Dict[str, Any], normalized: Dict[str, Any]) -> Dict[str, Any]:
    commands = raw["commands"]
    return {
        "timestamp_utc": raw["timestamp_utc"],
        "backend_status": {
            "wpctl": commands["wpctl_status"]["success"],
            "pactl": commands["pactl_sinks"]["success"] and commands["pactl_sources"]["success"],
            "alsa": commands["aplay_l"]["success"] or commands["arecord_l"]["success"],
            "wireplumber_or_pw": commands["wpctl_status"]["success"],
        },
        "commands": commands,
        "counts": {
            "output_devices": len(normalized["output_devices"]),
            "input_devices": len(normalized["input_devices"]),
            "monitor_devices": len(normalized["monitor_devices"]),
            "virtual_devices": len(normalized["virtual_devices"]),
            "hidden_diagnostic_only": len(normalized["hidden_diagnostic_only"]),
            "streams": len(normalized["streams"]),
        },
        "raw_sections": {
            "pactl_short_sinks": raw["parsed"]["pactl_short_sinks"],
            "pactl_short_sources": raw["parsed"]["pactl_short_sources"],
            "alsa_playback_hw": raw["parsed"]["alsa_playback_hw"],
            "alsa_capture_hw": raw["parsed"]["alsa_capture_hw"],
            "wpctl_nodes": raw["parsed"]["wpctl_nodes"],
            "alsa_plugin_playback": commands["aplay_L"]["output"],
            "alsa_plugin_capture": commands["arecord_L"]["output"],
        },
    }
