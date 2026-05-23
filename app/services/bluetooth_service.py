"""
Bluetooth-Geräteverwaltung über bluetoothctl (BlueZ).

System-Abhängigkeiten:
  - bluetoothctl  (Paket: bluez)
  - rfkill        (optional, für Adapter-Unblocking)

Kein externer Python-Pip-Paket erforderlich.
"""
from __future__ import annotations

import logging
import os
import pty
import queue as _queue
import re
import select as _select
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
        # SSE: Subscriber-Queues für Echtzeit-Events
        self._subscribers: List[_queue.Queue] = []
        self._sub_lock = threading.Lock()

    # ─── SSE Event-Bus ──────────────────────────────────────────────────────────

    def subscribe(self) -> "_queue.Queue[Dict[str, Any]]":
        """Registriert einen neuen SSE-Subscriber und gibt seine Queue zurück."""
        q: _queue.Queue[Dict[str, Any]] = _queue.Queue(maxsize=100)
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: "_queue.Queue[Dict[str, Any]]") -> None:
        """Entfernt einen Subscriber."""
        with self._sub_lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def _emit(self, event_type: str, data: Dict[str, Any]) -> None:
        """Sendet ein Event an alle Subscriber. Volle Queues werden entfernt."""
        with self._sub_lock:
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait({"type": event_type, "data": data})
                except _queue.Full:
                    dead.append(q)
            for q in dead:
                try:
                    self._subscribers.remove(q)
                except ValueError:
                    pass

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

    def _btctl_session(self, commands: List[str], timeout: int = 60) -> str:
        """
        Führt mehrere bluetoothctl-Befehle in einer einzigen Sitzung aus.

        Verwendet ein Pseudo-Terminal (PTY) statt einer Pipe, damit bluetoothctl
        vollständig initialisiert wird — inklusive Agent-Registrierung.

        Kritischer Unterschied zur Pipe-Variante: Jedes _read_until merkt sich
        einen Baseline-Index im Puffer und wertet nur den neuen Anteil aus.
        Ohne Baseline würde der `#`-Prompt aus einem früheren asynchronen Event
        (z. B. "Agent registered") als Abschluss des aktuellen Befehls erkannt
        und der nächste Befehl zu früh gesendet.

        Für `pair` und `connect` wird zusätzlich auf semantische Erfolgsmuster
        gewartet statt nur auf den Prompt, damit langsame Geräteantworten nicht
        versehentlich abgeschnitten werden.
        """
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            ["bluetoothctl"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
        os.close(slave_fd)

        all_output = b""

        def _read_until(patterns: List[str], per_timeout: float) -> str:
            """
            Liest vom PTY bis ein Muster in der neuen Ausgabe (seit Aufruf) auftaucht
            oder der Timeout abläuft.  Gibt den neuen Ausgabe-String zurück.
            """
            nonlocal all_output
            baseline = len(all_output)
            end = time.monotonic() + per_timeout
            while time.monotonic() < end:
                remaining = end - time.monotonic()
                ready, _, _ = _select.select([master_fd], [], [], min(0.2, remaining))
                if ready:
                    try:
                        chunk = os.read(master_fd, 4096)
                        if chunk:
                            all_output += chunk
                    except OSError:
                        break
                new = _strip_ansi(all_output[baseline:].decode("utf-8", errors="replace"))
                for p in patterns:
                    if p.lower() in new.lower():
                        # Kurz nachdrainieren – es könnten noch Folgezeilen kommen
                        time.sleep(0.15)
                        try:
                            r2, _, _ = _select.select([master_fd], [], [], 0.15)
                            if r2:
                                all_output += os.read(master_fd, 4096)
                        except OSError:
                            pass
                        return _strip_ansi(
                            all_output[baseline:].decode("utf-8", errors="replace")
                        )
            return _strip_ansi(all_output[baseline:].decode("utf-8", errors="replace"))

        # Erkennungsmuster für den Ruhezustand-Prompt "[bluetooth]# "
        PROMPT = ["[bluetooth]# "]

        try:
            # Warten auf den initialen [bluetooth]#-Prompt
            _read_until(PROMPT + ["[bluetooth]#\n", "[bluetooth]#\r"], 7)

            for cmd in commands:
                if proc.poll() is not None:
                    break
                os.write(master_fd, (cmd + "\n").encode())

                if "pair" in cmd:
                    # Auf explizites Ergebnis warten (nicht nur Prompt), da
                    # bluetoothctl bei laufendem Pairing asynchrone Events ausgibt
                    _read_until(
                        [
                            "Pairing successful",
                            "Failed to pair",
                            "already paired",
                            "not available",
                            "not ready",
                        ] + PROMPT,
                        35,
                    )
                elif "connect" in cmd:
                    _read_until(
                        [
                            "Connection successful",
                            "Failed to connect",
                            "already connected",
                        ] + PROMPT,
                        20,
                    )
                elif "agent" in cmd.lower():
                    # Agent-Registrierung ist asynchron; auf "registered" warten
                    _read_until(
                        ["Agent registered", "agent registered", "Failed to register"] + PROMPT,
                        7,
                    )
                else:
                    _read_until(PROMPT, 7)

            os.write(master_fd, b"quit\n")
            _read_until(PROMPT + ["[bluetooth]#"], 3)

        except OSError:
            pass
        finally:
            try:
                os.close(master_fd)
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

        return _strip_ansi(all_output.decode("utf-8", errors="replace"))

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

        self._emit("scan_status", {
            "scanning": True, "elapsed_sec": 0,
            "remaining_sec": duration_sec, "duration_sec": duration_sec,
            "devices_found": 0,
        })

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
        self._emit("scan_status", self.get_scan_status())
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
        Hintergrund-Thread: liest bluetoothctl-Ausgabe zeilenweise (Echtzeit)
        und emittiert SSE-Events für neue/aktualisierte Geräte und Statusänderungen.
        """
        _last_status_emit = time.monotonic()
        try:
            proc = subprocess.Popen(
                ["bluetoothctl", "--timeout", str(duration_sec + 2), "scan", "on"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for raw_line in proc.stdout:  # type: ignore[union-attr]
                with self._lock:
                    active = self._scan_active
                if not active:
                    proc.terminate()
                    break
                self._parse_scan_line(_strip_ansi(raw_line.strip()))
                # Periodisches Status-Emit (~jede Sekunde) für den Fortschrittsbalken
                now = time.monotonic()
                if now - _last_status_emit >= 1.0:
                    _last_status_emit = now
                    self._emit("scan_status", self.get_scan_status())
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except FileNotFoundError:
            logger.error("bluetoothctl nicht gefunden – BlueZ installieren")
        except Exception as exc:
            logger.error("Scan-Thread-Fehler: %s", exc)
        finally:
            with self._lock:
                self._scan_active = False
            try:
                subprocess.run(
                    ["bluetoothctl", "scan", "off"],
                    capture_output=True,
                    timeout=4,
                )
            except Exception:
                pass
            # Geräte mit Detailinfos anreichern, dann finales Status-Event
            self._enrich_scan_results()
            self._emit("scan_status", self.get_scan_status())

    def _parse_scan_line(self, line: str) -> None:
        """Verarbeitet eine einzelne Zeile aus der Scan-Ausgabe in Echtzeit.
        Emittiert 'scan_device' sobald ein Gerät neu erscheint oder sich ändert."""
        emit_dev: Optional[Dict[str, Any]] = None

        m = _RE_NEW_DEV.search(line)
        if m:
            mac  = m.group(1).upper()
            name = m.group(2).strip()
            with self._lock:
                if mac not in self._scan_devices:
                    self._scan_devices[mac] = {
                        "mac": mac, "name": name or mac, "rssi": None,
                        "device_type": "Unbekannt", "icon": "",
                        "paired": False, "connected": False, "trusted": False,
                    }
                    emit_dev = dict(self._scan_devices[mac])
            if emit_dev:
                self._emit("scan_device", emit_dev)
            return

        m = _RE_CHG_RSSI.search(line)
        if m:
            mac, rssi = m.group(1).upper(), int(m.group(2))
            with self._lock:
                if mac in self._scan_devices:
                    self._scan_devices[mac]["rssi"] = rssi
                    emit_dev = dict(self._scan_devices[mac])
            if emit_dev:
                self._emit("scan_device", emit_dev)
            return

        m = _RE_CHG_NAME.search(line)
        if m:
            mac, name = m.group(1).upper(), m.group(2).strip()
            with self._lock:
                if mac in self._scan_devices and name:
                    self._scan_devices[mac]["name"] = name
                    emit_dev = dict(self._scan_devices[mac])
            if emit_dev:
                self._emit("scan_device", emit_dev)
            return

        m = _RE_CHG_ICON.search(line)
        if m:
            mac, icon = m.group(1).upper(), m.group(2).strip()
            with self._lock:
                if mac in self._scan_devices and icon:
                    self._scan_devices[mac]["icon"]        = icon
                    self._scan_devices[mac]["device_type"] = _icon_to_type_label(icon)
                    emit_dev = dict(self._scan_devices[mac])
            if emit_dev:
                self._emit("scan_device", emit_dev)

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
                emit_dev: Optional[Dict[str, Any]] = None
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
                        emit_dev = dict(dev)
                if emit_dev:
                    self._emit("scan_device", emit_dev)
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

    def _pair_interactive(self, mac: str) -> str:
        """
        PTY-basierter Pairing-Ablauf: pair → trust → connect.

        Agent-Setup: Wir registrieren explizit 'NoInputNoOutput' — der richtige
        Typ für BT-Lautsprecher/Headsets ("Just Works"-Pairing).  Falls BlueZ
        bereits einen Agent auto-registriert hat, melden wir ihn kurz ab und
        registrieren neu.  Fehler beim default-agent werden ignoriert, da BlueZ
        ihn oft automatisch setzt.

        Aufrufbedingung: Kein aktiver Scan-Prozess (Scan muss vorher gestoppt
        werden), damit kein fremder Agent den Pairable-Status zurücksetzt.
        """
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            ["bluetoothctl"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
        os.close(slave_fd)

        buf: bytes = b""

        def _send(text: str) -> None:
            os.write(master_fd, (text + "\n").encode())

        def _read(t: float = 0.15) -> bytes:
            nonlocal buf
            r, _, _ = _select.select([master_fd], [], [], t)
            if r:
                try:
                    chunk = os.read(master_fd, 4096)
                    if chunk:
                        buf += chunk
                        return chunk
                except OSError:
                    pass
            return b""

        def _wait(
            done_patterns: List[str],
            timeout: float,
            respond: Optional[Dict[str, str]] = None,
        ) -> str:
            nonlocal buf
            baseline   = len(buf)
            end        = time.monotonic() + timeout
            responded: set = set()
            while time.monotonic() < end:
                _read(0.1)
                new = _strip_ansi(buf[baseline:].decode("utf-8", errors="replace"))
                nl  = new.lower()
                if respond:
                    for trigger, reply in respond.items():
                        if trigger.lower() in nl and trigger not in responded:
                            responded.add(trigger)
                            time.sleep(0.1)
                            logger.info("Pairing: '%s' erkannt → sende '%s'", trigger, reply)
                            _send(reply)
                for p in done_patterns:
                    if p.lower() in nl:
                        _read(0.2)
                        return _strip_ansi(buf[baseline:].decode("utf-8", errors="replace"))
            return _strip_ansi(buf[baseline:].decode("utf-8", errors="replace"))

        try:
            # Auf Bereitschaft warten (Prompt oder Auto-Agent-Meldung)
            _wait(["agent registered", "# "], 8)

            # ── Agent-Setup ────────────────────────────────────────────────────
            # Vorhandenen Agent abmelden (auch wenn keiner da ist — kein Fehler)
            _send("agent off")
            _wait(["# "], 3)

            # NoInputNoOutput: korrekt für BT-Lautsprecher ohne Display/Tastatur
            _send("agent NoInputNoOutput")
            _wait(["agent registered", "already registered", "# "], 5)

            # Als Default-Agent setzen; Fehler "DoesNotExist" ignorieren
            _send("default-agent")
            _wait(["default agent request successful", "DoesNotExist", "# "], 5)

            # ── Pairing ────────────────────────────────────────────────────────
            # Kein Prompt in done_patterns: '[bluetooth]# ' kommt sofort nach
            # dem Pairing-Start, das eigentliche Ergebnis folgt asynchron.
            _send(f"pair {mac}")
            _wait(
                done_patterns=[
                    "Pairing successful",
                    "Failed to pair",
                    "already paired",
                    "Paired: yes",
                ],
                timeout=35,
                respond={
                    "Enter PIN code":       "0000",
                    "Confirm passkey":      "yes",
                    "Request confirmation": "yes",
                },
            )

            # Trust immer setzen — auch wenn Pairing-Meldung zweideutig
            _send(f"trust {mac}")
            _wait(["trust succeeded", "trusted: yes", "# "], 7)

            # Verbinden
            _send(f"connect {mac}")
            _wait(
                done_patterns=[
                    "Connection successful",
                    "Failed to connect",
                    "Connected: yes",
                ],
                timeout=20,
            )

            _send("quit")
            _read(1.0)

        except OSError:
            pass
        finally:
            try:
                os.close(master_fd)
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

        return _strip_ansi(buf.decode("utf-8", errors="replace"))

    def pair_device(self, mac: str) -> Dict[str, Any]:
        """
        Vollständiger Pairing-Ablauf (pair → trust → connect).

        Scan wird vorher gestoppt, damit kein paralleler bluetoothctl-Prozess
        den Agent oder den Pairable-Status zurücksetzt.
        """
        mac = mac.upper()
        logger.info("Pairing starten: %s", mac)
        self._emit("action_progress", {"action": "pair", "mac": mac, "message": "Adapter wird vorbereitet\u2026"})

        # ── Scan stoppen ────────────────────────────────────────────────────────
        # Kritisch: Der Scan-Prozess registriert einen eigenen Agent.  Wenn er
        # endet, zieht er den Agent zurück und setzt Pairable: no – genau während
        # des Pairings.  Durch explizites Stoppen vor dem Pairing vermeiden wir
        # diesen Race.
        with self._lock:
            was_scanning = self._scan_active
            self._scan_active = False
        if was_scanning:
            try:
                subprocess.run(
                    ["bluetoothctl", "scan", "off"],
                    capture_output=True, timeout=4,
                )
            except Exception:
                pass
            time.sleep(0.5)   # BlueZ kurz Zeit geben, Scan-State aufzuräumen

        # ── Adapter in Pairing-Modus ────────────────────────────────────────────
        self._run(["rfkill", "unblock", "bluetooth"], timeout=3)
        self._btctl(["power",        "on"], timeout=5)
        self._btctl(["pairable",     "on"], timeout=5)
        self._btctl(["discoverable", "on"], timeout=5)

        self._emit("action_progress", {"action": "pair", "mac": mac, "message": "Pairing l\u00E4uft\u2026 (30\u201340s)"})
        output    = self._pair_interactive(mac)
        out_lower = output.lower()
        logger.info("Pairing-Ausgabe für %s:\n%s", mac, output)
        self._emit("action_progress", {"action": "pair", "mac": mac, "message": "Ger\u00E4testatus wird gepr\u00FCft\u2026"})

        # ── Erfolgs-Erkennung ───────────────────────────────────────────────────
        pair_ok = (
            "pairing successful" in out_lower
            or "paired: yes"     in out_lower
            or "already paired"  in out_lower
        )

        # Immer per 'info' verifizieren — zuverlässigster Statuskanal
        _, info_out = self._btctl(["info", mac], timeout=6)
        info        = self._parse_info_block(_strip_ansi(info_out))

        if not pair_ok:
            pair_ok = info.get("paired", False)

        if not pair_ok:
            result = {"ok": False, "error": f"Pairing fehlgeschlagen: {output}"}
            self._emit("action_done", {"action": "pair", "mac": mac, **result})
            return result

        connected = (
            "connection successful" in out_lower
            or "connected: yes"     in out_lower
            or info.get("connected", False)
        )
        result = {
            "ok":        True,
            "message":   "Verbunden & gespeichert" if connected
                         else "Gespeichert \u2014 Ger\u00E4t einschalten und erneut verbinden.",
            "paired":    True,
            "trusted":   True,
            "connected": connected,
        }
        self._emit("action_done", {"action": "pair", "mac": mac, **result})
        return result

    def trust_device(self, mac: str, trust: bool = True) -> Dict[str, Any]:
        """Markiert ein Gerät als vertrauenswürdig (oder hebt es auf)."""
        mac = mac.upper()
        cmd = "trust" if trust else "untrust"
        ok, out = self._btctl([cmd, mac], timeout=5)
        return {"ok": ok, "trusted": trust, "message": out}

    def connect_device(self, mac: str) -> Dict[str, Any]:
        """
        Verbindet ein bereits bekanntes Gerät.

        Bei 'br-connection-unknown' (häufig bei BT-Lautsprechern wenn BlueZ
        das Audioprofil noch nicht bereit hat): zweiter Versuch nach kurzer Pause.
        """
        mac = mac.upper()
        ok, out = self._btctl(["connect", mac], timeout=15)
        out_l = out.lower()

        if not ok and "br-connection-unknown" in out_l.replace(" ", ""):
            logger.info("connect %s: br-connection-unknown – zweiter Versuch", mac)
            time.sleep(2)
            ok, out = self._btctl(["connect", mac], timeout=15)
            out_l = out.lower()

        connected = ok or "successful" in out_l or "connected: yes" in out_l
        result = {"ok": connected, "message": out, "connected": connected}
        self._emit("action_done", {"action": "connect", "mac": mac, **result})
        return result

    def disconnect_device(self, mac: str) -> Dict[str, Any]:
        """Trennt die Verbindung zu einem Gerät."""
        mac = mac.upper()
        ok, out = self._btctl(["disconnect", mac], timeout=10)
        result = {"ok": ok, "message": out}
        self._emit("action_done", {"action": "disconnect", "mac": mac, **result})
        return result

    def remove_device(self, mac: str) -> Dict[str, Any]:
        """Entfernt und unpairt ein Gerät vollständig."""
        mac = mac.upper()
        ok, out = self._btctl(["remove", mac], timeout=10)
        with self._lock:
            self._scan_devices.pop(mac, None)
        result = {"ok": ok, "message": out}
        self._emit("action_done", {"action": "remove", "mac": mac, **result})
        return result
