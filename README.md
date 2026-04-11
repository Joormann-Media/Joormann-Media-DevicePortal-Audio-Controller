# Joormann-Media-DevicePortal-Audio-Controller

Windows-Mixer-ahnliches Audio Control Center fur Linux (PipeWire/Pulse/ALSA) auf Flask-Basis.

## Features

- Saubere Trennung von Playback und Capture
- Robustere Erkennung uber `pactl` / `wpctl` + ALSA-Hardware-Metadaten (`aplay -l`, `arecord -l`)
- ALSA-Plugin-Listen (`aplay -L`, `arecord -L`) nur in Diagnose
- Deduplizierung und Normalisierung mit `stable_id`
- Default-Device setzen (Input/Output)
- Lautstarke setzen (Input/Output + Stream)
- Mute/Unmute
- Aktive Playback-Streams anzeigen
- Live-Update ohne Seitenreload via SSE (`/api/audio/events`)
- Live-Metering (best effort) uber `ffmpeg` + Pulse-Source/Monitor-Source

## Projektstruktur

- `app/__init__.py`: Flask App Factory
- `app/routes/audio.py`: UI + API Endpunkte
- `app/services/audio_backend.py`: Rohdaten-Erfassung + Parsing
- `app/services/audio_normalize.py`: Filter, Klassifizierung, Dedupe
- `app/services/audio_control.py`: Control-Aktionen
- `app/services/audio_meter.py`: Live-Pegelmessung
- `app/services/audio_service.py`: Orchestrierung/Snapshot
- `app/services/audio_diagnostics.py`: Diagnosepayload
- `app/models/audio_models.py`: Datenmodelle
- `app/templates/audio/*`: UI + Partials
- `app/static/js/audio-controller.js`: Frontend-Logik
- `app/static/css/audio-controller.css`: Styling

## Installation

```bash
cd /home/djanebmb/projects/Joormann-Media-DevicePortal-Audio-Controller
chmod +x scripts/*.sh
./scripts/install.sh
```

## Start

```bash
./scripts/run.sh
```

UI:

- `http://127.0.0.1:5071/audio`
- Expertenmodus: `http://127.0.0.1:5071/audio?expert=1`

## API

- `GET  /api/audio/summary`
- `GET  /api/audio/devices`
- `GET  /api/audio/streams`
- `GET  /api/audio/diagnostics`
- `GET  /api/audio/meters`
- `GET  /api/audio/events` (SSE)
- `POST /api/audio/device/<stable_id>/set-default`
- `POST /api/audio/device/<stable_id>/set-volume` body `{ "volume_percent": 55 }`
- `POST /api/audio/device/<stable_id>/set-mute` body `{ "mute": true }`
- `POST /api/audio/stream/<stream_id>/set-volume` body `{ "volume_percent": 70 }`

## Hinweise zur Robustheit

- Wenn `wpctl` fehlt: App lauft weiter, reduziert auf verfugbare Backends.
- Wenn `pactl` fehlt: viele Control-/Device-Funktionen sind eingeschrankt; Diagnose zeigt den Status.
- Wenn `ffmpeg` fehlt: Meters zeigen `Pegel nicht verfugbar`.
- Command-Timeouts und defensive Parser verhindern harte Absturze bei Teilfehlern.
