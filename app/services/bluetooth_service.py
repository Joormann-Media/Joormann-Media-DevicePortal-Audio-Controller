"""
Bluetooth-Geräteverwaltung über bluetoothctl (BlueZ).

System-Abhängigkeiten:
  - bluetoothctl  (Paket: bluez)
  - rfkill        (optional, für Adapter-Unblocking)

Kein externer Python-Pip-Paket erforderlich.
"""
from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ANSI-Escape-Sequenzen (bluetoothctl gibt farbige Ausgaben)
_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

# Regex-Pattern für bluetoothctl-Ausgaben
_RE_NEW_DEV  = re.compile(r"\[NEW\]\s+Device\s+([0-9A-Fa-f:]{17})\s+(.*)")
_RE_CHG_RSSI = re.compile(r"\[CHG\]\s+Device\s+([0-9A-Fa-f:]{17})\s+RSSI:\s*(-?\d+)")
_RE_CHG_NAME = re.compile(r"\[CHG\]\s+Device\s+([0-9A-Fa-f:]{17})\s+Name:\s+(.*)")
_RE_CHG_ICON = re.compile(r"\[CHG\]\s+Device\s+([0-9A-Fa-f:]{17})\s+Icon:\s+(.*)")
_RE_DEV_LINE = re.compile(r"Device\s+([0-9A-Fa-f:]{17})\s+(.*)")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


def _icon_to_type_label(icon: str) -> str:
    """Übersetzt einen BlueZ-Icon-Namen in einen lesbaren Gerätetyp."""
    mapping = {
        "audio-headset":           "Headset",
        "audio-headphones":        "Kopfhörer",
        "audio-card":              "Audio-Gerät",
        "audio-input-microphone":  "Mikrofon",
        "audio-speakers":          "Lautsprecher",
        "phone":                   "Telefon",
        "computer":                "Computer",
        "input-keyboard":          "Tastatur",
        "input-mouse":             "Maus",
        "input-gaming":            "Gamepad",
        "video-display":           "Display",
        "printer":                 "Drucker",
    }
    return mapping.get((icon or "").lower().strip(), icon or "Unbekannt")


class BluetoothService:
    """
    Vereint Adapter-Verwaltung, Geräte-Scan und Pairing über bluetoothctl.

    Thread-sicher: interner Lock für alle geteilten Strukturen.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Gefundene Geräte während eines aktiven Scans: mac → device-dict
        self._scan_devices: Dict[str, Dict[str, Any]] = {}
        self._scan_active: bool = False
        self._scan_start_time: float = 0.0
        self._scan_duration: int = 0
        self._scan_thread: Optional[threading.Thread] = None

    # ─── Private Hilfsfunktionen ─────────────────────────────────────────────

    def _run(self, cmd: List[str], timeout: int = 8) -> tuple[bool, str]:
        """Führt einen Befehl aus; gibt (ok, bereinigter_Output) zurück."""
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = _strip_ansi((res.stdout + res.stderr).strip())
            return res.returncode == 0, output
        except subprocess.TimeoutExpired:
            return False, "Zeitüberschreitung"
        except FileNotFoundError:
            return False, f"Befehl nicht gefunden: {cmd[0]}"
        except Exception as exc:
            return False, str(exc)

    def _btctl(self, args: List[str], timeout: int = 8) -> tuple[bool, str]:
        """Shorthand: führt 'bluetoothctl <args>' aus."""
        return self._run(["bluetoothctl"] + args, timeout=timeout)

    def _btctl_session(self, commands: List[str], timeout: int = 50) -> str:
        """
        Führt mehrere bluetoothctl-Befehle in einer einzigen interaktiven Sitzung
        aus, indem alle Befehle über stdin übergeben werden.

        Notwendig für headless Pairing: Der Bluetooth-Agent (NoInputNoOutput)
        muss in derselben Sitzung aktiv sein, bevor 'pair' aufgerufen wird.
        Gibt die gesamte bereinigte Ausgabe zurück.
        """
        input_data = "\n".join(commands) + "\nquit\n"
        try:
            result = subprocess.run(
                ["bluetoothctl"],
                input=input_data,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return _strip_ansi((result.stdout + result.stderr).strip())
        except subprocess.TimeoutExpired:
            return "Zeitüberschreitung"
        except FileNotFoundError:
            return "bluetoothctl nicht gefunden"
        except Exception as exc:
            return str(exc)

    def _parse_info_block(self, output: str) -> Dict[str, Any]:
        """
        Parst die Ausgabe von 'bluetoothctl info <mac>' in ein Dict.
        Felder: name, icon, device_type, paired, trusted, blocked,
                connected, rssi, uuids
        """
        info: Dict[str, Any] = {
            "name":        "",
            "icon":        "",
            "device_type": "Unbekannt",
            "paired":      False,
            "trusted":     False,
            "blocked":     False,
            "connected":   False,
            "rssi":        None,
            "uuids":       [],
        }
        for raw in output.splitlines():
            line = raw.strip()
            if line.startswith("Name:"):
                info["name"] = line[5:].strip()
            elif line.startswith("Alias:") and not info["name"]:
                info["name"] = line[6:].strip()
            elif line.startswith("Icon:"):
                icon = line[5:].strip()
                info["icon"]        = icon
                info["device_type"] = _icon_to_type_label(icon)
            elif line.startswith("Paired:"):
                info["paired"] = "yes" in line.lower()
            elif line.startswith("Trusted:"):
                info["trusted"] = "yes" in line.lower()
            elif line.startswith("Blocked:"):
                info["blocked"] = "yes" in line.lower()
            elif line.startswith("Connected:"):
                info["connected"] = "yes" in line.lower()
            elif line.startswith("RSSI:"):
                try:
                    info["rssi"] = int(line.split(":")[1].strip())
                except (ValueError, IndexError):
                    pass
            elif "UUID:" in line:
                # Format: "UUID: Audio Sink   (0000110b-...)"
                uuid_m = re.search(r"\(([0-9a-f\-]{36})\)", line)
                name_m = re.match(r"UUID:\s+(.+?)\s+\(", line)
                if uuid_m and name_m:
                    info["uuids"].append({
                        "name": name_m.group(1).strip(),
                        "uuid": uuid_m.group(1),
                    })
        return info

    # ─── Adapter-Verwaltung ──────────────────────────────────────────────────

    def get_adapter_status(self) -> Dict[str, Any]:
        """
        Liefert den aktuellen Status des primären Bluetooth-Adapters.
        Gibt auch den Scan-Status zurück.
        """
        ok, output = self._btctl(["show"])
        if not ok and ("not found" in output.lower() or "unavailable" in output.lower()):
            return {
                "available":    False,
                "error":        (
                    "Kein Bluetooth-Adapter gefunden. "
                    "Bitte 'bluez' installieren und Adapter aktivieren."
                ),
                "scanning":     False,
            }

        status: Dict[str, Any] = {
            "available":    True,
            "powered":      False,
            "discoverable": False,
            "pairable":     False,
            "scanning":     self._scan_active,
            "name":         "",
            "mac":          "",
        }
        for raw in output.splitlines():
            line = raw.strip()
            if line.startswith("Controller"):
                parts = line.split()
                if len(parts) >= 2:
                    status["mac"] = parts[1]
            elif line.startswith("Name:"):
                status["name"] = line[5:].strip()
            elif "Powered:" in line:
                status["powered"]      = "yes" in line.lower()
            elif "Discoverable:" in line:
                status["discoverable"] = "yes" in line.lower()
            elif "Pairable:" in line:
                status["pairable"]     = "yes" in line.lower()

        return status

    def adapter_power(self, on: bool) -> Dict[str, Any]:
        """Schaltet den Bluetooth-Adapter ein oder aus."""
        # rfkill-Sperre aufheben (falls vorhanden)
        if on:
            self._run(["rfkill", "unblock", "bluetooth"], timeout=3)
        ok, output = self._btctl(["power", "on" if on else "off"])
        return {"ok": ok, "message": output}

    def adapter_discoverable(self, on: bool) -> Dict[str, Any]:
        """Macht den Adapter für andere Geräte sichtbar oder unsichtbar."""
        ok, output = self._btctl(["discoverable", "on" if on else "off"])
        return {"ok": ok, "message": output}

    def adapter_pairable(self, on: bool) -> Dict[str, Any]:
        """Aktiviert oder deaktiviert den Pairing-Modus."""
        ok, output = self._btctl(["pairable", "on" if on else "off"])
        return {"ok": ok, "message": output}

    # ─── Gerätescan ──────────────────────────────────────────────────────────

    def start_scan(self, duration_sec: int = 12) -> Dict[str, Any]:
        """
        Startet einen Bluetooth-Scan im Hintergrund-Thread.
        Bereitet den Adapter vor (Power-on, Pairable, Discoverable).
        """
        with self._lock:
            if self._scan_active:
                elapsed   = time.monotonic() - self._scan_start_time
                remaining = max(0, self._scan_duration - int(elapsed))
                return {
                    "ok":    False,
                    "error": f"Scan läuft bereits (noch {remaining}s)",
                }
            self._scan_devices.clear()
            self._scan_active     = True
            self._scan_start_time = time.monotonic()
            self._scan_duration   = duration_sec

        # Adapter vorbereiten (kurz blockierend)
        self._run(["rfkill", "unblock", "bluetooth"], timeout=3)
        self._btctl(["power", "on"],         timeout=5)
        self._btctl(["pairable",     "on"],  timeout=5)
        self._btctl(["discoverable", "on"],  timeout=5)

        thread = threading.Thread(
            target=self._scan_thread_func,
            args=(duration_sec,),
            daemon=True,
        )
        self._scan_thread = thread
        thread.start()

        return {
            "ok":          True,
            "message":     f"Scan gestartet für {duration_sec} Sekunden",
            "duration_sec": duration_sec,
        }

    def stop_scan(self) -> Dict[str, Any]:
        """Bricht den laufenden Scan ab."""
        with self._lock:
            if not self._scan_active:
                return {"ok": True, "message": "Kein aktiver Scan"}
            self._scan_active = False
        self._btctl(["scan", "off"], timeout=5)
        return {"ok": True, "message": "Scan gestoppt"}

    def get_scan_status(self) -> Dict[str, Any]:
        """Gibt Scan-Status und die bisher gefundenen Geräte zurück."""
        with self._lock:
            active    = self._scan_active
            elapsed   = time.monotonic() - self._scan_start_time if active else 0
            remaining = max(0, self._scan_duration - int(elapsed)) if active else 0
            duration  = self._scan_duration
            devices   = list(self._scan_devices.values())

        # Sortierung: stärkeres Signal zuerst; unbekannte RSSI ans Ende
        devices_sorted = sorted(
            devices,
            key=lambda d: d.get("rssi") if d.get("rssi") is not None else -999,
            reverse=True,
        )
        return {
            "scanning":      active,
            "elapsed_sec":   int(elapsed),
            "remaining_sec": remaining,
            "duration_sec":  duration,
            "devices_found": len(devices),
            "devices":       devices_sorted,
        }

    def _scan_thread_func(self, duration_sec: int) -> None:
        """
        Hintergrund-Thread: Führt 'bluetoothctl --timeout N scan on' aus.
        bluetoothctl gibt [NEW]/[CHG] Device-Zeilen für entdeckte Geräte aus.
        Nach dem Scan werden bekannte Geräte mit Detailinfos angereichert.
        """
        try:
            result = subprocess.run(
                ["bluetoothctl", "--timeout", str(duration_sec + 2), "scan", "on"],
                capture_output=True,
                text=True,
                timeout=duration_sec + 6,
            )
            output = _strip_ansi(result.stdout + result.stderr)
            self._parse_scan_output(output)
        except subprocess.TimeoutExpired:
            logger.debug("Bluetooth-Scan regulär abgelaufen")
        except FileNotFoundError:
            logger.error("bluetoothctl nicht gefunden – BlueZ installieren")
        except Exception as exc:
            logger.error("Scan-Thread-Fehler: %s", exc)
        finally:
            with self._lock:
                self._scan_active = False

            # Scan stoppen (Sicherheitsnetz)
            try:
                subprocess.run(
                    ["bluetoothctl", "scan", "off"],
                    capture_output=True,
                    timeout=4,
                )
            except Exception:
                pass

            # Gerätecache mit 'bluetoothctl devices' + info anreichern
            self._enrich_scan_results()

    def _parse_scan_output(self, output: str) -> None:
        """Verarbeitet die Rohausgabe des Scan-Subprozesses."""
        for line in output.splitlines():
            line = line.strip()

            m = _RE_NEW_DEV.search(line)
            if m:
                mac  = m.group(1).upper()
                name = m.group(2).strip()
                with self._lock:
                    if mac not in self._scan_devices:
                        self._scan_devices[mac] = {
                            "mac":         mac,
                            "name":        name or mac,
                            "rssi":        None,
                            "device_type": "Unbekannt",
                            "icon":        "",
                            "paired":      False,
                            "connected":   False,
                            "trusted":     False,
                        }
                continue

            m = _RE_CHG_RSSI.search(line)
            if m:
                mac, rssi = m.group(1).upper(), int(m.group(2))
                with self._lock:
                    if mac in self._scan_devices:
                        self._scan_devices[mac]["rssi"] = rssi
                continue

            m = _RE_CHG_NAME.search(line)
            if m:
                mac, name = m.group(1).upper(), m.group(2).strip()
                with self._lock:
                    if mac in self._scan_devices and name:
                        self._scan_devices[mac]["name"] = name
                continue

            m = _RE_CHG_ICON.search(line)
            if m:
                mac, icon = m.group(1).upper(), m.group(2).strip()
                with self._lock:
                    if mac in self._scan_devices and icon:
                        self._scan_devices[mac]["icon"]        = icon
                        self._scan_devices[mac]["device_type"] = _icon_to_type_label(icon)

    def _enrich_scan_results(self) -> None:
        """
        Lädt alle bekannten bluetoothctl-Geräte und fügt ggf. fehlende
        Geräte zum Scan-Cache hinzu. Dann werden Detailinfos per 'info'
        für jeden Eintrag geladen.
        """
        _, out = self._btctl(["devices"])
        for line in out.splitlines():
            m = _RE_DEV_LINE.search(_strip_ansi(line))
            if m:
                mac, name = m.group(1).upper(), m.group(2).strip()
                with self._lock:
                    if mac not in self._scan_devices:
                        self._scan_devices[mac] = {
                            "mac":         mac,
                            "name":        name or mac,
                            "rssi":        None,
                            "device_type": "Unbekannt",
                            "icon":        "",
                            "paired":      False,
                            "connected":   False,
                            "trusted":     False,
                        }

        with self._lock:
            macs = list(self._scan_devices.keys())

        for mac in macs:
            try:
                _, info_out = self._btctl(["info", mac], timeout=5)
                info = self._parse_info_block(_strip_ansi(info_out))
                with self._lock:
                    if mac in self._scan_devices:
                        dev = self._scan_devices[mac]
                        if info.get("name"):
                            dev["name"] = info["name"]
                        if info.get("icon"):
                            dev["icon"]        = info["icon"]
                            dev["device_type"] = _icon_to_type_label(info["icon"])
                        dev["paired"]    = info.get("paired",    False)
                        dev["connected"] = info.get("connected", False)
                        dev["trusted"]   = info.get("trusted",   False)
                        if info.get("rssi") is not None:
                            dev["rssi"] = info["rssi"]
            except Exception as exc:
                logger.debug("Gerät %s – Info-Fehler: %s", mac, exc)

    # ─── Geräteverwaltung ────────────────────────────────────────────────────

    def get_known_devices(self) -> List[Dict[str, Any]]:
        """
        Gibt alle bei bluetoothctl bekannten Geräte mit Detailinfos zurück.
        Gepaarte Geräte werden zuerst aufgelistet.
        """
        _, out = self._btctl(["devices"])
        devices: List[Dict[str, Any]] = []

        for line in out.splitlines():
            line = _strip_ansi(line.strip())
            m = _RE_DEV_LINE.search(line)
            if not m:
                continue
            mac, fallback_name = m.group(1).upper(), m.group(2).strip()

            _, info_out = self._btctl(["info", mac], timeout=5)
            info = self._parse_info_block(_strip_ansi(info_out))

            devices.append({
                "mac":         mac,
                "name":        info.get("name") or fallback_name or mac,
                "icon":        info.get("icon", ""),
                "device_type": _icon_to_type_label(info.get("icon", "")),
                "paired":      info.get("paired",    False),
                "trusted":     info.get("trusted",   False),
                "blocked":     info.get("blocked",   False),
                "connected":   info.get("connected", False),
                "rssi":        info.get("rssi"),
                "uuids":       info.get("uuids", []),
            })

        # Sortierung: verbunden → gepairt → restliche
        def _sort_key(d: Dict[str, Any]) -> int:
            if d.get("connected"): return 0
            if d.get("paired"):    return 1
            return 2

        return sorted(devices, key=_sort_key)

    def pair_device(self, mac: str) -> Dict[str, Any]:
        """
        Vollständiger headless Pairing-Ablauf in einer einzigen bluetoothctl-Sitzung:
          1. agent NoInputNoOutput  – headless Pairing ohne PIN/Bestätigung
          2. default-agent          – Agent als Standard registrieren
          3. pair <mac>             – Pairing starten
          4. trust <mac>            – Autoverbindung erlauben
          5. connect <mac>          – Verbindung aufbauen

        Alle Schritte müssen in derselben Sitzung laufen, da der Agent-Kontext
        nicht über separate Prozessaufrufe hinweg erhalten bleibt.
        """
        mac = mac.upper()
        logger.info("Pairing starten (headless): %s", mac)

        output = self._btctl_session(
            [
                "agent NoInputNoOutput",
                "default-agent",
                f"pair {mac}",
                f"trust {mac}",
                f"connect {mac}",
            ],
            timeout=55,
        )
        logger.info("Pairing-Ausgabe für %s: %s", mac, output)

        out_lower      = output.lower()
        already_paired = "already paired" in out_lower
        pair_ok        = "pairing successful" in out_lower or already_paired

        if not pair_ok:
            return {"ok": False, "error": f"Pairing fehlgeschlagen: {output}"}

        connected = (
            "connection successful" in out_lower
            or ("connected" in out_lower and "not connected" not in out_lower)
        )

        return {
            "ok":        True,
            "message":   "Erfolgreich gepairt und verbunden" if connected
                         else f"Gepairt, Verbindung ausstehend: {output}",
            "paired":    True,
            "connected": connected,
        }

    def trust_device(self, mac: str, trust: bool = True) -> Dict[str, Any]:
        """Markiert ein Gerät als vertrauenswürdig (oder hebt es auf)."""
        mac = mac.upper()
        cmd = "trust" if trust else "untrust"
        ok, out = self._btctl([cmd, mac], timeout=5)
        return {"ok": ok, "trusted": trust, "message": out}

    def connect_device(self, mac: str) -> Dict[str, Any]:
        """Verbindet ein bereits bekanntes Gerät."""
        mac = mac.upper()
        ok, out = self._btctl(["connect", mac], timeout=15)
        connected = ok or "successful" in out.lower() or "connected" in out.lower()
        return {"ok": connected, "message": out, "connected": connected}

    def disconnect_device(self, mac: str) -> Dict[str, Any]:
        """Trennt die Verbindung zu einem Gerät."""
        mac = mac.upper()
        ok, out = self._btctl(["disconnect", mac], timeout=10)
        return {"ok": ok, "message": out}

    def remove_device(self, mac: str) -> Dict[str, Any]:
        """Entfernt und unpairt ein Gerät vollständig."""
        mac = mac.upper()
        ok, out = self._btctl(["remove", mac], timeout=10)
        with self._lock:
            self._scan_devices.pop(mac, None)
        return {"ok": ok, "message": out}
