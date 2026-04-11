from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Dict, List, Tuple

from app.models.audio_models import AudioDevice, AudioStream

logger = logging.getLogger(__name__)

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
    return ".monitor" in s or "monitor" in (name or "").lower()


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
    if "displayport" in text or " dp" in text:
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


def _parse_mute(value: str) -> bool:
    v = (value or "").strip().lower()
    return v in {"yes", "ja", "true", "on"}


def _parse_volume_payload(text: str) -> Dict[str, Any]:
    # Parses lines like:
    # front-left: 26241 / 40% / -23.85 dB, front-right: 26241 / 40% / -23.85 dB
    # Volume: mono: 65536 / 100% / 0.00 dB
    channel_re = re.compile(
        r"([a-z0-9\-_]+(?:\s+[a-z0-9\-_]+)*)\s*:\s*(\d+)\s*/\s*(-?\d+)%\s*/\s*(-?\d+(?:[\.,]\d+)?)\s*dB",
        re.IGNORECASE,
    )
    channels: List[Dict[str, Any]] = []
    for m in channel_re.finditer(text or ""):
        channels.append(
            {
                "channel": m.group(1).strip().lower(),
                "raw": int(m.group(2)),
                "percent": int(m.group(3)),
                "db": float(m.group(4).replace(",", ".")),
            }
        )
    if not channels:
        simple = re.search(r"(\d+)\s*/\s*(-?\d+)%\s*/\s*(-?\d+(?:[\.,]\d+)?)\s*dB", text or "", re.IGNORECASE)
        if simple:
            channels.append(
                {
                    "channel": "master",
                    "raw": int(simple.group(1)),
                    "percent": int(simple.group(2)),
                    "db": float(simple.group(3).replace(",", ".")),
                }
            )

    if not channels:
        return {
            "percent": None,
            "db": None,
            "raw": None,
            "channels": [],
        }

    primary = channels[0]
    if len(channels) > 1:
        avg_percent = int(round(sum(c["percent"] for c in channels) / len(channels)))
        avg_db = round(sum(c["db"] for c in channels) / len(channels), 2)
        avg_raw = int(round(sum(c["raw"] for c in channels) / len(channels)))
    else:
        avg_percent, avg_db, avg_raw = primary["percent"], primary["db"], primary["raw"]

    return {
        "percent": avg_percent,
        "db": avg_db,
        "raw": avg_raw,
        "channels": channels,
    }


def _parse_flags(raw_flags: str) -> Tuple[bool, bool]:
    flags = (raw_flags or "").upper()
    return ("HW_VOLUME_CTRL" in flags or "HARDWARE" in flags), ("HW_MUTE_CTRL" in flags)


def _choose_amixer_controls(card_idx: int | None, amixer_per_card: Dict[str, Any]) -> Dict[str, Any]:
    if card_idx is None:
        return {
            "capture_gain": None,
            "mic_boost": None,
            "controls": [],
        }
    card = amixer_per_card.get(str(card_idx), {})
    controls = card.get("parsed_controls", [])
    capture = None
    boost = None
    for c in controls:
        kind = c.get("kind")
        if kind == "capture_gain" and capture is None:
            capture = c
        if kind == "mic_boost" and boost is None:
            boost = c
    return {
        "capture_gain": capture,
        "mic_boost": boost,
        "controls": controls,
    }


def normalize_audio(raw: Dict[str, Any]) -> Dict[str, Any]:
    parsed = raw["parsed"]
    defaults = raw["defaults"]

    pactl_sinks = parsed["pactl_sinks"][:]
    pactl_sources = parsed["pactl_sources"][:]
    short_sink_by_name = {row.get("name", ""): row for row in parsed["pactl_short_sinks"] if row.get("name")}
    short_source_by_name = {row.get("name", ""): row for row in parsed["pactl_short_sources"] if row.get("name")}

    # Locale/format fallback
    if not pactl_sinks and parsed["pactl_short_sinks"]:
        for row in parsed["pactl_short_sinks"]:
            pactl_sinks.append(
                {
                    "index": row.get("index", ""),
                    "name": row.get("name", ""),
                    "description": row.get("name", ""),
                    "state": row.get("state", ""),
                    "mute": "no",
                    "volume": "",
                    "base_volume": "",
                    "flags": "",
                    "sample_spec": row.get("sample_spec", ""),
                    "channel_map": "",
                    "monitor_source": f"{row.get('name', '')}.monitor",
                    "properties": {},
                    "ports": {},
                }
            )
    if not pactl_sources and parsed["pactl_short_sources"]:
        for row in parsed["pactl_short_sources"]:
            name = row.get("name", "")
            pactl_sources.append(
                {
                    "index": row.get("index", ""),
                    "name": name,
                    "description": name,
                    "state": row.get("state", ""),
                    "mute": "no",
                    "volume": "",
                    "base_volume": "",
                    "flags": "",
                    "sample_spec": row.get("sample_spec", ""),
                    "channel_map": "",
                    "monitor_of_sink": "yes" if name.endswith(".monitor") else "",
                    "properties": {},
                    "ports": {},
                }
            )

    wpctl_name_to_id = {row["name"]: row["wpctl_id"] for row in parsed["wpctl_nodes"]}

    output_devices: List[AudioDevice] = []
    input_devices: List[AudioDevice] = []
    monitor_devices: List[AudioDevice] = []
    virtual_devices: List[AudioDevice] = []
    hidden_diagnostic_only: List[AudioDevice] = []

    for sink in pactl_sinks:
        tech_name = sink.get("name", "")
        short_sink = short_sink_by_name.get(tech_name, {})
        probe = parsed["sink_probe"].get(tech_name, {})
        props = sink.get("properties", {})
        desc = sink.get("description", props.get("device.description", ""))
        looks_plugin = _looks_like_plugin(tech_name, desc)
        bus = _bus_type(tech_name, desc, props)
        card_name = props.get("alsa.card_name", props.get("device.product.name", ""))
        card_idx, dev_idx, hw_present = _match_alsa_card(card_name, parsed["alsa_playback_hw"])
        state = _parse_state(sink.get("state", "") or short_sink.get("state", ""))

        current_vol = _parse_volume_payload(sink.get("volume", "") or probe.get("volume", ""))
        base_vol = _parse_volume_payload(sink.get("base_volume", ""))
        has_hw_volume, has_hw_mute = _parse_flags(sink.get("flags", ""))
        muted = _parse_mute(sink.get("mute", "") or probe.get("mute", ""))

        logger.debug("sink %s parsed current=%s base=%s", tech_name, current_vol, base_vol)

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
            muted=muted,
            channels=sink.get("channel_map", ""),
            sample_rate=sink.get("sample_spec", "") or short_sink.get("sample_spec", ""),
            description=desc,
            hardware_present=hw_present,
            physical_likely=(not looks_plugin and bus != "virtual"),
            monitor_source_name=sink.get("monitor_source", ""),
            active_port=sink.get("active_port", ""),
            volume_percent_current=current_vol["percent"],
            volume_db_current=current_vol["db"],
            volume_raw_current=current_vol["raw"],
            base_volume_percent=base_vol["percent"],
            base_volume_db=base_vol["db"],
            has_hw_volume=has_hw_volume,
            has_hw_mute=has_hw_mute,
            channel_volumes=current_vol["channels"],
            volume_percent=current_vol["percent"],
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

    for source in pactl_sources:
        tech_name = source.get("name", "")
        short_source = short_source_by_name.get(tech_name, {})
        probe = parsed["source_probe"].get(tech_name, {})
        props = source.get("properties", {})
        desc = source.get("description", props.get("device.description", ""))
        monitor_of_sink = source.get("monitor_of_sink", "")
        is_monitor = _is_monitor(tech_name, desc, monitor_of_sink)
        looks_plugin = _looks_like_plugin(tech_name, desc)
        bus = _bus_type(tech_name, desc, props)
        card_name = props.get("alsa.card_name", props.get("device.product.name", ""))
        card_idx, dev_idx, hw_present = _match_alsa_card(card_name, parsed["alsa_capture_hw"])
        state = _parse_state(source.get("state", "") or short_source.get("state", ""))
        has_hw_volume, has_hw_mute = _parse_flags(source.get("flags", ""))
        source_vol = _parse_volume_payload(source.get("volume", "") or probe.get("volume", ""))
        base_vol = _parse_volume_payload(source.get("base_volume", ""))
        muted = _parse_mute(source.get("mute", "") or probe.get("mute", ""))
        mixer = _choose_amixer_controls(card_idx, parsed["amixer_per_card"])

        if is_monitor:
            cls = "output_monitor"
        else:
            cls = "input_device"

        logger.debug(
            "source %s parsed current=%s base=%s amixer_capture=%s amixer_boost=%s",
            tech_name,
            source_vol,
            base_vol,
            mixer["capture_gain"],
            mixer["mic_boost"],
        )

        capture_gain = mixer["capture_gain"]
        mic_boost = mixer["mic_boost"]

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
            muted=muted,
            channels=source.get("channel_map", ""),
            sample_rate=source.get("sample_spec", "") or short_source.get("sample_spec", ""),
            description=desc,
            hardware_present=hw_present,
            physical_likely=(not looks_plugin and not is_monitor and bus != "virtual"),
            monitor_source_name="",
            active_port=source.get("active_port", ""),
            source_volume_percent_current=source_vol["percent"],
            source_volume_db_current=source_vol["db"],
            source_volume_raw_current=source_vol["raw"],
            base_volume_percent=base_vol["percent"],
            base_volume_db=base_vol["db"],
            has_hw_volume=has_hw_volume,
            has_hw_mute=has_hw_mute,
            has_capture_gain=bool(capture_gain),
            capture_gain_percent=(capture_gain or {}).get("percent"),
            capture_gain_db=(capture_gain or {}).get("db"),
            capture_gain_control=(capture_gain or {}).get("name", ""),
            mic_boost_available=bool(mic_boost),
            mic_boost_percent=(mic_boost or {}).get("percent"),
            mic_boost_db=(mic_boost or {}).get("db"),
            mic_boost_control=(mic_boost or {}).get("name", ""),
            channel_volumes=source_vol["channels"],
            hw_controls=mixer["controls"],
            volume_percent=source_vol["percent"],
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

    devices_by_stable_id = {
        d.stable_id: d
        for d in [*output_devices, *input_devices, *monitor_devices, *virtual_devices, *hidden_diagnostic_only]
    }
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
        volume = _parse_volume_payload(stream.get("volume", ""))
        streams.append(
            AudioStream(
                stream_id=f"sink-input-{stream.get('index', '')}",
                direction="playback",
                app_name=props.get("application.name", "Unknown App"),
                process_name=props.get("application.process.binary", ""),
                process_id=props.get("application.process.id", ""),
                target_device_stable_id=target_stable,
                target_device_name=sink_name,
                muted=_parse_mute(stream.get("mute", "")),
                volume_percent=volume["percent"],
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
