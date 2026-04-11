from __future__ import annotations

import json
import time

from flask import Blueprint, Response, jsonify, render_template, request, send_file

from app.services.audio_service import AudioService


audio_bp = Blueprint("audio", __name__)
audio_service = AudioService()


@audio_bp.get("/audio")
def audio_index():
    expert_mode = request.args.get("expert", "0") == "1"
    snapshot = audio_service.build_snapshot(include_diagnostics=expert_mode)
    return render_template("audio/index.html", snapshot=snapshot, expert_mode=expert_mode)


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


@audio_bp.post("/api/audio/device/<stable_id>/test-record")
def api_test_record(stable_id: str):
    body = request.get_json(silent=True) or {}
    duration = float(body.get("duration_sec", 3.0))
    result = audio_service.test_record_input(stable_id, duration_sec=duration)
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@audio_bp.get("/api/audio/device/<stable_id>/test-record/latest.wav")
def api_latest_record_wav(stable_id: str):
    item = audio_service.latest_recording(stable_id)
    if not item:
        return jsonify({"ok": False, "error": "no recording"}), 404
    return send_file(item["file_path"], mimetype="audio/wav", as_attachment=False)


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
