(() => {
  const state = {
    snapshotHash: window.AUDIO_BOOTSTRAP.snapshot_hash,
    expertMode: window.AUDIO_BOOTSTRAP.expert_mode,
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
      text.textContent = "Pegel nicht verfügbar";
      return;
    }
    bar.style.width = `${value.peak_percent}%`;
    text.textContent = `RMS ${value.rms_percent}% | Peak ${value.peak_percent}%`;
  }

  async function refreshMeters() {
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

    if (state.expertMode) {
      const diag = await getJson("/api/audio/diagnostics");
      const pre = qs("#diag-json");
      if (pre) {
        pre.textContent = JSON.stringify(diag, null, 2);
      }
    }
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
  }

  init().catch((err) => {
    qs("#status").textContent = `Initialisierung fehlgeschlagen: ${err.message}`;
  });
})();
