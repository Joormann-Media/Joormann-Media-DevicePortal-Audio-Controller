from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.request import urlopen, Request

from flask import Blueprint, Response, jsonify, render_template, request, send_file

from app.services.audio_service import AudioService

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_AUDIOPLAYER_BASE_PATHS = [
    Path.home() / "projects" / "Joormann-Media-Jarvis-AudioPlayer",
    Path("/mnt/ai-ssd/Joormann-Media-Jarvis-AudioPlayer"),
]


def _find_zones_dir() -> Path | None:
    for base in _AUDIOPLAYER_BASE_PATHS:
        z = base / "config" / "zones"
        if z.is_dir():
            return z
    return None


def _load_zone_configs() -> list[dict[str, Any]]:
    zones_dir = _find_zones_dir()
    if not zones_dir:
        return []
    zones = []
    for env_file in sorted(zones_dir.glob("zone-*.env")):
        cfg: dict[str, Any] = {"file": env_file.name, "zone_name": "", "sink_name": "", "bt_address": "", "port": ""}
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"')
                if k == "AUDIOPLAYER_ZONE_NAME":
                    cfg["zone_name"] = v
                elif k == "AUDIOPLAYER_SINK_NAME":
                    cfg["sink_name"] = v
                elif k == "AUDIOPLAYER_BT_ADDRESS":
                    cfg["bt_address"] = v
                elif k == "FLASK_PORT":
                    cfg["port"] = v
        if not cfg["zone_name"]:
            cfg["zone_name"] = env_file.stem.replace("zone-", "")
        zones.append(cfg)
    return zones


def _fetch_zone_status(port: str) -> dict[str, Any]:
    if not port:
        return {"ok": False, "error": "no_port"}
    url = f"http://127.0.0.1:{port}/api/audio/status"
    try:
        req = Request(url, headers={"User-Agent": "AudioController/1.0"})
        with urlopen(req, timeout=3) as r:
            data = json.loads(r.read().decode())
        inner = data.get("data") or data
        return {"ok": True, "status": inner}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _zone_api_call(port: str, path: str, method: str = "POST", body: dict | None = None) -> dict[str, Any]:
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body or {}).encode()
    req = Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": "AudioController/1.0"}, method=method)
    try:
        with urlopen(req, timeout=6) as r:
            return json.loads(r.read().decode())
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _run_repo_update(mode: str) -> tuple[dict, int]:
    script = _PROJECT_ROOT / "scripts" / "update_manager.sh"
    if not script.exists():
        return {"ok": False, "code": "update_script_missing", "message": f"Script fehlt: {script}"}, 500
    timeout = 1800 if mode == "apply" else 45
    try:
        proc = subprocess.run(
            ["bash", str(script), mode],
            text=True, capture_output=True, timeout=timeout,
        )
    except Exception as exc:
        return {"ok": False, "code": "update_exec_failed", "message": str(exc)}, 500

    raw = (proc.stdout or "").strip()
    payload: dict = {}
    if raw:
        try:
            payload = json.loads(raw.splitlines()[-1])
        except Exception:
            payload = {}

    if not isinstance(payload, dict) or not payload:
        payload = {
            "ok": proc.returncode == 0,
            "code": "update_output_invalid",
            "message": "Ungültige Update-Antwort",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    if proc.returncode != 0:
        payload["ok"] = False
        payload.setdefault("code", "update_failed")
        payload.setdefault("message", "Update fehlgeschlagen")

    return payload, 200 if payload.get("ok") else 500


audio_bp = Blueprint("audio", __name__)
audio_service = AudioService()


@audio_bp.get("/audio")
def audio_index():
    expert_mode = request.args.get("expert", "0") == "1"
    snapshot = audio_service.build_snapshot(include_diagnostics=expert_mode)
    meter_status = audio_service.meter_status()
    zone_configs = _load_zone_configs()
    zones_with_status = []
    for z in zone_configs:
        status = _fetch_zone_status(z.get("port", ""))
        zones_with_status.append({**z, "live": status})
    return render_template(
        "audio/index.html",
        snapshot=snapshot,
        expert_mode=expert_mode,
        meter_running=meter_status["running"],
        meter_autostart=meter_status["autostart"],
        zones=zones_with_status,
    )


@audio_bp.get("/api/audio/zones")
def api_zones():
    zone_configs = _load_zone_configs()
    zones_with_status = []
    for z in zone_configs:
        status = _fetch_zone_status(z.get("port", ""))
        zones_with_status.append({**z, "live": status})
    return jsonify({"ok": True, "zones": zones_with_status})


@audio_bp.get("/audio/zones")
def audio_zones_ui():
    zone_configs = _load_zone_configs()
    zones_with_status = []
    for z in zone_configs:
        status = _fetch_zone_status(z.get("port", ""))
        zones_with_status.append({**z, "live": status})
    return render_template("audio/zones.html", zones=zones_with_status)


@audio_bp.post("/api/audio/zones")
def api_zones_create():
    body = request.get_json(silent=True) or {}
    name = str(body.get("zone_name") or "").strip().lower().replace(" ", "-")
    port = str(body.get("port") or "").strip()
    sink = str(body.get("sink_name") or "").strip()
    bt_addr = str(body.get("bt_address") or "").strip()

    if not name or not port:
        return jsonify({"ok": False, "error": "zone_name und port sind Pflichtfelder"}), 400

    zones_dir = _find_zones_dir()
    if not zones_dir:
        return jsonify({"ok": False, "error": "zones-Verzeichnis nicht gefunden"}), 500

    env_path = zones_dir / f"zone-{name}.env"
    if env_path.exists():
        return jsonify({"ok": False, "error": f"Zone '{name}' existiert bereits"}), 409

    # Prüfe Port-Konflikt
    for z in _load_zone_configs():
        if z.get("port") == port:
            return jsonify({"ok": False, "error": f"Port {port} bereits von Zone '{z['zone_name']}' belegt"}), 409

    lines = [
        f"FLASK_PORT={port}",
        f"AUDIOPLAYER_ZONE_NAME={name}",
        f"AUDIOPLAYER_SINK_NAME={sink}",
        f"AUDIOPLAYER_BT_ADDRESS={bt_addr}",
    ]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jsonify({"ok": True, "zone_name": name, "port": port, "file": env_path.name}), 201


@audio_bp.put("/api/audio/zones/<zone_name>")
def api_zones_update(zone_name: str):
    body = request.get_json(silent=True) or {}
    zones_dir = _find_zones_dir()
    if not zones_dir:
        return jsonify({"ok": False, "error": "zones-Verzeichnis nicht gefunden"}), 500

    env_path = zones_dir / f"zone-{zone_name}.env"
    if not env_path.exists():
        return jsonify({"ok": False, "error": f"Zone '{zone_name}' nicht gefunden"}), 404

    port = str(body.get("port") or "").strip()
    sink = str(body.get("sink_name") or "").strip()
    bt_addr = str(body.get("bt_address") or "").strip()

    if not port:
        return jsonify({"ok": False, "error": "port ist Pflichtfeld"}), 400

    lines = [
        f"FLASK_PORT={port}",
        f"AUDIOPLAYER_ZONE_NAME={zone_name}",
        f"AUDIOPLAYER_SINK_NAME={sink}",
        f"AUDIOPLAYER_BT_ADDRESS={bt_addr}",
    ]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jsonify({"ok": True, "zone_name": zone_name, "port": port})


@audio_bp.delete("/api/audio/zones/<zone_name>")
def api_zones_delete(zone_name: str):
    zones_dir = _find_zones_dir()
    if not zones_dir:
        return jsonify({"ok": False, "error": "zones-Verzeichnis nicht gefunden"}), 500

    env_path = zones_dir / f"zone-{zone_name}.env"
    if not env_path.exists():
        return jsonify({"ok": False, "error": f"Zone '{zone_name}' nicht gefunden"}), 404

    env_path.unlink()
    return jsonify({"ok": True, "deleted": zone_name})


@audio_bp.post("/api/audio/zones/<zone_name>/start")
def api_zones_start(zone_name: str):
    audioplayer_base = _find_zones_dir()
    if not audioplayer_base:
        return jsonify({"ok": False, "error": "zones-Verzeichnis nicht gefunden"}), 500
    start_script = audioplayer_base.parent.parent / "scripts" / "start-zone.sh"
    if not start_script.exists():
        return jsonify({"ok": False, "error": f"start-zone.sh nicht gefunden: {start_script}"}), 500
    try:
        proc = subprocess.run(
            ["bash", str(start_script), zone_name],
            capture_output=True, text=True, timeout=30,
        )
        ok = proc.returncode == 0
        return jsonify({"ok": ok, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@audio_bp.post("/api/audio/zones/<zone_name>/stop")
def api_zones_stop(zone_name: str):
    audioplayer_base = _find_zones_dir()
    if not audioplayer_base:
        return jsonify({"ok": False, "error": "zones-Verzeichnis nicht gefunden"}), 500
    stop_script = audioplayer_base.parent.parent / "scripts" / "stop-zone.sh"
    if not stop_script.exists():
        return jsonify({"ok": False, "error": f"stop-zone.sh nicht gefunden: {stop_script}"}), 500
    try:
        proc = subprocess.run(
            ["bash", str(stop_script), zone_name],
            capture_output=True, text=True, timeout=15,
        )
        return jsonify({"ok": proc.returncode == 0, "stdout": proc.stdout.strip()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@audio_bp.get("/api/audio/summary")
def api_summary():
    snapshot = audio_service.build_snapshot(include_diagnostics=False)
    return jsonify(
        {
            "timestamp_utc": snapshot["timestamp_utc"],
            "snapshot_hash": snapshot["snapshot_hash"],
            "summary": snapshot["summary"],
        }
    )


@audio_bp.get("/api/audio/devices")
def api_devices():
    include_expert = request.args.get("expert", "0") == "1"
    snapshot = audio_service.build_snapshot(include_diagnostics=include_expert)
    payload = {
        "timestamp_utc": snapshot["timestamp_utc"],
        "snapshot_hash": snapshot["snapshot_hash"],
        "defaults": snapshot["defaults"],
        "devices": snapshot["devices"],
    }
    if include_expert:
        payload["hidden_diagnostic_only"] = snapshot.get("hidden_diagnostic_only", [])
    return jsonify(payload)


@audio_bp.get("/api/audio/streams")
def api_streams():
    snapshot = audio_service.build_snapshot(include_diagnostics=False)
    return jsonify(
        {
            "timestamp_utc": snapshot["timestamp_utc"],
            "snapshot_hash": snapshot["snapshot_hash"],
            "streams": snapshot["streams"],
        }
    )


@audio_bp.get("/api/audio/diagnostics")
def api_diagnostics():
    snapshot = audio_service.build_snapshot(include_diagnostics=True)
    return jsonify(
        {
            "timestamp_utc": snapshot["timestamp_utc"],
            "snapshot_hash": snapshot["snapshot_hash"],
            "diagnostics": snapshot.get("diagnostics", {}),
            "hidden_diagnostic_only": snapshot.get("hidden_diagnostic_only", []),
        }
    )


@audio_bp.get("/api/audio/meters")
def api_meters():
    return jsonify(audio_service.get_meters())


@audio_bp.get("/api/audio/meter/status")
def api_meter_status():
    return jsonify(audio_service.meter_status())


@audio_bp.post("/api/audio/meter/start")
def api_meter_start():
    return jsonify(audio_service.meter_start())


@audio_bp.post("/api/audio/meter/stop")
def api_meter_stop():
    return jsonify(audio_service.meter_stop())


@audio_bp.post("/api/audio/meter/autostart/enable")
def api_meter_autostart_enable():
    return jsonify(audio_service.meter_autostart_enable())


@audio_bp.post("/api/audio/meter/autostart/disable")
def api_meter_autostart_disable():
    return jsonify(audio_service.meter_autostart_disable())


@audio_bp.post("/api/audio/device/<stable_id>/set-default")
def api_set_default(stable_id: str):
    result = audio_service.set_device_default(stable_id)
    code = 200 if result["ok"] else 400
    return jsonify(result), code


@audio_bp.post("/api/audio/device/<stable_id>/set-output-volume")
def api_set_output_volume(stable_id: str):
    body = request.get_json(silent=True) or {}
    if "volume_percent" not in body:
        return jsonify({"ok": False, "error": "volume_percent required"}), 400
    result = audio_service.set_output_volume(stable_id, int(body["volume_percent"]))
    code = 200 if result["ok"] else 400
    return jsonify(result), code


@audio_bp.post("/api/audio/device/<stable_id>/set-input-volume")
def api_set_input_volume(stable_id: str):
    body = request.get_json(silent=True) or {}
    if "volume_percent" not in body:
        return jsonify({"ok": False, "error": "volume_percent required"}), 400
    result = audio_service.set_input_volume(stable_id, int(body["volume_percent"]))
    code = 200 if result["ok"] else 400
    return jsonify(result), code


@audio_bp.post("/api/audio/device/<stable_id>/set-output-mute")
def api_set_output_mute(stable_id: str):
    body = request.get_json(silent=True) or {}
    if "mute" not in body:
        return jsonify({"ok": False, "error": "mute required"}), 400
    result = audio_service.set_output_mute(stable_id, bool(body["mute"]))
    code = 200 if result["ok"] else 400
    return jsonify(result), code


@audio_bp.post("/api/audio/device/<stable_id>/set-input-mute")
def api_set_input_mute(stable_id: str):
    body = request.get_json(silent=True) or {}
    if "mute" not in body:
        return jsonify({"ok": False, "error": "mute required"}), 400
    result = audio_service.set_input_mute(stable_id, bool(body["mute"]))
    code = 200 if result["ok"] else 400
    return jsonify(result), code


@audio_bp.post("/api/audio/device/<stable_id>/set-capture-gain")
def api_set_capture_gain(stable_id: str):
    body = request.get_json(silent=True) or {}
    if "value_percent" not in body:
        return jsonify({"ok": False, "error": "value_percent required"}), 400
    result = audio_service.set_capture_gain(stable_id, int(body["value_percent"]))
    code = 200 if result["ok"] else 400
    return jsonify(result), code


@audio_bp.post("/api/audio/device/<stable_id>/set-mic-boost")
def api_set_mic_boost(stable_id: str):
    body = request.get_json(silent=True) or {}
    if "value_percent" not in body:
        return jsonify({"ok": False, "error": "value_percent required"}), 400
    result = audio_service.set_mic_boost(stable_id, int(body["value_percent"]))
    code = 200 if result["ok"] else 400
    return jsonify(result), code


@audio_bp.post("/api/audio/device/<stable_id>/set-hardware-gain")
def api_set_hardware_gain(stable_id: str):
    body = request.get_json(silent=True) or {}
    value_percent = body.get("value_percent")
    raw_value = body.get("raw_value")
    if value_percent is None and raw_value is None:
        return jsonify({"ok": False, "error": "value_percent or raw_value required"}), 400
    result = audio_service.set_hardware_gain(
        stable_id,
        value_percent=int(value_percent) if value_percent is not None else None,
        raw_value=int(raw_value) if raw_value is not None else None,
    )
    code = 200 if result["ok"] else 400
    return jsonify(result), code


@audio_bp.post("/api/audio/device/<stable_id>/set-alsa-control")
def api_set_alsa_control(stable_id: str):
    body = request.get_json(silent=True) or {}
    control_name = str(body.get("control_name", "")).strip()
    value_percent = body.get("value_percent")
    raw_value = body.get("raw_value")
    if control_name == "":
        return jsonify({"ok": False, "error": "control_name required"}), 400
    if value_percent is None and raw_value is None:
        return jsonify({"ok": False, "error": "value_percent or raw_value required"}), 400
    result = audio_service.set_alsa_control(
        stable_id,
        control_name,
        value_percent=int(value_percent) if value_percent is not None else None,
        raw_value=int(raw_value) if raw_value is not None else None,
    )
    code = 200 if result["ok"] else 400
    return jsonify(result), code


@audio_bp.post("/api/audio/device/<stable_id>/set-alsa-switch")
def api_set_alsa_switch(stable_id: str):
    body = request.get_json(silent=True) or {}
    control_name = str(body.get("control_name", "")).strip()
    if control_name == "":
        return jsonify({"ok": False, "error": "control_name required"}), 400
    if "switch_on" not in body:
        return jsonify({"ok": False, "error": "switch_on required"}), 400
    result = audio_service.set_alsa_switch(stable_id, control_name, bool(body["switch_on"]))
    code = 200 if result["ok"] else 400
    return jsonify(result), code


@audio_bp.post("/api/audio/device/<stable_id>/test-record")
def api_test_record(stable_id: str):
    body = request.get_json(silent=True) or {}
    duration = float(body.get("duration_sec", 3.0))
    result = audio_service.test_record_input(stable_id, duration_sec=duration)
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@audio_bp.post("/api/audio/device/<stable_id>/calibrate-input")
def api_calibrate_input(stable_id: str):
    body = request.get_json(silent=True) or {}
    duration_sec = float(body.get("duration_sec", 4.0))
    result = audio_service.calibrate_input(stable_id, duration_sec=duration_sec)
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@audio_bp.get("/api/audio/device/<stable_id>/calibration")
def api_get_calibration(stable_id: str):
    result = audio_service.get_input_calibration(stable_id)
    code = 200 if result.get("ok") else 404
    return jsonify(result), code


@audio_bp.post("/api/audio/device/<stable_id>/apply-calibration-recommendation")
def api_apply_calibration(stable_id: str):
    result = audio_service.apply_calibration_recommendation(stable_id)
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@audio_bp.get("/api/audio/device/<stable_id>/test-record/latest.wav")
def api_latest_record_wav(stable_id: str):
    item = audio_service.latest_recording(stable_id)
    if not item:
        return jsonify({"ok": False, "error": "no recording"}), 404
    return send_file(item["file_path"], mimetype="audio/wav", as_attachment=False)


@audio_bp.get("/api/audio/device/<stable_id>/calibration/latest.wav")
def api_latest_calibration_wav(stable_id: str):
    file_path = audio_service.get_calibration_recording_file(stable_id)
    if not file_path:
        return jsonify({"ok": False, "error": "no calibration recording"}), 404
    return send_file(file_path, mimetype="audio/wav", as_attachment=False)


# Backward compatibility endpoints
@audio_bp.post("/api/audio/device/<stable_id>/set-volume")
def api_set_volume_legacy(stable_id: str):
    body = request.get_json(silent=True) or {}
    if "volume_percent" not in body:
        return jsonify({"ok": False, "error": "volume_percent required"}), 400
    result = audio_service.set_device_volume(stable_id, int(body["volume_percent"]))
    code = 200 if result["ok"] else 400
    return jsonify(result), code


@audio_bp.post("/api/audio/device/<stable_id>/set-mute")
def api_set_mute_legacy(stable_id: str):
    body = request.get_json(silent=True) or {}
    if "mute" not in body:
        return jsonify({"ok": False, "error": "mute required"}), 400
    result = audio_service.set_device_mute(stable_id, bool(body["mute"]))
    code = 200 if result["ok"] else 400
    return jsonify(result), code


@audio_bp.post("/api/audio/stream/<stream_id>/set-volume")
def api_set_stream_volume(stream_id: str):
    body = request.get_json(silent=True) or {}
    if "volume_percent" not in body:
        return jsonify({"ok": False, "error": "volume_percent required"}), 400
    result = audio_service.set_stream_volume(stream_id, int(body["volume_percent"]))
    code = 200 if result["ok"] else 400
    return jsonify(result), code


@audio_bp.get("/api/update/status")
def api_update_status():
    payload, status = _run_repo_update("status")
    return jsonify(payload), status


@audio_bp.post("/api/update/apply")
def api_update_apply():
    payload, status = _run_repo_update("apply")
    return jsonify(payload), status


@audio_bp.get("/api/audio/events")
def api_events():
    def stream() -> Response:
        previous_hash = ""
        while True:
            snapshot = audio_service.build_snapshot(include_diagnostics=False)
            current_hash = snapshot["snapshot_hash"]
            if current_hash != previous_hash:
                payload = {
                    "snapshot_hash": current_hash,
                    "timestamp_utc": snapshot["timestamp_utc"],
                }
                yield f"event: snapshot\ndata: {json.dumps(payload)}\n\n"
                previous_hash = current_hash
            else:
                yield "event: keepalive\ndata: {}\n\n"
            time.sleep(2)

    return Response(stream(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})


@audio_bp.post("/api/audio/broadcast")
def api_broadcast():
    body = request.get_json(silent=True) or {}
    stream_url = str(body.get("stream_url") or "").strip()
    zone_names = body.get("zone_names") if isinstance(body.get("zone_names"), list) else []
    sync = bool(body.get("sync", True))

    if not stream_url:
        return jsonify({"ok": False, "error": "stream_url fehlt"}), 400

    zones = _load_zone_configs()
    targets = []
    for z in zones:
        if zone_names and z["zone_name"] not in zone_names:
            continue
        if not z.get("port"):
            continue
        live = _fetch_zone_status(z["port"])
        if live.get("ok"):
            targets.append(z)

    if not targets:
        return jsonify({"ok": False, "error": "Keine erreichbaren Zonen gefunden"}), 404

    if sync:
        stop_threads = [threading.Thread(target=_zone_api_call, args=(z["port"], "/api/audio/radio/stop", "POST")) for z in targets]
        for t in stop_threads:
            t.start()
        for t in stop_threads:
            t.join(timeout=4)
        time.sleep(0.15)

    results: dict[str, Any] = {}

    def _start(z: dict[str, Any]) -> None:
        results[z["zone_name"]] = _zone_api_call(z["port"], "/api/audio/radio/start", "POST", {"stream_url": stream_url})

    start_threads = [threading.Thread(target=_start, args=(z,)) for z in targets]
    for t in start_threads:
        t.start()
    for t in start_threads:
        t.join(timeout=10)

    ok = any(r.get("ok") for r in results.values())
    return jsonify({"ok": ok, "stream_url": stream_url, "zones": results, "synced": sync, "target_count": len(targets)})


@audio_bp.post("/api/audio/broadcast/stop")
def api_broadcast_stop():
    body = request.get_json(silent=True) or {}
    zone_names = body.get("zone_names") if isinstance(body.get("zone_names"), list) else []

    zones = _load_zone_configs()
    results: dict[str, Any] = {}
    for z in zones:
        if zone_names and z["zone_name"] not in zone_names:
            continue
        if not z.get("port"):
            continue
        live = _fetch_zone_status(z["port"])
        if live.get("ok"):
            results[z["zone_name"]] = _zone_api_call(z["port"], "/api/audio/radio/stop", "POST")

    return jsonify({"ok": True, "stopped": list(results.keys()), "results": results})


@audio_bp.get("/api/audio/broadcast/status")
def api_broadcast_status():
    zones = _load_zone_configs()
    statuses: list[dict[str, Any]] = []
    for z in zones:
        live = _fetch_zone_status(z.get("port", ""))
        st = live.get("status") if isinstance(live.get("status"), dict) else {}
        statuses.append({
            "zone_name": z["zone_name"],
            "port": z["port"],
            "online": live.get("ok", False),
            "active_source": st.get("active_source", "idle"),
            "radio_url": (st.get("radio") or {}).get("stream_url"),
            "radio_running": bool((st.get("radio") or {}).get("running")),
            "volume_percent": st.get("volume_percent"),
        })
    playing = [s for s in statuses if s["radio_running"]]
    urls = list({s["radio_url"] for s in playing if s["radio_url"]})
    return jsonify({"ok": True, "zones": statuses, "broadcast_active": len(urls) == 1 and len(playing) > 1, "stream_urls": urls})


@audio_bp.post("/api/audio/zones/<zone_name>/output/switch")
def api_zone_output_switch(zone_name: str):
    body = request.get_json(silent=True) or {}
    sink_name = str(body.get("sink_name") or "").strip()
    if not sink_name:
        return jsonify({"ok": False, "error": "sink_name fehlt"}), 400
    zones = _load_zone_configs()
    zone = next((z for z in zones if z["zone_name"] == zone_name), None)
    if not zone or not zone.get("port"):
        return jsonify({"ok": False, "error": f"Zone '{zone_name}' nicht gefunden oder offline"}), 404
    result = _zone_api_call(zone["port"], "/api/audio/output/switch", "POST", {"sink_name": sink_name})
    return jsonify(result)
