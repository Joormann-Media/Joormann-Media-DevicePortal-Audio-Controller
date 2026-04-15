(() => {
  const state = {
    snapshotHash: window.AUDIO_BOOTSTRAP.snapshot_hash,
    expertMode: window.AUDIO_BOOTSTRAP.expert_mode,
    meterRunning: window.AUDIO_BOOTSTRAP.meter_running,
    meterAutostart: window.AUDIO_BOOTSTRAP.meter_autostart,
  };

  async function getJson(url) {
    const res = await fetch(url, { cache: "no-store", headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  function qs(sel) {
    return document.querySelector(sel);
  }

  function meterBar(id, value) {
    const bar = qs(`[data-meter="${id}"]`);
    const text = qs(`[data-meter-text="${id}"]`);
    if (!bar || !text) return;
    if (!value || !value.available) {
      bar.style.width = "0%";
      if (value && value.reason === "meter_stopped") {
        text.textContent = "Meter nicht aktiv";
      } else {
        text.textContent = "Pegel nicht verfügbar";
      }
      return;
    }
    bar.style.width = `${value.peak_percent}%`;
    text.textContent = `RMS ${value.rms_percent}% | Peak ${value.peak_percent}%`;
  }

  function updateMeterBtn(running) {
    state.meterRunning = running;
    const btn = qs("#meter-toggle");
    if (!btn) return;
    if (running) {
      btn.textContent = "🎚 Pegel-Meter stoppen";
      btn.classList.remove("meter-inactive");
      btn.classList.add("meter-active");
    } else {
      btn.textContent = "🎚 Pegel-Meter starten";
      btn.classList.remove("meter-active");
      btn.classList.add("meter-inactive");
    }
  }

  function updateAutostartBadge(enabled) {
    state.meterAutostart = enabled;
    const badge = qs("#autostart-badge");
    if (!badge) return;
    badge.textContent = enabled ? "an" : "aus";
    badge.className = enabled ? "badge badge-on" : "badge badge-off";
  }

  async function refreshMeters() {
    if (!state.meterRunning) return;
    try {
      const data = await getJson("/api/audio/meters");
      Object.entries(data.meters || {}).forEach(([stableId, meter]) => meterBar(stableId, meter));
    } catch (_err) {
      // keep last state
    }
  }

  async function refreshSummaryAndTables() {
    const [summaryData, devicesData, streamsData] = await Promise.all([
      getJson("/api/audio/summary"),
      getJson(`/api/audio/devices?expert=${state.expertMode ? "1" : "0"}`),
      getJson("/api/audio/streams"),
    ]);

    state.snapshotHash = summaryData.snapshot_hash;
    qs("[data-kpi='outputs']").textContent = summaryData.summary.outputs_count;
    qs("[data-kpi='inputs']").textContent = summaryData.summary.inputs_count;
    qs("[data-kpi='default-output']").textContent = summaryData.summary.default_output || "-";
    qs("[data-kpi='default-input']").textContent = summaryData.summary.default_input || "-";
    qs("[data-kpi='updated']").textContent = summaryData.timestamp_utc;

    renderDevices("output", devicesData.devices.outputs || []);
    renderDevices("input", devicesData.devices.inputs || []);
    renderStreams(streamsData.streams || []);
    await loadCalibrations(devicesData.devices.inputs || []);

    if (state.expertMode) {
      const diag = await getJson("/api/audio/diagnostics");
      const pre = qs("#diag-json");
      if (pre) {
        pre.textContent = JSON.stringify(diag, null, 2);
      }
    }
  }

  function renderCalibration(stableId, payload) {
    const box = qs(`[data-calibration="${stableId}"]`);
    if (!box) return;
    if (!payload || !payload.ok || !payload.calibration) {
      box.innerHTML = `<div class="meta">Noch keine Kalibrierung vorhanden.</div>`;
      return;
    }
    const cal = payload.calibration;
    const a = cal.analysis || {};
    const rec = cal.recommendation || {};
    const applyBtn = rec.applicable
      ? `<button data-action="apply-calibration" data-id="${stableId}">Empfehlung übernehmen</button>`
      : "";
    box.innerHTML = `
      <div class="meta"><strong>Kalibrierung:</strong> ${escapeHtml(cal.message || "-")}</div>
      <div class="meta">RMS: ${a.rms_percent ?? "-"}% / ${a.rms_dbfs ?? "-"} dBFS | Peak: ${a.peak_percent ?? "-"}% / ${a.peak_dbfs ?? "-"} dBFS</div>
      <div class="meta">Stille-Anteil: ${a.silence_ratio ?? "-"} | Noise Floor: ${a.noise_floor_percent ?? "-"}%</div>
      <div class="meta"><strong>Empfehlung:</strong> ${escapeHtml(rec.summary || "Keine")}</div>
      <div class="meta">Vorschlag Source: ${rec.suggest_source_volume_percent ?? "-"}% | Hardware Gain: ${rec.suggest_hardware_gain_percent ?? "-"}%</div>
      <div class="actions">${applyBtn}</div>
    `;
  }

  async function loadCalibrations(inputDevices) {
    const tasks = (inputDevices || []).map(async (d) => {
      try {
        const data = await getJson(`/api/audio/device/${d.stable_id}/calibration`);
        renderCalibration(d.stable_id, data);
      } catch (_err) {
        renderCalibration(d.stable_id, null);
      }
    });
    await Promise.all(tasks);
  }

  function volumeForDevice(d, kind) {
    if (kind === "output") return Number.isInteger(d.volume_percent_current) ? d.volume_percent_current : 0;
    return Number.isInteger(d.source_volume_percent_current) ? d.source_volume_percent_current : 0;
  }

  function renderInputExtras(d) {
    const hwGainPercent = Number.isInteger(d.hardware_gain_percent) ? d.hardware_gain_percent : 0;
    const hwGain = d.hardware_gain_available
      ? `<label>${d.hardware_gain_kind === "mic_gain" ? "Mic Gain" : "Hardware Gain"}:</label>
         <input class="range" type="range" min="0" max="100" step="1" value="${hwGainPercent}" data-action="set-hardware-gain" data-id="${d.stable_id}" />
         <span>${hwGainPercent}%</span>
         <span class="meta-inline">${d.hardware_gain_db !== null && d.hardware_gain_db !== undefined ? `${escapeHtml(String(d.hardware_gain_db))} dB` : ""}</span>
         <span class="meta-inline">${Number.isInteger(d.hardware_gain_raw) ? `raw ${d.hardware_gain_raw}` : ""}${Number.isInteger(d.hardware_gain_min_raw) && Number.isInteger(d.hardware_gain_max_raw) ? ` (${d.hardware_gain_min_raw}-${d.hardware_gain_max_raw})` : ""}</span>
         ${d.hardware_gain_switch_on === true ? '<span class="meta-inline">aktiv</span>' : d.hardware_gain_switch_on === false ? '<span class="meta-inline">inaktiv</span>' : ""}`
      : "<span>Hardware Gain: nicht verfugbar</span>";

    const micBoostPercent = Number.isInteger(d.mic_boost_percent) ? d.mic_boost_percent : 0;
    const micBoost = d.mic_boost_available
      ? `<label>Mic Boost:</label>
         <input class="range" type="range" min="0" max="100" step="1" value="${micBoostPercent}" data-action="set-mic-boost" data-id="${d.stable_id}" />
         <span>${micBoostPercent}%</span>
         <span class="meta-inline">${d.mic_boost_db !== null && d.mic_boost_db !== undefined ? `${escapeHtml(String(d.mic_boost_db))} dB` : ""}</span>`
      : "<span>Mic Boost: nicht verfugbar</span>";

    const extraControls = (d.alsa_controls || [])
      .filter((c) => c && c.name && c.name !== d.hardware_gain_name && c.name !== d.mic_boost_control)
      .map((c) => {
        const name = escapeHtml(c.name);
        const kind = escapeHtml(c.kind || "diagnostic_only");
        if (c.kind === "diagnostic_only") {
          return `<div class="meta">ALSA (diagnose): ${name} (${kind})</div>`;
        }
        if (c.has_volume) {
          const p = Number.isInteger(c.percent) ? c.percent : 0;
          return `<div class="actions">
              <label>${name}:</label>
              <input class="range" type="range" min="0" max="100" step="1" value="${p}" data-action="set-alsa-control" data-id="${d.stable_id}" data-control="${name}" />
              <span>${p}%</span>
              <span class="meta-inline">${c.db !== null && c.db !== undefined ? `${escapeHtml(String(c.db))} dB` : ""}</span>
            </div>`;
        }
        if (c.has_switch) {
          return `<div class="actions">
              <label>${name}:</label>
              <button data-action="set-alsa-switch" data-id="${d.stable_id}" data-control="${name}" data-switch="${c.switch_on ? "0" : "1"}">${c.switch_on ? "Deaktivieren" : "Aktivieren"}</button>
              <span>${c.switch_on ? "aktiv" : "inaktiv"}</span>
            </div>`;
        }
        return `<div class="meta">ALSA: ${name} (${kind})</div>`;
      })
      .join("");

    return `
      <div class="actions">${hwGain}</div>
      <div class="actions">${micBoost}<button data-action="test-record" data-id="${d.stable_id}">Testaufnahme</button></div>
      ${extraControls}
      <div class="meta" data-test-result="${d.stable_id}">Letzte Testaufnahme: -</div>
      <audio controls class="hidden" data-audio-player="${d.stable_id}"></audio>
    `;
  }

  function deviceCardHtml(kind, d) {
    const isDefault = d.default ? "<span class='badge default'>Default</span>" : "";
    const stateBadge = `<span class='badge state-${d.state}'>${d.state}</span>`;
    const vol = volumeForDevice(d, kind);
    const muteLabel = d.muted ? "Unmute" : "Mute";
    const setMuteAction = kind === "output" ? "set-output-mute" : "set-input-mute";
    const setVolAction = kind === "output" ? "set-output-volume" : "set-input-volume";
    const volumeLabel = kind === "output" ? "Output-Lautstarke" : "Mikrofon-Lautstarke";
    const currentDb = kind === "output" ? d.volume_db_current : d.source_volume_db_current;

    return `
      <article class="card" data-device="${d.stable_id}">
        <div class="card-head">
          <div class="dev-name">${escapeHtml(d.display_name)}</div>
          <div class="badges">${isDefault}<span class="badge">${escapeHtml(d.bus_type)}</span>${stateBadge}</div>
        </div>
        <div class="meta">${escapeHtml(d.description || d.technical_name)}</div>
        <div class="meta">Anschluss: ${escapeHtml(d.connection_label || "-")} | Port: ${escapeHtml(d.active_port || "-")} | Profil: ${escapeHtml(d.profile || "-")}</div>
        <div class="meta">Basislautstarke: ${Number.isInteger(d.base_volume_percent) ? d.base_volume_percent : "-"}% ${d.base_volume_db !== null && d.base_volume_db !== undefined ? `/ ${escapeHtml(String(d.base_volume_db))} dB` : ""}</div>
        <div class="actions">
          <button data-action="set-default" data-id="${d.stable_id}">Als Default</button>
          <button data-action="${setMuteAction}" data-id="${d.stable_id}" data-mute="${d.muted ? "0" : "1"}">${muteLabel}</button>
          <label>${volumeLabel}:</label>
          <input class="range" type="range" min="0" max="150" step="1" value="${vol}" data-action="${setVolAction}" data-id="${d.stable_id}" />
          <span>${vol}%</span>
        </div>
        <div class="meta">Aktuell: ${vol}% ${currentDb !== null && currentDb !== undefined ? `/ ${escapeHtml(String(currentDb))} dB` : ""}</div>
        ${kind === "input" ? renderInputExtras(d) : ""}
        <div class="meter"><div class="meter-bar" data-meter="${d.stable_id}"></div></div>
        <div class="meter-text" data-meter-text="${d.stable_id}">Pegel wird geladen...</div>
        <details class="details">
          <summary>Technische Details</summary>
          <div class="meta">technical_name: ${escapeHtml(d.technical_name)}</div>
          <div class="meta">card: ${escapeHtml(d.card_name || "-")} card_index=${d.card_index ?? "-"} device_index=${d.device_index ?? "-"}</div>
          <div class="meta">ports: ${escapeHtml((d.ports || []).join(", ") || "-")}</div>
          <div class="meta">kanale: ${escapeHtml(JSON.stringify(d.channel_volumes || []))}</div>
          ${kind === "input" ? `<div class="meta">alsa_controls: ${escapeHtml(((d.alsa_controls || []).map((x) => x.name)).join(", ") || "-")}</div>` : ""}
        </details>
      </article>
    `;
  }

  function renderDevices(kind, devices) {
    const target = qs(kind === "output" ? "#outputs" : "#inputs");
    if (!target) return;
    if (!devices.length) {
      target.innerHTML = `<article class='card'><div class='meta'>Keine Gerate gefunden.</div></article>`;
      return;
    }
    target.innerHTML = devices.map((d) => deviceCardHtml(kind, d)).join("");
  }

  function renderStreams(streams) {
    const tbody = qs("#streams-body");
    if (!tbody) return;
    if (!streams.length) {
      tbody.innerHTML = "<tr><td colspan='6'>Keine aktiven Streams erkannt.</td></tr>";
      return;
    }
    tbody.innerHTML = streams
      .map((s) => {
        const vol = Number.isInteger(s.volume_percent) ? s.volume_percent : 0;
        return `
        <tr>
          <td>${escapeHtml(s.app_name)}</td>
          <td>${escapeHtml(s.process_name || "-")}</td>
          <td>${escapeHtml(s.target_device_name || "-")}</td>
          <td>
            <input type="range" min="0" max="150" step="1" value="${vol}" data-action="stream-volume" data-stream="${s.stream_id}" />
            <span>${vol}%</span>
          </td>
          <td>${s.muted ? "muted" : "active"}</td>
          <td>${escapeHtml(s.stream_id)}</td>
        </tr>`;
      })
      .join("");
  }

  function escapeHtml(v) {
    return String(v ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  async function postJson(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      throw new Error(data.error || `HTTP ${res.status}`);
    }
    return data;
  }

  async function handleAction(ev) {
    const target = ev.target;
    const action = target.getAttribute("data-action");
    if (!action) return;

    try {
      if (action === "set-default") {
        await postJson(`/api/audio/device/${target.getAttribute("data-id")}/set-default`, {});
      } else if (action === "set-output-mute") {
        await postJson(`/api/audio/device/${target.getAttribute("data-id")}/set-output-mute`, {
          mute: target.getAttribute("data-mute") === "1",
        });
      } else if (action === "set-input-mute") {
        await postJson(`/api/audio/device/${target.getAttribute("data-id")}/set-input-mute`, {
          mute: target.getAttribute("data-mute") === "1",
        });
      } else if (action === "set-output-volume") {
        await postJson(`/api/audio/device/${target.getAttribute("data-id")}/set-output-volume`, {
          volume_percent: Number(target.value),
        });
      } else if (action === "set-input-volume") {
        await postJson(`/api/audio/device/${target.getAttribute("data-id")}/set-input-volume`, {
          volume_percent: Number(target.value),
        });
      } else if (action === "set-capture-gain") {
        await postJson(`/api/audio/device/${target.getAttribute("data-id")}/set-capture-gain`, {
          value_percent: Number(target.value),
        });
      } else if (action === "set-hardware-gain") {
        await postJson(`/api/audio/device/${target.getAttribute("data-id")}/set-hardware-gain`, {
          value_percent: Number(target.value),
        });
      } else if (action === "set-mic-boost") {
        await postJson(`/api/audio/device/${target.getAttribute("data-id")}/set-mic-boost`, {
          value_percent: Number(target.value),
        });
      } else if (action === "set-alsa-control") {
        await postJson(`/api/audio/device/${target.getAttribute("data-id")}/set-alsa-control`, {
          control_name: target.getAttribute("data-control"),
          value_percent: Number(target.value),
        });
      } else if (action === "set-alsa-switch") {
        await postJson(`/api/audio/device/${target.getAttribute("data-id")}/set-alsa-switch`, {
          control_name: target.getAttribute("data-control"),
          switch_on: target.getAttribute("data-switch") === "1",
        });
      } else if (action === "stream-volume") {
        await postJson(`/api/audio/stream/${target.getAttribute("data-stream")}/set-volume`, {
          volume_percent: Number(target.value),
        });
      } else if (action === "test-record") {
        const stableId = target.getAttribute("data-id");
        const data = await postJson(`/api/audio/device/${stableId}/test-record`, { duration_sec: 3.0 });
        const resultEl = qs(`[data-test-result="${stableId}"]`);
        if (resultEl) {
          resultEl.textContent = `Letzte Testaufnahme: RMS ${data.rms_percent}% | Peak ${data.peak_percent}% | Bewertung: ${data.loudness_label}`;
        }
        const player = qs(`[data-audio-player="${stableId}"]`);
        if (player && data.playback_url) {
          player.src = data.playback_url;
          player.classList.remove("hidden");
        }
      } else if (action === "calibrate-input") {
        const stableId = target.getAttribute("data-id");
        const box = qs(`[data-calibration="${stableId}"]`);
        if (box) {
          box.innerHTML = `<div class="meta">Kalibrierung läuft... Bitte normal sprechen (3-5 Sekunden).</div>`;
        }
        const data = await postJson(`/api/audio/device/${stableId}/calibrate-input`, { duration_sec: 4.0 });
        renderCalibration(stableId, data);
      } else if (action === "apply-calibration") {
        const stableId = target.getAttribute("data-id");
        await postJson(`/api/audio/device/${stableId}/apply-calibration-recommendation`, {});
        const cal = await getJson(`/api/audio/device/${stableId}/calibration`);
        renderCalibration(stableId, cal);
      }
      await refreshSummaryAndTables();
      await refreshMeters();
      qs("#status").textContent = `Aktualisiert: ${new Date().toLocaleTimeString()}`;
    } catch (err) {
      qs("#status").textContent = `Fehler: ${err.message}`;
    }
  }

  function attachEvents() {
    document.body.addEventListener("click", handleAction);
    document.body.addEventListener("change", (ev) => {
      const t = ev.target;
      const action = t.getAttribute("data-action");
      if (
        action === "set-output-volume" ||
        action === "set-input-volume" ||
        action === "set-capture-gain" ||
        action === "set-hardware-gain" ||
        action === "set-alsa-control" ||
        action === "set-mic-boost" ||
        action === "stream-volume"
      ) {
        handleAction(ev);
      }
    });
    qs("#refresh-btn")?.addEventListener("click", async () => {
      await refreshSummaryAndTables();
      await refreshMeters();
      qs("#status").textContent = `Aktualisiert: ${new Date().toLocaleTimeString()}`;
    });
    qs("#expert-toggle")?.addEventListener("click", () => {
      const url = new URL(window.location.href);
      url.searchParams.set("expert", state.expertMode ? "0" : "1");
      window.location.href = url.toString();
    });

    qs("#meter-toggle")?.addEventListener("click", async () => {
      const btn = qs("#meter-toggle");
      btn.disabled = true;
      try {
        const endpoint = state.meterRunning ? "/api/audio/meter/stop" : "/api/audio/meter/start";
        const res = await fetch(endpoint, { method: "POST", cache: "no-store" });
        const data = await res.json();
        updateMeterBtn(data.running);
        if (!data.running) {
          document.querySelectorAll("[data-meter-text]").forEach((el) => {
            el.textContent = "Meter nicht aktiv";
          });
          document.querySelectorAll("[data-meter]").forEach((el) => {
            el.style.width = "0%";
          });
        }
      } catch (_err) {
        qs("#status").textContent = "Meter-Fehler";
      } finally {
        btn.disabled = false;
      }
    });
  }

  function setupSse() {
    if (!window.EventSource) return;
    const es = new EventSource("/api/audio/events");
    es.addEventListener("snapshot", async (ev) => {
      const payload = JSON.parse(ev.data || "{}");
      if (!payload.snapshot_hash || payload.snapshot_hash === state.snapshotHash) return;
      await refreshSummaryAndTables();
      await refreshMeters();
      qs("#status").textContent = `Live-Update: ${new Date().toLocaleTimeString()}`;
    });
  }

  async function init() {
    attachEvents();
    await refreshSummaryAndTables();
    await refreshMeters();
    setupSse();
    setInterval(refreshMeters, 450);
    initBluetooth();
  }

  init().catch((err) => {
    qs("#status").textContent = `Initialisierung fehlgeschlagen: ${err.message}`;
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // BLUETOOTH-MODAL
  // ═══════════════════════════════════════════════════════════════════════════

  const btState = {
    adapterAvailable: false,
    adapterPowered:   false,
    scanning:         false,
    scanDuration:     12,
    scanPollTimer:    null,
  };

  // Gerätetyp → Emoji
  function btDeviceEmoji(deviceType, icon) {
    const t = `${deviceType || ""} ${icon || ""}`.toLowerCase();
    if (t.includes("headset"))    return "\uD83C\uDFA7";
    if (t.includes("kopfhörer") || t.includes("headphone")) return "\uD83C\uDFA7";
    if (t.includes("lautsprecher") || t.includes("speaker")) return "\uD83D\uDD0A";
    if (t.includes("mikrofon") || t.includes("microphone")) return "\uD83C\uDF99";
    if (t.includes("audio"))      return "\uD83D\uDD09";
    if (t.includes("telefon") || t.includes("phone")) return "\uD83D\uDCF1";
    if (t.includes("tastatur") || t.includes("keyboard")) return "\u2328\uFE0F";
    if (t.includes("maus")    || t.includes("mouse"))    return "\uD83D\uDDB1";
    if (t.includes("gamepad") || t.includes("gaming"))   return "\uD83C\uDFAE";
    if (t.includes("computer"))   return "\uD83D\uDCBB";
    return "\uD83D\uDCF6";
  }

  // Signal-Klasse aus RSSI
  function btSignalClass(rssi) {
    if (rssi == null) return "";
    if (rssi >= -60) return "strong";
    if (rssi >= -70) return "good";
    if (rssi >= -80) return "fair";
    return "weak";
  }

  function btSignalLabel(rssi) {
    if (rssi == null) return "";
    const cls  = btSignalClass(rssi);
    const bars = cls === "strong" ? "\u2582\u2584\u2586\u2588" : cls === "good" ? "\u2582\u2584\u2586\u00B7" : cls === "fair" ? "\u2582\u2584\u00B7\u00B7" : "\u2582\u00B7\u00B7\u00B7";
    return `${bars} ${rssi} dBm`;
  }

  function btSetStatus(msg, kind) {
    const el = qs("#bt-status-line");
    if (!el) return;
    el.textContent = msg;
    el.className   = `bt-status-line${kind ? " " + kind : ""}`;
  }

  function btRenderAdapter(adapter) {
    if (!adapter) return;
    btState.adapterAvailable = adapter.available !== false;
    btState.adapterPowered   = !!adapter.powered;

    const dot     = qs("#bt-dot-power");
    const nameEl  = qs("#bt-adapter-name");
    const macEl   = qs("#bt-adapter-mac");
    const flags   = qs("#bt-adapter-flags");
    const pwBtn   = qs("#bt-power-btn");
    const discBtn = qs("#bt-discoverable-btn");

    if (!adapter.available) {
      if (dot)    dot.className  = "bt-status-dot off";
      if (nameEl) nameEl.textContent = adapter.error || "Kein Bluetooth-Adapter gefunden";
      if (flags)  flags.style.display = "none";
      if (pwBtn)  { pwBtn.disabled = true; pwBtn.textContent = "Nicht verfügbar"; }
      if (discBtn) discBtn.disabled = true;
      return;
    }

    if (dot)   dot.className  = adapter.powered ? "bt-status-dot on" : "bt-status-dot off";
    if (nameEl) nameEl.textContent = adapter.powered
      ? `Adapter aktiv${adapter.name ? ": " + adapter.name : ""}`
      : "Adapter inaktiv";
    if (macEl) { macEl.textContent = adapter.mac || ""; macEl.style.display = adapter.mac ? "" : "none"; }
    if (flags) {
      flags.style.display = adapter.powered ? "" : "none";
      const dEl = qs("#bt-flag-discoverable");
      const pEl = qs("#bt-flag-pairable");
      if (dEl) { dEl.className = `badge${adapter.discoverable ? " default" : ""}`; dEl.textContent = adapter.discoverable ? "Sichtbar \u2713" : "Unsichtbar"; }
      if (pEl) { pEl.className = `badge${adapter.pairable ? " state-running" : ""}`; pEl.textContent = adapter.pairable ? "Pairing aktiv \u2713" : "Pairing inaktiv"; }
    }
    if (pwBtn)  { pwBtn.disabled = false; pwBtn.textContent = adapter.powered ? "Ausschalten" : "Einschalten"; }
    if (discBtn) { discBtn.disabled = !adapter.powered; discBtn.textContent = adapter.discoverable ? "Unsichtbar schalten" : "Sichtbar machen"; }
  }

  function btUpdateScanProgress(scan) {
    if (!scan) return;
    const bar     = qs("#bt-scan-bar");
    const info    = qs("#bt-scan-info");
    const scanBtn = qs("#bt-scan-btn");
    const stopBtn = qs("#bt-scan-stop-btn");
    const timerEl = qs("#bt-scan-timer");
    const dot     = qs("#bt-dot-power");

    if (scan.scanning) {
      if (bar)     bar.classList.add("active");
      if (dot)     dot.className = "bt-status-dot scanning";
      if (scanBtn)  scanBtn.classList.add("hidden");
      if (stopBtn)  stopBtn.classList.remove("hidden");
      if (timerEl) { timerEl.classList.remove("hidden"); timerEl.textContent = `${scan.elapsed_sec}s / ${scan.duration_sec}s`; }
      if (info)    info.textContent = `Scanne\u2026 ${scan.devices_found} Ger\u00E4t${scan.devices_found !== 1 ? "e" : ""} gefunden`;
    } else {
      if (bar)     bar.classList.remove("active");
      if (dot && btState.adapterPowered) dot.className = "bt-status-dot on";
      if (scanBtn)  scanBtn.classList.remove("hidden");
      if (stopBtn)  stopBtn.classList.add("hidden");
      if (timerEl)  timerEl.classList.add("hidden");
      if (info)    info.textContent = scan.devices_found > 0
        ? `Scan abgeschlossen \u2013 ${scan.devices_found} Ger\u00E4t${scan.devices_found !== 1 ? "e" : ""} gefunden`
        : "Bereit f\u00FCr Scan";
    }
  }

  function btDeviceItemHtml(dev, context) {
    const emoji  = btDeviceEmoji(dev.device_type, dev.icon);
    const sigCls = btSignalClass(dev.rssi);
    const sigTxt = btSignalLabel(dev.rssi);
    const badges = [
      dev.device_type && dev.device_type !== "Unbekannt" ? `<span class="badge">${escapeHtml(dev.device_type)}</span>` : "",
      dev.connected  ? `<span class="badge state-running">Verbunden</span>` : "",
      dev.paired     ? `<span class="badge default">Gepairt</span>` : "",
      dev.trusted    ? `<span class="badge">Vertraut</span>` : "",
    ].filter(Boolean).join("");

    let actions = "";
    const safeMac = escapeHtml(dev.mac);
    if (context === "found") {
      if (!dev.paired) {
        actions += `<button data-btaction="pair" data-mac="${safeMac}" class="primary">Pairen</button>`;
      } else if (dev.connected) {
        actions += `<button data-btaction="disconnect" data-mac="${safeMac}">Trennen</button>`;
      } else {
        actions += `<button data-btaction="connect" data-mac="${safeMac}" class="primary">Verbinden</button>`;
      }
    } else {
      if (dev.connected) {
        actions += `<button data-btaction="disconnect" data-mac="${safeMac}">Trennen</button>`;
      } else {
        actions += `<button data-btaction="connect" data-mac="${safeMac}" class="primary">Verbinden</button>`;
      }
      if (!dev.trusted) {
        actions += `<button data-btaction="trust" data-mac="${safeMac}">Vertrauen</button>`;
      } else {
        actions += `<button data-btaction="untrust" data-mac="${safeMac}" class="muted-btn">Vertrauen entziehen</button>`;
      }
      actions += `<button data-btaction="remove" data-mac="${safeMac}">Entfernen</button>`;
    }

    return `
      <div class="bt-device-item${dev.connected ? " connected" : ""}">
        <div class="bt-device-icon">${emoji}</div>
        <div class="bt-device-info">
          <div class="bt-device-name">${escapeHtml(dev.name || dev.mac)}</div>
          <div class="bt-device-mac">${safeMac}</div>
          <div class="bt-device-badges">${badges}</div>
        </div>
        ${sigTxt ? `<div class="bt-signal ${sigCls}">${sigTxt}</div>` : ""}
        <div class="bt-device-actions">${actions}</div>
      </div>`;
  }

  function btRenderFoundDevices(devices) {
    const list = qs("#bt-found-list");
    if (!list) return;
    if (!devices || !devices.length) { list.innerHTML = `<div class="bt-empty">Keine Ger\u00E4te in Reichweite gefunden.</div>`; return; }
    list.innerHTML = devices.map((d) => btDeviceItemHtml(d, "found")).join("");
  }

  function btRenderKnownDevices(devices) {
    const list = qs("#bt-known-list");
    if (!list) return;
    if (!devices || !devices.length) { list.innerHTML = `<div class="bt-empty">Keine bekannten Ger\u00E4te.</div>`; return; }
    list.innerHTML = devices.map((d) => btDeviceItemHtml(d, "known")).join("");
  }

  async function btLoadStatus() {
    try {
      const data = await getJson("/api/bluetooth/status");
      btRenderAdapter(data.adapter);
      btUpdateScanProgress(data.scan);
      if (data.scan && data.scan.devices_found > 0) btRenderFoundDevices(data.scan.devices);
      btState.scanning = !!(data.scan && data.scan.scanning);
      if (btState.scanning) btStartScanPolling();
    } catch (err) {
      btSetStatus(`Adapter-Status Fehler: ${err.message}`, "error");
    }
  }

  async function btLoadKnown() {
    try {
      const data = await getJson("/api/bluetooth/devices");
      btRenderKnownDevices(data.devices || []);
    } catch (err) {
      const list = qs("#bt-known-list");
      if (list) list.innerHTML = `<div class="bt-empty">Fehler: ${escapeHtml(err.message)}</div>`;
    }
  }

  async function btPollScan() {
    try {
      const data = await getJson("/api/bluetooth/scan/results");
      btUpdateScanProgress(data);
      btRenderFoundDevices(data.devices || []);
      btState.scanning = data.scanning;
      if (!data.scanning) {
        btStopScanPolling();
        await btLoadKnown();
        await refreshSummaryAndTables();
      }
    } catch (err) {
      btSetStatus(`Scan-Fehler: ${err.message}`, "error");
      btStopScanPolling();
    }
  }

  function btStartScanPolling() {
    if (btState.scanPollTimer) return;
    btState.scanPollTimer = setInterval(btPollScan, 2000);
  }

  function btStopScanPolling() {
    if (btState.scanPollTimer) { clearInterval(btState.scanPollTimer); btState.scanPollTimer = null; }
  }

  async function btHandleAction(action, mac) {
    btSetStatus(`${action}: ${mac}\u2026`);
    const urlMac = encodeURIComponent(mac);
    const cfg = {
      pair:       { url: `/api/bluetooth/device/${urlMac}/pair`,       method: "POST" },
      connect:    { url: `/api/bluetooth/device/${urlMac}/connect`,    method: "POST" },
      disconnect: { url: `/api/bluetooth/device/${urlMac}/disconnect`, method: "POST" },
      trust:      { url: `/api/bluetooth/device/${urlMac}/trust`,      method: "POST", body: { trust: true } },
      untrust:    { url: `/api/bluetooth/device/${urlMac}/trust`,      method: "POST", body: { trust: false } },
      remove:     { url: `/api/bluetooth/device/${urlMac}`,            method: "DELETE" },
    }[action];
    if (!cfg) return;

    const btn = document.querySelector(`[data-btaction="${action}"][data-mac="${mac}"]`);
    if (btn) { btn.disabled = true; btn.textContent = "\u2026"; }

    try {
      const reqBody = cfg.method !== "DELETE" ? JSON.stringify(cfg.body ?? {}) : undefined;
      const res  = await fetch(cfg.url, { method: cfg.method, headers: { Accept: "application/json", "Content-Type": "application/json" }, body: reqBody });
      const data = await res.json().catch(() => ({}));
      if (!data.ok && res.status !== 200) {
        btSetStatus(`Fehler: ${data.error || data.message || "Unbekannt"}`, "error");
        if (btn) { btn.disabled = false; btn.textContent = action; }
      } else {
        const labels = { pair: "Gepairt", connect: "Verbunden", disconnect: "Getrennt", trust: "Als vertrauenswürdig markiert", untrust: "Vertrauen entzogen", remove: "Entfernt" };
        btSetStatus(`${labels[action] || "OK"}: ${mac}`, "ok");
        await Promise.all([btLoadKnown(), btLoadInline()]);
        const scanData = await getJson("/api/bluetooth/scan/results");
        btRenderFoundDevices(scanData.devices || []);
        if (action === "pair" || action === "connect" || action === "disconnect") {
          setTimeout(async () => { await refreshSummaryAndTables(); }, 2000);
        }
      }
    } catch (err) {
      btSetStatus(`Netzwerkfehler: ${err.message}`, "error");
      if (btn) { btn.disabled = false; btn.textContent = action; }
    }
  }

  async function btOpenModal() {
    const modal = qs("#bt-modal");
    if (!modal) return;
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    await btLoadStatus();
    await btLoadKnown();
  }

  function btCloseModal() {
    const modal = qs("#bt-modal");
    if (!modal) return;
    modal.classList.add("hidden");
    document.body.style.overflow = "";
    btStopScanPolling();
  }

  async function btLoadInline() {
    const list = qs("#bt-inline-list");
    if (!list) return;
    try {
      const data = await getJson("/api/bluetooth/devices");
      const devices = data.devices || [];
      if (devices.length === 0) {
        list.innerHTML = `<span class="meta">Keine bekannten Bluetooth-Geräte.</span>`;
        return;
      }
      list.innerHTML = devices.map((d) => btDeviceItemHtml(d, "known")).join("");
    } catch (err) {
      list.innerHTML = `<span class="meta" style="color:var(--warn)">Geräte konnten nicht geladen werden.</span>`;
    }
  }

  function initBluetooth() {
    btLoadInline();

    qs("#bt-open-btn")?.addEventListener("click", btOpenModal);
    qs("#bt-modal-close")?.addEventListener("click", btCloseModal);
    qs("#bt-modal")?.addEventListener("click", (ev) => { if (ev.target === qs("#bt-modal")) btCloseModal(); });
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !qs("#bt-modal")?.classList.contains("hidden")) btCloseModal(); });

    qs("#bt-inline-list")?.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-btaction]");
      if (!btn) return;
      ev.stopPropagation();
      btHandleAction(btn.getAttribute("data-btaction"), btn.getAttribute("data-mac"));
    });

    qs("#bt-power-btn")?.addEventListener("click", async () => {
      const on = qs("#bt-power-btn")?.textContent?.includes("Einschalten");
      try {
        const res  = await fetch("/api/bluetooth/adapter/power", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on }) });
        const data = await res.json().catch(() => ({}));
        btSetStatus(data.ok ? (on ? "Adapter eingeschaltet" : "Adapter ausgeschaltet") : `Fehler: ${data.message}`, data.ok ? "ok" : "error");
        await btLoadStatus();
      } catch (err) { btSetStatus(`Fehler: ${err.message}`, "error"); }
    });

    qs("#bt-discoverable-btn")?.addEventListener("click", async () => {
      const on = qs("#bt-discoverable-btn")?.textContent?.includes("Sichtbar machen");
      try {
        const res  = await fetch("/api/bluetooth/adapter/discoverable", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on }) });
        const data = await res.json().catch(() => ({}));
        btSetStatus(data.ok ? (on ? "Adapter sichtbar" : "Adapter unsichtbar") : `Fehler: ${data.message}`, data.ok ? "ok" : "error");
        await btLoadStatus();
      } catch (err) { btSetStatus(`Fehler: ${err.message}`, "error"); }
    });

    qs("#bt-scan-btn")?.addEventListener("click", async () => {
      try {
        const res  = await fetch("/api/bluetooth/scan/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ duration_sec: btState.scanDuration }) });
        const data = await res.json().catch(() => ({}));
        if (data.ok) {
          btState.scanning = true;
          btSetStatus("Scan l\u00E4uft\u2026");
          await btPollScan();
          btStartScanPolling();
        } else {
          btSetStatus(`Scan-Fehler: ${data.error || data.message}`, "error");
        }
      } catch (err) { btSetStatus(`Fehler: ${err.message}`, "error"); }
    });

    qs("#bt-scan-stop-btn")?.addEventListener("click", async () => {
      try {
        await fetch("/api/bluetooth/scan/stop", { method: "POST" });
        btStopScanPolling();
        btState.scanning = false;
        btUpdateScanProgress({ scanning: false, devices_found: 0, elapsed_sec: 0, duration_sec: btState.scanDuration });
        btSetStatus("Scan gestoppt");
        await btLoadKnown();
      } catch (err) { btSetStatus(`Fehler: ${err.message}`, "error"); }
    });

    qs("#bt-modal")?.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-btaction]");
      if (!btn) return;
      ev.stopPropagation();
      btHandleAction(btn.getAttribute("data-btaction"), btn.getAttribute("data-mac"));
    });
  }
})();
