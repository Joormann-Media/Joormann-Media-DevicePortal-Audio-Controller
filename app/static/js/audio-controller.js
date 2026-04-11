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

  function qsa(sel) {
    return Array.from(document.querySelectorAll(sel));
  }

  function meterBar(id, value) {
    const bar = qs(`[data-meter="${id}"]`);
    const text = qs(`[data-meter-text="${id}"]`);
    if (!bar || !text) return;
    if (!value || !value.available) {
      bar.style.width = "0%";
      text.textContent = "Pegel nicht verfugbar";
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

  function deviceCardHtml(kind, d) {
    const isDefault = d.default ? "<span class='badge default'>Default</span>" : "";
    const stateBadge = `<span class='badge state-${d.state}'>${d.state}</span>`;
    const muteLabel = d.muted ? "Unmute" : "Mute";
    const vol = Number.isInteger(d.volume_percent) ? d.volume_percent : 0;
    return `
      <article class="card" data-device="${d.stable_id}">
        <div class="card-head">
          <div class="dev-name">${escapeHtml(d.display_name)}</div>
          <div class="badges">
            ${isDefault}
            <span class="badge">${escapeHtml(d.bus_type)}</span>
            ${stateBadge}
          </div>
        </div>
        <div class="meta">${escapeHtml(d.description || d.technical_name)}</div>
        <div class="meta">Anschluss: ${escapeHtml(d.connection_label || "-")} | Profil: ${escapeHtml(d.profile || "-")}</div>
        <div class="actions">
          <button data-action="set-default" data-id="${d.stable_id}">Als Default</button>
          <button data-action="toggle-mute" data-id="${d.stable_id}" data-mute="${d.muted ? "0" : "1"}">${muteLabel}</button>
          <input class="range" type="range" min="0" max="150" step="1" value="${vol}" data-action="set-volume" data-id="${d.stable_id}" />
          <span>${vol}%</span>
        </div>
        <div class="meter"><div class="meter-bar" data-meter="${d.stable_id}"></div></div>
        <div class="meter-text" data-meter-text="${d.stable_id}">Pegel wird geladen...</div>
        <details class="details">
          <summary>Technische Details</summary>
          <div class="meta">technical_name: ${escapeHtml(d.technical_name)}</div>
          <div class="meta">card: ${escapeHtml(d.card_name || "-")} card_index=${d.card_index ?? "-"} device_index=${d.device_index ?? "-"}</div>
          <div class="meta">ports: ${escapeHtml((d.ports || []).join(", ") || "-")}</div>
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
    tbody.innerHTML = streams.map((s) => {
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
    }).join("");
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
  }

  async function handleAction(ev) {
    const target = ev.target;
    const action = target.getAttribute("data-action");
    if (!action) return;

    try {
      if (action === "set-default") {
        await postJson(`/api/audio/device/${target.getAttribute("data-id")}/set-default`, {});
      } else if (action === "toggle-mute") {
        await postJson(`/api/audio/device/${target.getAttribute("data-id")}/set-mute`, {
          mute: target.getAttribute("data-mute") === "1",
        });
      } else if (action === "set-volume") {
        await postJson(`/api/audio/device/${target.getAttribute("data-id")}/set-volume`, {
          volume_percent: Number(target.value),
        });
      } else if (action === "stream-volume") {
        await postJson(`/api/audio/stream/${target.getAttribute("data-stream")}/set-volume`, {
          volume_percent: Number(target.value),
        });
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
      if (action === "set-volume" || action === "stream-volume") {
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
    setInterval(refreshMeters, 1200);
  }

  init().catch((err) => {
    qs("#status").textContent = `Initialisierung fehlgeschlagen: ${err.message}`;
  });
})();
