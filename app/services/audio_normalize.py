from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Tuple

from app.models.audio_models import AudioDevice, AudioStream

PLUGIN_KEYWORDS = {
    "null",
    "lavrate",
    "samplerate",
    "speexrate",
    "jack",
    "oss",
    "pipewire",
    "pulse",
    "speex",
    "upmix",
    "vdownmix",
    "default",
    "sysdefault",
    "dmix",
    "dsnoop",
    "usbstream",
    "plughw",
    "surround",
    "front",
}


def _first_percent(text: str) -> int | None:
    m = re.search(r"(\d+)%", text or "")
    if not m:
        return None
    return max(0, min(150, int(m.group(1))))


def _parse_state(raw_state: str) -> str:
    state = (raw_state or "").strip().lower()
    if "running" in state:
        return "running"
    if "idle" in state:
        return "idle"
    if "suspend" in state:
        return "suspended"
    if "unavailable" in state:
        return "unavailable"
    return state or "unknown"


def _is_monitor(name: str, description: str, monitor_of_sink: str) -> bool:
    s = f"{name} {description} {monitor_of_sink}".lower()
    return ".monitor" in s or "monitor of sink" in s or "monitor" in (name or "").lower()


def _looks_like_plugin(name: str, description: str) -> bool:
    s = f"{name} {description}".lower().replace("_", "-")
    for keyword in PLUGIN_KEYWORDS:
        if keyword in s:
            return True
    if s.startswith("hw:") or s.startswith("plughw:"):
        return True
    return False


def _bus_type(name: str, description: str, props: Dict[str, str]) -> str:
    text = " ".join([name, description, props.get("device.bus", ""), props.get("device.api", "")]).lower()
    if "bluez" in text or "bluetooth" in text:
        return "bluetooth"
    if "usb" in text:
        return "usb"
    if "hdmi" in text:
        return "hdmi"
    if "displayport" in text or "dp " in text or " dp" in text:
        return "displayport"
    if "analog" in text or "lineout" in text or "headphone" in text or "mic" in text:
        return "analog"
    if "pci" in text or "hda" in text or "onboard" in text:
        return "pci"
    if "virtual" in text or "null" in text:
        return "virtual"
    return "unknown"


def _connection_label(active_port: str, bus: str, description: str) -> str:
    port = (active_port or "").lower()
    desc = (description or "").lower()
    if "hdmi" in port or "hdmi" in desc:
        return "HDMI"
    if "displayport" in port or "displayport" in desc:
        return "DisplayPort"
    if "headphone" in port:
        return "Headphone"
    if "lineout" in port or "line-out" in port:
        return "Line Out"
    if "mic" in port:
        return "Mic"
    if bus == "usb":
        return "USB"
    if bus == "bluetooth":
        return "Bluetooth"
    if bus == "analog":
        return "Analog"
    if bus == "pci":
        return "Onboard/PCI"
    return "Unknown"


def _stable_id(kind: str, technical_name: str, card_name: str) -> str:
    base = f"{kind}|{technical_name}|{card_name}".encode("utf-8")
    return f"{kind[:3]}-{hashlib.sha1(base).hexdigest()[:14]}"


def _friendly_name(description: str, technical_name: str) -> str:
    if description and description.lower() != technical_name.lower():
        return description
    tech = technical_name
    tech = tech.replace("alsa_output.", "").replace("alsa_input.", "")
    tech = tech.replace(".analog-stereo", " Analog Stereo")
    tech = tech.replace(".hdmi-stereo", " HDMI Stereo")
    tech = tech.replace("_", " ")
    return tech


def _match_alsa_card(card_name: str, alsa_hw: List[Dict[str, Any]]) -> Tuple[int | None, int | None, bool]:
    if not card_name:
        return None, None, False
    needle = card_name.lower()
    for row in alsa_hw:
        all_text = f"{row['card_short']} {row['card_name']} {row['device_short']} {row['device_name']}".lower()
        if needle in all_text or row["card_short"].lower() in needle:
            return row["card_index"], row["device_index"], True
    return None, None, False


def normalize_audio(raw: Dict[str, Any]) -> Dict[str, Any]:
    parsed = raw["parsed"]
    defaults = raw["defaults"]

    sink_by_name = {s.get("name", ""): s for s in parsed["pactl_sinks"] if s.get("name")}
    source_by_name = {s.get("name", ""): s for s in parsed["pactl_sources"] if s.get("name")}

    wpctl_name_to_id = {row["name"]: row["wpctl_id"] for row in parsed["wpctl_nodes"]}

    output_devices: List[AudioDevice] = []
    input_devices: List[AudioDevice] = []
    monitor_devices: List[AudioDevice] = []
    virtual_devices: List[AudioDevice] = []
    hidden_diagnostic_only: List[AudioDevice] = []

    for sink in parsed["pactl_sinks"]:
        tech_name = sink.get("name", "")
        props = sink.get("properties", {})
        desc = sink.get("description", props.get("device.description", ""))
        looks_plugin = _looks_like_plugin(tech_name, desc)
        bus = _bus_type(tech_name, desc, props)
        card_name = props.get("alsa.card_name", props.get("device.product.name", ""))
        card_idx, dev_idx, hw_present = _match_alsa_card(card_name, parsed["alsa_playback_hw"])
        state = _parse_state(sink.get("state", ""))

        device = AudioDevice(
            stable_id=_stable_id("output_device", tech_name, card_name),
            device_class="output_device",
            display_name=_friendly_name(desc, tech_name),
            technical_name=tech_name,
            backend_ids={"pactl": str(sink.get("index", "")), "wpctl": wpctl_name_to_id.get(desc, "")},
            card_name=card_name,
            card_index=card_idx,
            device_index=dev_idx,
            bus_type=bus,
            connection_label=_connection_label(sink.get("active_port", ""), bus, desc),
            profile=props.get("device.profile.description", ""),
            ports=[p for p in list(sink.get("ports", {}).keys()) if p.lower() != "active port"],
            state=state,
            default=(tech_name == defaults.get("sink")),
            muted=(sink.get("mute", "").lower() == "yes"),
            volume_percent=_first_percent(sink.get("volume", "")),
            channels=sink.get("channel_map", ""),
            sample_rate=sink.get("sample_spec", ""),
            description=desc,
            hardware_present=hw_present,
            physical_likely=(not looks_plugin and bus != "virtual"),
            monitor_source_name=sink.get("monitor_source", ""),
        )

        if looks_plugin:
            device.device_class = "plugin_device"
            device.diagnostic_flags.append("plugin_like_name")
            hidden_diagnostic_only.append(device)
        elif bus == "virtual":
            device.device_class = "virtual_device"
            virtual_devices.append(device)
        else:
            output_devices.append(device)

    for source in parsed["pactl_sources"]:
        tech_name = source.get("name", "")
        props = source.get("properties", {})
        desc = source.get("description", props.get("device.description", ""))
        monitor_of_sink = source.get("monitor_of_sink", "")
        is_monitor = _is_monitor(tech_name, desc, monitor_of_sink)
        looks_plugin = _looks_like_plugin(tech_name, desc)
        bus = _bus_type(tech_name, desc, props)
        card_name = props.get("alsa.card_name", props.get("device.product.name", ""))
        card_idx, dev_idx, hw_present = _match_alsa_card(card_name, parsed["alsa_capture_hw"])
        state = _parse_state(source.get("state", ""))

        if is_monitor:
            cls = "output_monitor"
        else:
            cls = "input_device"

        device = AudioDevice(
            stable_id=_stable_id(cls, tech_name, card_name),
            device_class=cls,
            display_name=_friendly_name(desc, tech_name),
            technical_name=tech_name,
            backend_ids={"pactl": str(source.get("index", "")), "wpctl": wpctl_name_to_id.get(desc, "")},
            card_name=card_name,
            card_index=card_idx,
            device_index=dev_idx,
            bus_type=bus,
            connection_label=_connection_label(source.get("active_port", ""), bus, desc),
            profile=props.get("device.profile.description", ""),
            ports=[p for p in list(source.get("ports", {}).keys()) if p.lower() != "active port"],
            state=state,
            default=(tech_name == defaults.get("source")),
            muted=(source.get("mute", "").lower() == "yes"),
            volume_percent=_first_percent(source.get("volume", "")),
            channels=source.get("channel_map", ""),
            sample_rate=source.get("sample_spec", ""),
            description=desc,
            hardware_present=hw_present,
            physical_likely=(not looks_plugin and not is_monitor and bus != "virtual"),
            monitor_source_name="",
        )

        if cls == "output_monitor":
            monitor_devices.append(device)
        elif looks_plugin:
            device.device_class = "plugin_device"
            device.diagnostic_flags.append("plugin_like_name")
            hidden_diagnostic_only.append(device)
        elif bus == "virtual":
            device.device_class = "virtual_device"
            virtual_devices.append(device)
        else:
            input_devices.append(device)

    devices_by_stable_id = {d.stable_id: d for d in [*output_devices, *input_devices, *monitor_devices, *virtual_devices, *hidden_diagnostic_only]}
    tech_to_stable = {d.technical_name: d.stable_id for d in devices_by_stable_id.values()}

    streams: List[AudioStream] = []
    for stream in parsed["pactl_sink_inputs"]:
        props = stream.get("properties", {})
        sink_idx = stream.get("sink", "")
        sink_name = ""
        for sk in parsed["pactl_short_sinks"]:
            if sk.get("index") == sink_idx:
                sink_name = sk.get("name", "")
                break
        target_stable = tech_to_stable.get(sink_name, "")
        streams.append(
            AudioStream(
                stream_id=f"sink-input-{stream.get('index', '')}",
                direction="playback",
                app_name=props.get("application.name", "Unknown App"),
                process_name=props.get("application.process.binary", ""),
                process_id=props.get("application.process.id", ""),
                target_device_stable_id=target_stable,
                target_device_name=sink_name,
                muted=(stream.get("mute", "").lower() == "yes"),
                volume_percent=_first_percent(stream.get("volume", "")),
                state="running",
                technical_name=props.get("media.name", ""),
            )
        )

    return {
        "output_devices": [d.to_dict() for d in output_devices],
        "input_devices": [d.to_dict() for d in input_devices],
        "monitor_devices": [d.to_dict() for d in monitor_devices],
        "virtual_devices": [d.to_dict() for d in virtual_devices],
        "hidden_diagnostic_only": [d.to_dict() for d in hidden_diagnostic_only],
        "streams": [s.to_dict() for s in streams],
        "device_lookup": {sid: dev.to_dict() for sid, dev in devices_by_stable_id.items()},
        "defaults": defaults,
    }
