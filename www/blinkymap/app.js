/**
 * app.js — BlinkyMap FPP Plugin SPA orchestrator.
 *
 * State machine:  idle → configured → scanning → done
 *
 * Connects to the Python WebSocket server running on the same Pi.
 * Drives the camera module for detection and the Three.js viewer for 3D.
 */

import { openCamera, captureBackground, detectLED } from "./camera.js";
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
const btnSaveConfig      = document.getElementById("btn-save-config");
const controllerStatus   = document.getElementById("controller-status");
const btnTestBlink       = document.getElementById("btn-test-blink");
const btnStopTest    = document.getElementById("btn-stop-test");
const testResultMsg  = document.getElementById("test-result-msg");
const btnOpenCamera  = document.getElementById("btn-open-camera");
const camStatusBar   = document.getElementById("cam-status-bar");
const camPreview     = document.getElementById("cam-preview");
const camCanvas      = document.getElementById("cam-canvas");

const sessAngle      = document.getElementById("sess-angle");
const sessDist       = document.getElementById("sess-dist");
const sessHeight     = document.getElementById("sess-height");
const btnStartSess   = document.getElementById("btn-start-session");
const scanBlock      = document.getElementById("scan-progress-block");
const progressBar    = document.getElementById("scan-progress-bar");
const progressLabel  = document.getElementById("scan-progress-label");
const btnStopScan    = document.getElementById("btn-stop-scan");
const suggCard       = document.getElementById("suggestion-card");
const suggAngle      = document.getElementById("sugg-angle");
const suggDist       = document.getElementById("sugg-dist");
const suggReason     = document.getElementById("sugg-reason");
const btnUseSugg     = document.getElementById("btn-use-suggestion");
const sessionList    = document.getElementById("session-list");
const confidencePct  = document.getElementById("confidence-pct");
const confidenceGrade= document.getElementById("confidence-grade");
const confidenceDet  = document.getElementById("confidence-detail");

const diffCanvas     = document.getElementById("diff-canvas");
const diffLabel      = document.getElementById("diff-label");
const viewerContainer= document.getElementById("viewer-container");
const pixelListEl    = document.getElementById("pixel-list");

const exportConfLabel= document.getElementById("export-confidence-label");
const btnExportXmodel= document.getElementById("btn-export-xmodel");
const btnExportCsv   = document.getElementById("btn-export-csv");

// ── App state ─────────────────────────────────────────────────────────────────
let ws           = null;
let viewer       = null;
let bgImageData  = null;
let camWidth     = 1280;
let camHeight    = 720;
let scanning     = false;
let currentPixelIdx = -1;   // pixel the server is currently firing
let sessions        = [];    // [{id, angle, detected, total}]
let latestPixels    = [];    // last model from server
let lastSuggestion  = null;  // last next_suggestion payload

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

    case "capture_background":
      if (camPreview.srcObject) {
        bgImageData = captureBackground(camPreview, camCanvas);
        camStatusBar.textContent = "Camera: background captured — scanning…";
        camStatusBar.className   = "cam-status cam-status-bg";
        statusMsg("Background captured");
      } else {
        camStatusBar.textContent = "Camera: not open — detections will be skipped";
        camStatusBar.className   = "cam-status cam-status-off";
      }
      break;

    case "pixel_on":
      currentPixelIdx = msg.index;
      if (bgImageData && camPreview.srcObject) {
        await sleep(80);
        const result = detectLED(camPreview, camCanvas, bgImageData, 10);
        // Draw amplified diff so user can see what the camera sees
        drawDiff(camCanvas, bgImageData, msg.index, result);
        if (result.found) {
          send({
            type: "detection",
            index: msg.index,
            cx:   result.cx,
            cy:   result.cy,
            conf: result.conf,
          });
        } else {
          send({ type: "no_detection", index: msg.index });
        }
      } else {
        send({ type: "no_detection", index: msg.index });
      }
      break;

    case "pixel_off":
      currentPixelIdx = -1;
      break;

    case "progress":
      updateProgress(msg.index + 1, msg.total);
      break;

    case "scan_complete":
      scanning = false;
      scanBlock.style.display = "none";
      addSessionCard(msg.session, msg.detected, msg.total);
      statusMsg(`Session ${msg.session}: ${msg.detected}/${msg.total} detected`);
      if (camPreview.srcObject) {
        camStatusBar.textContent = `Camera active — ${msg.detected}/${msg.total} pixels detected last session`;
        camStatusBar.className   = "cam-status " + (msg.detected > 0 ? "cam-status-on" : "cam-status-off");
      }
      break;

    case "model":
      latestPixels = msg.pixels;
      if (viewer) viewer.update(latestPixels);
      updatePixelList(latestPixels);
      break;

    case "confidence":
      updateConfidence(msg);
      break;

    case "next_suggestion":
      lastSuggestion = msg;
      showSuggestion(msg);
      if (viewer) viewer.setSuggestion(msg.angle, msg.distance);
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
btnSaveConfig.addEventListener("click", () => {
  send({
    type:        "set_config",
    host:        cfgHost.value.trim(),
    output_mode: cfgOutputMode.value,
    start_ch:    parseInt(cfgStart.value),
    pixel_count: parseInt(cfgPixels.value),
    delay:       parseFloat(cfgDelay.value),
  });
  controllerStatus.textContent = `Checking ${cfgHost.value.trim()}…`;
  controllerStatus.className   = "controller-status ctrl-ok";
  controllerStatus.style.display = "block";
});

// ── Test blink ────────────────────────────────────────────────────────────────
btnTestBlink.addEventListener("click", () => {
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

// ── Camera ────────────────────────────────────────────────────────────────────
btnOpenCamera.addEventListener("click", async () => {
  try {
    const dim = await openCamera(camPreview, camCanvas);
    camWidth  = dim.width;
    camHeight = dim.height;
    camPreview.style.display = "block";
    camStatusBar.textContent = `Camera active (${camWidth}×${camHeight}) — ready to scan`;
    camStatusBar.className   = "cam-status cam-status-on";
    send({
      type:     "set_fov",
      hfov_deg: parseFloat(cfgFov.value),
      width:    camWidth,
      height:   camHeight,
    });
    statusMsg(`Camera open: ${camWidth}×${camHeight}`);
  } catch (e) {
    alert("Camera error: " + e.message);
  }
});

// ── Session / Scan ────────────────────────────────────────────────────────────
btnStartSess.addEventListener("click", () => {
  if (scanning) { alert("Scan already running"); return; }

  const angle    = parseFloat(sessAngle.value)  || 0;
  const distance = parseFloat(sessDist.value)   || 2.0;
  const height   = parseFloat(sessHeight.value) || 1.5;

  send({
    type:     "set_session",
    angle,
    distance,
    height,
    hfov_deg: parseFloat(cfgFov.value),
    img_width:  camWidth,
    img_height: camHeight,
  });

  // Give server a tick to register session then start
  setTimeout(() => {
    send({ type: "start_scan" });
    scanning = true;
    progressBar.style.width = "0%";
    progressLabel.textContent = `0 / ${cfgPixels.value}`;
    scanBlock.style.display = "block";
  }, 200);
});

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

function addSessionCard(sessionId, detected, total) {
  sessions.push({ id: sessionId, detected, total });
  const pct = total > 0 ? Math.round((detected / total) * 100) : 0;
  const badge = pct >= 70 ? "badge-good" : pct >= 40 ? "badge-medium" : "badge-poor";
  const card = document.createElement("div");
  card.className = "session-card";
  card.innerHTML = `
    <span>Session ${sessionId}</span>
    <span class="badge ${badge}">${detected}/${total} (${pct}%)</span>
  `;
  sessionList.appendChild(card);
}

function showSuggestion(msg) {
  const angle = msg.angle ?? 0;
  suggAngle.textContent  = `${angle}°`;
  suggDist.textContent   = `${msg.distance ?? 2}m from trunk · same height`;
  suggReason.textContent = msg.reason ?? "";
  suggCard.style.display = "block";
}

btnUseSugg.addEventListener("click", () => {
  if (!lastSuggestion) return;
  sessAngle.value = lastSuggestion.angle;
  sessDist.value  = lastSuggestion.distance;
  // Scroll to top of Scan tab so user sees the form
  document.getElementById("tab-scan").scrollTo({ top: 0, behavior: "smooth" });
});

function updateConfidence(msg) {
  const pct = Math.round((msg.overall ?? 0) * 100);
  confidencePct.textContent  = `${pct}%`;
  confidenceGrade.textContent = msg.grade ?? "–";

  const hue = pct >= 75 ? "#69f0ae" : pct >= 50 ? "#ffee58" : "#ef5350";
  confidencePct.style.color  = hue;
  confidenceGrade.style.color = hue;

  confidenceDet.textContent =
    `Coverage ${Math.round((msg.coverage ?? 0)*100)}% · ` +
    `High ${msg.high ?? 0} · Med ${msg.medium ?? 0} · Low ${msg.low ?? 0} · Unseen ${msg.unseen ?? 0}`;

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
    const conf  = p.position != null ? `${Math.round((p.confidence ?? 0) * 100)}%` : "–";
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

function drawDiff(srcCanvas, bgImageData, pixelIdx, result) {
  const W = srcCanvas.width;
  const H = srcCanvas.height;
  if (!W || !H) return;

  const srcCtx  = srcCanvas.getContext("2d", { willReadFrequently: true });
  const litData = srcCtx.getImageData(0, 0, W, H);
  const bg      = bgImageData.data;
  const lit     = litData.data;

  // Scale down 4× for the preview canvas
  const scale  = 4;
  const dW     = Math.floor(W / scale);
  const dH     = Math.floor(H / scale);
  diffCanvas.width  = dW;
  diffCanvas.height = dH;

  const dCtx  = diffCanvas.getContext("2d");
  const imgD  = dCtx.createImageData(dW, dH);
  const d     = imgD.data;

  let peakLum = 0;
  for (let dy = 0; dy < dH; dy++) {
    for (let dx = 0; dx < dW; dx++) {
      const sx = dx * scale;
      const sy = dy * scale;
      const si = (sy * W + sx) * 4;
      const di = (dy * dW + dx) * 4;
      const dr = Math.max(0, lit[si]   - bg[si]);
      const dg = Math.max(0, lit[si+1] - bg[si+1]);
      const db = Math.max(0, lit[si+2] - bg[si+2]);
      const lum = 0.299 * dr + 0.587 * dg + 0.114 * db;
      if (lum > peakLum) peakLum = lum;
      // Amplify 4× so faint signals are visible
      d[di]   = Math.min(255, dr * 4);
      d[di+1] = Math.min(255, dg * 4);
      d[di+2] = Math.min(255, db * 4);
      d[di+3] = 255;
    }
  }
  dCtx.putImageData(imgD, 0, 0);

  // Draw crosshair if detected
  if (result.found) {
    dCtx.strokeStyle = "#69f0ae";
    dCtx.lineWidth   = 1;
    const cx = result.cx / scale;
    const cy = result.cy / scale;
    dCtx.beginPath();
    dCtx.moveTo(cx - 8, cy); dCtx.lineTo(cx + 8, cy);
    dCtx.moveTo(cx, cy - 8); dCtx.lineTo(cx, cy + 8);
    dCtx.stroke();
  }

  const status = result.found
    ? `Pixel ${pixelIdx + 1}: detected  conf=${(result.conf * 100).toFixed(0)}%  peak=${peakLum.toFixed(0)}`
    : `Pixel ${pixelIdx + 1}: not found  peak=${peakLum.toFixed(0)}`;
  diffLabel.textContent = status;
  console.log("[BlinkyMap]", status);
}

// ── Boot ──────────────────────────────────────────────────────────────────────
connect();
