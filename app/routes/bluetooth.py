"""
Bluetooth-Verwaltungs-Routes für die Flask-Anwendung.

Alle Endpunkte folgen dem bestehenden Muster aus audio.py:
  - GET-Routen liefern JSON
  - POST-Routen nehmen JSON-Body entgegen
  - Antworten enthalten immer {"ok": bool, ...}
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.services.bluetooth_service import BluetoothService

bluetooth_bp = Blueprint("bluetooth", __name__)
_bt = BluetoothService()


# ─── Adapter-Status ──────────────────────────────────────────────────────────

@bluetooth_bp.get("/api/bluetooth/status")
def api_bt_status():
    """Adapter-Status (Power, Discoverable, Pairable) + Scan-Status."""
    return jsonify({
        "adapter": _bt.get_adapter_status(),
        "scan":    _bt.get_scan_status(),
    })


@bluetooth_bp.post("/api/bluetooth/adapter/power")
def api_bt_power():
    """Bluetooth-Adapter ein-/ausschalten. Body: { "on": true }"""
    body = request.get_json(silent=True) or {}
    on   = bool(body.get("on", True))
    res  = _bt.adapter_power(on)
    return jsonify(res), 200 if res["ok"] else 400


@bluetooth_bp.post("/api/bluetooth/adapter/discoverable")
def api_bt_discoverable():
    """Adapter sichtbar/unsichtbar schalten. Body: { "on": true }"""
    body = request.get_json(silent=True) or {}
    on   = bool(body.get("on", True))
    res  = _bt.adapter_discoverable(on)
    return jsonify(res), 200 if res["ok"] else 400


@bluetooth_bp.post("/api/bluetooth/adapter/pairable")
def api_bt_pairable():
    """Pairing-Modus aktivieren/deaktivieren. Body: { "on": true }"""
    body = request.get_json(silent=True) or {}
    on   = bool(body.get("on", True))
    res  = _bt.adapter_pairable(on)
    return jsonify(res), 200 if res["ok"] else 400


# ─── Scan ────────────────────────────────────────────────────────────────────

@bluetooth_bp.post("/api/bluetooth/scan/start")
def api_bt_scan_start():
    """
    Startet einen Bluetooth-Geräte-Scan im Hintergrund.
    Body: { "duration_sec": 12 }   (Standard: 12, Min: 5, Max: 30)
    """
    body     = request.get_json(silent=True) or {}
    duration = int(body.get("duration_sec", 12))
    duration = max(5, min(30, duration))
    res      = _bt.start_scan(duration_sec=duration)
    return jsonify(res), 200 if res["ok"] else 409


@bluetooth_bp.post("/api/bluetooth/scan/stop")
def api_bt_scan_stop():
    """Bricht den laufenden Scan ab."""
    return jsonify(_bt.stop_scan())


@bluetooth_bp.get("/api/bluetooth/scan/results")
def api_bt_scan_results():
    """
    Aktueller Scan-Status und alle bisher gefundenen Geräte.
    Wird alle 2 Sekunden vom Frontend abgefragt.
    """
    return jsonify(_bt.get_scan_status())


# ─── Bekannte Geräte ─────────────────────────────────────────────────────────

@bluetooth_bp.get("/api/bluetooth/devices")
def api_bt_devices():
    """Alle bei bluetoothctl bekannten (gepairten/gespeicherten) Geräte."""
    devices = _bt.get_known_devices()
    return jsonify({"ok": True, "devices": devices})


# ─── Geräteoperationen ───────────────────────────────────────────────────────

@bluetooth_bp.post("/api/bluetooth/device/<path:mac>/pair")
def api_bt_pair(mac: str):
    """
    Pairt ein gefundenes Gerät (pair → trust → connect).
    MAC-Adresse im URL-Pfad, z. B. /api/bluetooth/device/AA:BB:CC:DD:EE:FF/pair
    """
    res = _bt.pair_device(mac)
    return jsonify(res), 200 if res["ok"] else 400


@bluetooth_bp.post("/api/bluetooth/device/<path:mac>/trust")
def api_bt_trust(mac: str):
    """Markiert ein gepairtes Gerät als vertrauenswürdig. Body: { "trust": true }"""
    body  = request.get_json(silent=True) or {}
    trust = bool(body.get("trust", True))
    res   = _bt.trust_device(mac, trust)
    return jsonify(res), 200 if res["ok"] else 400


@bluetooth_bp.post("/api/bluetooth/device/<path:mac>/connect")
def api_bt_connect(mac: str):
    """Verbindet ein bereits gepairtes Gerät."""
    res = _bt.connect_device(mac)
    return jsonify(res), 200 if res["ok"] else 400


@bluetooth_bp.post("/api/bluetooth/device/<path:mac>/disconnect")
def api_bt_disconnect(mac: str):
    """Trennt die Verbindung zu einem Gerät (ohne zu entpairen)."""
    res = _bt.disconnect_device(mac)
    return jsonify(res), 200 if res["ok"] else 400


@bluetooth_bp.delete("/api/bluetooth/device/<path:mac>")
def api_bt_remove(mac: str):
    """Entfernt und unpairt ein Gerät vollständig."""
    res = _bt.remove_device(mac)
    return jsonify(res), 200 if res["ok"] else 400
