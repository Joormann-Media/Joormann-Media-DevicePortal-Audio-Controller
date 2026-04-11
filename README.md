# Joormann-Media-DevicePortal-Audio-Controller

Windows-Mixer-ahnliches Audio Control Center fur Linux (PipeWire/Pulse/ALSA) auf Flask-Basis.

## Kernpunkte

- Saubere Trennung: Output-Lautstarke vs. Input-/Source-Lautstarke
- `Base Volume` separat als Referenzwert (nicht als Hauptslider)
- Robustes Parsing von `pactl list sinks/sources` inkl. Mehrkanal-Volume (`raw/%/dB`)
- Per-Device Fallback uber `pactl get-sink/source-volume` und `...-mute`
- Optional erkannte Hardware-Regler (Capture Gain / Mic Boost) uber `amixer`
- Live-Meter (RMS/Peak) und Mikrofon-Testaufnahme mit Bewertung

## Projektstruktur

- `app/services/audio_backend.py`: Rohdaten + Parsing (`pactl/wpctl/alsa/amixer`)
- `app/services/audio_normalize.py`: Normalisierung, Klassifizierung, Volume-/Gain-Modell
- `app/services/audio_control.py`: Regler setzen (output/input/gain/boost)
- `app/services/audio_meter.py`: Live-Metering
- `app/services/audio_recorder.py`: Testaufnahme + RMS/Peak-Analyse
- `app/services/audio_service.py`: Orchestrierung
- `app/routes/audio.py`: UI + API

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
- `POST /api/audio/device/<stable_id>/set-output-volume` body `{ "volume_percent": 40 }`
- `POST /api/audio/device/<stable_id>/set-input-volume` body `{ "volume_percent": 100 }`
- `POST /api/audio/device/<stable_id>/set-output-mute` body `{ "mute": true }`
- `POST /api/audio/device/<stable_id>/set-input-mute` body `{ "mute": false }`
- `POST /api/audio/device/<stable_id>/set-capture-gain` body `{ "value_percent": 65 }`
- `POST /api/audio/device/<stable_id>/set-mic-boost` body `{ "value_percent": 20 }`
- `POST /api/audio/device/<stable_id>/set-hardware-gain` body `{ "value_percent": 65 }` oder `{ "raw_value": 12 }`
- `POST /api/audio/device/<stable_id>/set-alsa-control` body `{ "control_name": "Mic", "value_percent": 75 }`
- `POST /api/audio/device/<stable_id>/set-alsa-switch` body `{ "control_name": "Mic", "switch_on": true }`
- `POST /api/audio/device/<stable_id>/test-record` body `{ "duration_sec": 3 }`
- `GET  /api/audio/device/<stable_id>/test-record/latest.wav`

Legacy kompatibel:

- `POST /api/audio/device/<stable_id>/set-volume`
- `POST /api/audio/device/<stable_id>/set-mute`

## Hinweise

- Wenn `amixer` fur ein Geraet keine sinnvollen Controls liefert, werden keine Hardware-Gain-Slider angezeigt.
- Wenn `ffmpeg` fehlt, sind Meter/Testaufnahme nicht verfugbar.
- ALSA-Plugin-Listen (`aplay -L`/`arecord -L`) bleiben Diagnose-only.
