/**
 * control.js — the laptop half of BlinkyMap.
 *
 * Owns controller setup, the session list, the 3D model and export. It has no
 * camera and no detection code: the server rejects detections from a client
 * that declared role "control", so a stray webcam here cannot race the sensor.
 *
 * State machine:  idle → configured → scanning → done
 *
 * Connects to the Python WebSocket server running on the same Pi.
 * Drives the camera module for detection and the Three.js viewer for 3D.
 */

import { Viewer3D } from "./viewer3d.js";

// ── WebSocket URL — proxied through Apache at same origin to satisfy CSP ─────
const WS_URL = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/blinkymap-ws`;

// ── DOM references ────────────────────────────────────────────────────────────
const wsIndicator    = document.getElementById("ws-indicator");
const tabBtns        = document.querySelectorAll(".tab-btn");
const tabPanels      = document.querySelectorAll(".tab-panel");

const cfgHost        = document.getElementById("cfg-host");
const cfgOutputMode  = document.getElementById("cfg-output-mode");
const cfgStart       = document.getElementById("cfg-start");
const cfgPixels      = document.getElementById("cfg-pixels");
const cfgDelay       = document.getElementById("cfg-delay");
const cfgFov         = document.getElementById("cfg-fov");
const cfgMinConf     = document.getElementById("cfg-min-conf");
const cfgMinConfVal  = document.getElementById("cfg-min-conf-val");
const btnSaveConfig      = document.getElementById("btn-save-config");
const controllerStatus   = document.getElementById("controller-status");
const btnTestBlink       = document.getElementById("btn-test-blink");
const btnStopTest    = document.getElementById("btn-stop-test");
const testResultMsg  = document.getElementById("test-result-msg");

const scanBlock      = document.getElementById("scan-progress-block");
const progressBar    = document.getElementById("scan-progress-bar");
const progressLabel  = document.getElementById("scan-progress-label");
const btnStopScan    = document.getElementById("btn-stop-scan");
const suggCard       = document.getElementById("suggestion-card");
const suggAngle      = document.getElementById("sugg-angle");
const suggDist       = document.getElementById("sugg-dist");
const suggReason     = document.getElementById("sugg-reason");
const sessionList    = document.getElementById("session-list");
const confidencePct  = document.getElementById("confidence-pct");
const confidenceGrade= document.getElementById("confidence-grade");
const confidenceDet  = document.getElementById("confidence-detail");

const viewerContainer= document.getElementById("viewer-container");
const pixelListEl    = document.getElementById("pixel-list");

const exportConfLabel= document.getElementById("export-confidence-label");
const btnExportXmodel= document.getElementById("btn-export-xmodel");
const btnExportCsv   = document.getElementById("btn-export-csv");
const unitToggle     = document.getElementById("unit-toggle");
const confidenceTip  = document.getElementById("confidence-tip");

// ── App state ─────────────────────────────────────────────────────────────────
let ws           = null;
let viewer       = null;
let scanning     = false;
let currentPixelIdx = -1;
let sessions        = [];
let latestPixels    = [];
let lastSuggestion  = null;
let units           = "ft";  // "m" or "ft"

// ── Unit helpers ──────────────────────────────────────────────────────────────
// Form fields hold values in the CURRENTLY SELECTED display units, never
// metres. The server always speaks metres. Convert at every boundary.
function fromMeters(meters) {
  return units === "ft" ? meters * 3.28084 : meters;
}
function formatDist(meters) {
  return `${fromMeters(meters).toFixed(1)} ${units}`;
}
function toMeters(val) {
  return units === "ft" ? val / 3.28084 : val;
}

// ── Tab switching ─────────────────────────────────────────────────────────────
tabBtns.forEach(btn => {
  btn.addEventListener("click", () => {
    tabBtns.forEach(b => b.classList.remove("active"));
    tabPanels.forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");

    if (btn.dataset.tab === "model" && !viewer) {
      viewer = new Viewer3D(viewerContainer);
      if (latestPixels.length) viewer.update(latestPixels);
      if (lastSuggestion) viewer.setSuggestion(lastSuggestion.angle, lastSuggestion.distance);
    }
  });
});

// ── WebSocket ─────────────────────────────────────────────────────────────────
function connect() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    setIndicator("green");
    send({ type: "hello", role: "control" });
    statusMsg("Connected to BlinkyMap server");
  };

  ws.onclose = () => {
    setIndicator("red");
    setTimeout(connect, 3000);   // auto-reconnect
  };

  ws.onerror = () => setIndicator("yellow");

  ws.onmessage = async (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    await handleServerMessage(msg);
  };
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
  }
}

function setIndicator(color) {
  wsIndicator.className = `dot dot-${color}`;
}

function statusMsg(text) {
  console.log("[BlinkyMap]", text);
}

// ── Server message handler ────────────────────────────────────────────────────
async function handleServerMessage(msg) {
  switch (msg.type) {

    case "status":
      statusMsg(msg.message);
      break;

    case "progress":
      updateProgress(msg.index + 1, msg.total);
      break;

    case "scan_complete":
      scanning = false;
      scanBlock.style.display = "none";
      addSessionCard(msg.session, msg.detected, msg.total, msg.detections || {},
                     msg.angle ?? 0, msg.distance ?? 2, msg.height ?? 1.5,
                     msg.pitch ?? null, msg.pitch_spread ?? 0);
      statusMsg(`Session ${msg.session}: ${msg.detected}/${msg.total} detected`);
      break;

    case "session_list":
      // Replayed on (re)connect. Rebuild from scratch so a reload shows every
      // session the server actually holds — otherwise they stay invisible and
      // cannot be deleted, while still feeding triangulation.
      sessions.length = 0;
      sessionList.innerHTML = "";
      for (const s of msg.sessions || []) {
        addSessionCard(s.session, s.detected, s.total, s.detections || {},
                       s.angle ?? 0, s.distance ?? 2, s.height ?? 1.5,
                       s.pitch ?? null, s.pitch_spread ?? 0);
      }
      break;

    case "model":
      latestPixels = msg.pixels;
      if (viewer) viewer.update(latestPixels);
      updatePixelList(latestPixels);
      break;

    case "sensor_status":
      renderSensorStatus(msg);
      break;

    case "confidence":
      updateConfidence(msg);
      break;

    case "next_suggestion":
      lastSuggestion = msg;
      showSuggestion(msg);
      if (viewer) viewer.setSuggestion(msg.angle, msg.distance);
      break;

    case "session_deleted":
      sessions = sessions.filter(s => s.id !== msg.session_id);
      sessionList.querySelector(`[data-session-id="${msg.session_id}"]`)?.remove();
      break;

    case "controller_status":
      controllerStatus.textContent = msg.message;
      controllerStatus.className   = `controller-status ${msg.ok ? "ctrl-ok" : "ctrl-fail"}`;
      controllerStatus.style.display = "block";
      break;

    case "test_sweep_progress":
      showTestResult(true,
        `Pixel ${msg.index + 1} / ${msg.total} · ${msg.mode} · ch ${msg.start_ch + msg.index * 3}`);
      break;

    case "test_sweep_done":
      btnTestBlink.style.display = "block";
      btnStopTest.style.display  = "none";
      showTestResult(msg.ok, msg.message);
      break;

    case "export_ready":
      if (msg.xmodel) triggerDownload("BlinkyTree.xmodel", msg.xmodel, "text/xml");
      if (msg.csv)    triggerDownload("BlinkyTree.csv",    msg.csv,    "text/csv");
      break;
  }
}

// ── Config ────────────────────────────────────────────────────────────────────
function sendConfig() {
  send({
    type:        "set_config",
    host:        cfgHost.value.trim(),
    output_mode: cfgOutputMode.value,
    start_ch:    parseInt(cfgStart.value),
    pixel_count: parseInt(cfgPixels.value),
    delay:       parseFloat(cfgDelay.value),
    // Owned here, relayed by the server to the sensor that actually detects.
    min_conf:    parseInt(cfgMinConf.value) / 100,
    hfov_deg:    parseFloat(cfgFov.value),
  });
}

btnSaveConfig.addEventListener("click", () => {
  sendConfig();
  controllerStatus.textContent = `Checking ${cfgHost.value.trim()}…`;
  controllerStatus.className   = "controller-status ctrl-ok";
  controllerStatus.style.display = "block";
});

// ── Test blink ────────────────────────────────────────────────────────────────
btnTestBlink.addEventListener("click", () => {
  // Push the current form values first — otherwise the sweep runs against
  // whatever config the server last received.
  sendConfig();
  send({ type: "test_sweep" });
  btnTestBlink.style.display = "none";
  btnStopTest.style.display  = "block";
  showTestResult(true, "Starting sweep…");
});

btnStopTest.addEventListener("click", () => {
  send({ type: "stop_test" });
  btnStopTest.style.display = "none";
});

let _testResultTimer = null;
function showTestResult(ok, message) {
  testResultMsg.textContent = message;
  testResultMsg.className   = `test-result ${ok ? "test-ok" : "test-fail"}`;
  testResultMsg.style.display = "block";
  clearTimeout(_testResultTimer);
  // Auto-hide after 8 s once we have a final answer (not "Connecting…"/"Sending…")
  if (!message.endsWith("…")) {
    _testResultTimer = setTimeout(() => { testResultMsg.style.display = "none"; }, 8000);
  }
}

// ── Min-confidence slider ─────────────────────────────────────────────────────
cfgMinConf.addEventListener("input", () => {
  cfgMinConfVal.textContent = `${cfgMinConf.value}%`;
});

// ── Unit toggle ───────────────────────────────────────────────────────────────
unitToggle.addEventListener("click", e => {
  const btn = e.target.closest(".unit-btn");
  if (!btn || btn.dataset.unit === units) return;
  const prev = units;
  units = btn.dataset.unit;

  // Update button styles
  unitToggle.querySelectorAll(".unit-btn").forEach(b =>
    b.classList.toggle("active", b.dataset.unit === units));

  // Update label suffixes
  document.querySelectorAll(".unit-sfx").forEach(el => el.textContent = units);

  // Distance/height inputs live on the sensor now; only cards need re-rendering.
  // Update existing session card positions
  document.querySelectorAll(".session-card[data-dist-m]").forEach(card => {
    card.querySelector(".sess-pos").textContent = sessionPosStr(
      parseFloat(card.dataset.angle),
      parseFloat(card.dataset.distM),
      parseFloat(card.dataset.heightM),
    );
  });

  // Update suggestion card if showing
  if (lastSuggestion) showSuggestion(lastSuggestion);
});

// ── Camera ────────────────────────────────────────────────────────────────────
// ── Session / Scan ────────────────────────────────────────────────────────────
btnStopScan.addEventListener("click", () => {
  send({ type: "stop_scan" });
  scanning = false;
  scanBlock.style.display = "none";
});

// ── UI helpers ────────────────────────────────────────────────────────────────
function updateProgress(done, total) {
  const pct = total > 0 ? (done / total) * 100 : 0;
  progressBar.style.width = `${pct}%`;
  progressLabel.textContent = `${done} / ${total}`;
}

function sessionPosStr(angle, distM, heightM, pitch) {
  // heightM is the camera's height above its AIM POINT, not above the floor —
  // the floor never enters the geometry.
  const rise = `${heightM >= 0 ? "+" : "−"}${formatDist(Math.abs(heightM))} above aim`;
  const how = (pitch === null || pitch === undefined)
    ? "typed"
    : `${pitch.toFixed(1)}° tilt`;
  return `${Math.round(angle)}° · ${formatDist(distM)} away · ${rise} · ${how}`;
}

function addSessionCard(sessionId, detected, total, detections, angleDeg, distM, heightM,
                        pitch = null, pitchSpread = 0) {
  sessions.push({ id: sessionId, detected, total, detections, angleDeg, distM, heightM,
                  pitch, pitchSpread });
  const pct = total > 0 ? Math.round((detected / total) * 100) : 0;
  const badge = pct >= 70 ? "badge-good" : pct >= 40 ? "badge-medium" : "badge-poor";

  let rows = "";
  for (let i = 0; i < total; i++) {
    const d = detections[i];
    if (d) {
      rows += `<div class="sess-row sess-row-seen">
        <span>${i + 1}</span>
        <span class="sess-seen-yes">✓</span>
        <span>${Math.round(d.conf * 100)}%</span>
        <span>${Math.round(d.cx)}</span>
        <span>${Math.round(d.cy)}</span>
      </div>`;
    } else {
      rows += `<div class="sess-row sess-row-unseen">
        <span>${i + 1}</span>
        <span class="sess-seen-no">–</span>
        <span>–</span><span>–</span><span>–</span>
      </div>`;
    }
  }

  const card = document.createElement("div");
  card.className = "session-card";
  card.dataset.sessionId = sessionId;
  card.dataset.angle     = angleDeg;
  card.dataset.distM     = distM;
  card.dataset.heightM   = heightM;
  card.innerHTML = `
    <div class="session-card-header">
      <div class="sess-title">
        <div class="sess-name">Session ${sessionId}</div>
        <div class="sess-pos">${sessionPosStr(angleDeg, distM, heightM, pitch)}</div>
      </div>
      <span class="badge ${badge}">${detected}/${total} (${pct}%)</span>
      <span class="sess-chevron">▸</span>
      <button class="session-delete" title="Delete this session">&#x2715;</button>
    </div>
    <div class="session-detail">
      <div class="sess-col-header">
        <span>Ch#</span><span>Seen</span><span>Conf</span><span>ImgX</span><span>ImgY</span>
      </div>
      <div class="sess-pixel-list">${rows}</div>
    </div>
  `;

  card.querySelector(".session-card-header").addEventListener("click", e => {
    if (e.target.closest(".session-delete")) return;
    card.classList.toggle("expanded");
  });
  card.querySelector(".session-delete").addEventListener("click", () => {
    send({ type: "delete_session", session_id: sessionId });
  });
  sessionList.appendChild(card);
}

function showSuggestion(msg) {
  const angle = msg.angle ?? 0;
  suggAngle.textContent  = `${angle}°`;
  // "Same framing" rather than "same height": the tilt is measured per position,
  // so standing higher or lower is now free — framing the prop the same way is
  // what keeps the aim point consistent between positions.
  suggDist.textContent   = `${formatDist(msg.distance ?? 2)} from center · same framing`;
  suggReason.textContent = msg.reason ?? "";
  suggCard.style.display = "block";
}


function updateConfidence(msg) {
  const pct = Math.round((msg.overall ?? 0) * 100);
  confidencePct.textContent  = `${pct}%`;
  confidenceGrade.textContent = msg.grade ?? "–";

  const hue = pct >= 75 ? "#69f0ae" : pct >= 50 ? "#ffee58" : "#ef5350";
  confidencePct.style.color  = hue;
  confidenceGrade.style.color = hue;

  const reproj = msg.consensus_px !== undefined
    ? ` · Reproj ${msg.consensus_px}px (${msg.reproj_px}px pairwise)`
    : "";
  confidenceDet.textContent =
    `Coverage ${Math.round((msg.coverage ?? 0)*100)}% · ` +
    `High ${msg.high ?? 0} · Med ${msg.medium ?? 0} · Low ${msg.low ?? 0} · ` +
    `Unseen ${msg.unseen ?? 0}${reproj}`;

  // Contextual tip
  const nSess = sessions.length;
  let tip;
  if (nSess === 0) {
    tip = "Complete your first scan to start building the model.";
  } else if (nSess === 1) {
    tip = "Scan from a second angle (~180° away) to enable 3D triangulation — positions can't be calculated from one view alone.";
  } else if (pct < 20) {
    tip = `Only ${msg.high + msg.medium} pixels triangulated so far. Try more angles or lower the detection confidence threshold.`;
  } else if ((msg.unseen ?? 0) > (msg.high + msg.medium + msg.low)) {
    tip = `${msg.unseen} pixels still unseen — scan from more angles to find them.`;
  } else {
    // The server tells us which of the three confidence terms is capping the
    // score. Guessing "add more angles" is actively wrong once angular spread
    // is already saturated — extra scans then cannot move the number at all.
    switch (msg.limiting) {
      case "coverage":
        tip = `${msg.unseen} pixels never seen from two positions — scan more angles, ` +
              `or lower the detection confidence threshold.`;
        break;
      case "spread":
        tip = `Your scan positions are too close together (spread ` +
              `${Math.round((msg.spread ?? 0) * 180)}° of a possible 180°). ` +
              `Move further around the model — roughly 90° apart.`;
        break;
      case "accuracy":
        tip = `Coverage and angle spread are already maxed, so more scans will not ` +
              `raise this score. Reprojection error is ${msg.consensus_px}px — the limit ` +
              `is detection precision and how accurately the distance was entered ` +
              `(the camera height is measured, not typed). Spread the pixels out, ` +
              `or re-measure your distance.`;
        break;
      default:
        tip = `${msg.high} high-confidence · ${msg.medium} medium · ${msg.low} low · ` +
              `${msg.unseen} unseen across ${msg.sessions ?? nSess} positions.`;
    }
  }
  confidenceTip.textContent = tip;

  // Update export tab label
  exportConfLabel.textContent = `Model confidence: ${pct}% (${msg.grade})`;
}

function updatePixelList(pixels) {
  // Sort: unseen first, then low, medium, high
  const order = { unseen: 0, low: 1, medium: 2, high: 3 };
  const sorted = [...pixels].sort((a, b) =>
    (order[a.grade] ?? 0) - (order[b.grade] ?? 0)
  );

  pixelListEl.innerHTML = "";
  for (const p of sorted) {
    const row = document.createElement("div");
    row.className = "pixel-row";
    // Server sends x/y/z, never a `position` key — testing p.position made
    // this column render "–" for every pixel.
    const conf  = p.x != null ? `${Math.round((p.confidence ?? 0) * 100)}%` : "–";
    const seen  = p.sessions?.length ?? 0;
    row.innerHTML = `
      <span>${p.index + 1}</span>
      <span class="status-${p.grade}">${p.grade}</span>
      <span>${conf}</span>
      <span>${seen}</span>
    `;
    pixelListEl.appendChild(row);
  }
}

// ── Export ────────────────────────────────────────────────────────────────────
btnExportXmodel.addEventListener("click", () => send({ type: "export", format: "xmodel" }));
btnExportCsv.addEventListener("click",    () => send({ type: "export", format: "csv" }));

function triggerDownload(filename, content, mime) {
  const blob = new Blob([content], { type: mime });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ── Utility ───────────────────────────────────────────────────────────────────
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }


// ── Boot ──────────────────────────────────────────────────────────────────────
connect();

// ── Sensor presence ───────────────────────────────────────────────────────────
function renderSensorStatus(msg) {
  const el = document.getElementById("sensor-status");
  if (!el) return;
  if (!msg.connected) {
    el.className   = "cam-status cam-status-off";
    el.textContent = "No sensor connected — open /sensor.html on your phone";
    return;
  }
  const bits = [];
  if (msg.angle !== null && msg.angle !== undefined) {
    bits.push(`${Math.round(msg.angle)}° around model`);
  } else if (msg.reference === null || msg.reference === undefined) {
    bits.push("0° reference not set on phone");
  }
  if (msg.heading !== null && msg.heading !== undefined) {
    bits.push(`heading ${Math.round(msg.heading)}°`);
  }
  if (msg.accuracy) bits.push(`±${Math.round(msg.accuracy)}°`);
  el.className   = "cam-status cam-status-on";
  el.textContent = "Sensor connected — " + (bits.join(" · ") || "waiting for compass");
}
