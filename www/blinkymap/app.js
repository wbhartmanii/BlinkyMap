/**
 * app.js — BlinkyMap FPP Plugin SPA orchestrator.
 */

import { openCamera, captureBackground, detectLED } from "./camera.js";
import { Viewer3D } from "./viewer3d.js";

const WS_URL = `ws://${location.hostname}:8765`;

const wsIndicator    = document.getElementById("ws-indicator");
const tabBtns        = document.querySelectorAll(".tab-btn");
const tabPanels      = document.querySelectorAll(".tab-panel");
const cfgHost        = document.getElementById("cfg-host");
const cfgUniverse    = document.getElementById("cfg-universe");
const cfgStart       = document.getElementById("cfg-start");
const cfgPixels      = document.getElementById("cfg-pixels");
const cfgDelay       = document.getElementById("cfg-delay");
const cfgFov         = document.getElementById("cfg-fov");
const btnSaveConfig  = document.getElementById("btn-save-config");
const btnOpenCamera  = document.getElementById("btn-open-camera");
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
const viewerContainer= document.getElementById("viewer-container");
const pixelListEl    = document.getElementById("pixel-list");
const exportConfLabel= document.getElementById("export-confidence-label");
const btnExportXmodel= document.getElementById("btn-export-xmodel");
const btnExportCsv   = document.getElementById("btn-export-csv");

let ws = null, viewer = null, bgImageData = null;
let camWidth = 1280, camHeight = 720, scanning = false, currentPixelIdx = -1;
let sessions = [], latestPixels = [], lastSuggestion = null;

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

function connect() {
  ws = new WebSocket(WS_URL);
  ws.onopen  = () => { setIndicator("green"); };
  ws.onclose = () => { setIndicator("red"); setTimeout(connect, 3000); };
  ws.onerror = () => setIndicator("yellow");
  ws.onmessage = async (ev) => {
    let msg; try { msg = JSON.parse(ev.data); } catch { return; }
    await handleServerMessage(msg);
  };
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}
function setIndicator(color) { wsIndicator.className = `dot dot-${color}`; }

async function handleServerMessage(msg) {
  switch (msg.type) {
    case "capture_background":
      if (camPreview.srcObject) bgImageData = captureBackground(camPreview, camCanvas);
      break;
    case "pixel_on":
      currentPixelIdx = msg.index;
      if (bgImageData && camPreview.srcObject) {
        await sleep(80);
        const result = detectLED(camPreview, camCanvas, bgImageData);
        if (result.found) {
          send({ type: "detection", index: currentPixelIdx, cx: result.cx, cy: result.cy, conf: result.conf });
        } else {
          send({ type: "no_detection", index: currentPixelIdx });
        }
      } else {
        send({ type: "no_detection", index: msg.index });
      }
      break;
    case "pixel_off":   currentPixelIdx = -1; break;
    case "progress":    updateProgress(msg.index + 1, msg.total); break;
    case "scan_complete":
      scanning = false; scanBlock.style.display = "none";
      addSessionCard(msg.session, msg.detected, msg.total);
      break;
    case "model":
      latestPixels = msg.pixels;
      if (viewer) viewer.update(latestPixels);
      updatePixelList(latestPixels);
      break;
    case "confidence":   updateConfidence(msg); break;
    case "next_suggestion":
      lastSuggestion = msg;
      showSuggestion(msg);
      if (viewer) viewer.setSuggestion(msg.angle, msg.distance);
      break;
    case "export_ready":
      if (msg.xmodel) triggerDownload("BlinkyTree.xmodel", msg.xmodel, "text/xml");
      if (msg.csv)    triggerDownload("BlinkyTree.csv",    msg.csv,    "text/csv");
      break;
  }
}

btnSaveConfig.addEventListener("click", () => {
  send({ type: "set_config", host: cfgHost.value.trim(), universe: parseInt(cfgUniverse.value),
         start_ch: parseInt(cfgStart.value), pixel_count: parseInt(cfgPixels.value),
         delay: parseFloat(cfgDelay.value) });
});

btnOpenCamera.addEventListener("click", async () => {
  try {
    const dim = await openCamera(camPreview, camCanvas);
    camWidth = dim.width; camHeight = dim.height;
    camPreview.style.display = "block";
    send({ type: "set_fov", hfov_deg: parseFloat(cfgFov.value), width: camWidth, height: camHeight });
  } catch (e) { alert("Camera error: " + e.message); }
});

btnStartSess.addEventListener("click", () => {
  if (scanning) { alert("Scan already running"); return; }
  send({ type: "set_session", angle: parseFloat(sessAngle.value) || 0,
         distance: parseFloat(sessDist.value) || 2.0, height: parseFloat(sessHeight.value) || 1.5,
         hfov_deg: parseFloat(cfgFov.value), img_width: camWidth, img_height: camHeight });
  setTimeout(() => {
    send({ type: "start_scan" });
    scanning = true;
    progressBar.style.width = "0%";
    progressLabel.textContent = `0 / ${cfgPixels.value}`;
    scanBlock.style.display = "block";
  }, 200);
});

btnStopScan.addEventListener("click", () => {
  send({ type: "stop_scan" }); scanning = false; scanBlock.style.display = "none";
});

function updateProgress(done, total) {
  progressBar.style.width = `${total > 0 ? (done/total)*100 : 0}%`;
  progressLabel.textContent = `${done} / ${total}`;
}

function addSessionCard(sessionId, detected, total) {
  sessions.push({ id: sessionId, detected, total });
  const pct = total > 0 ? Math.round((detected / total) * 100) : 0;
  const badge = pct >= 70 ? "badge-good" : pct >= 40 ? "badge-medium" : "badge-poor";
  const card = document.createElement("div");
  card.className = "session-card";
  card.innerHTML = `<span>Session ${sessionId}</span><span class="badge ${badge}">${detected}/${total} (${pct}%)</span>`;
  sessionList.appendChild(card);
}

function showSuggestion(msg) {
  suggAngle.textContent  = `${msg.angle ?? 0}°`;
  suggDist.textContent   = `${msg.distance ?? 2}m from trunk · same height`;
  suggReason.textContent = msg.reason ?? "";
  suggCard.style.display = "block";
}

btnUseSugg.addEventListener("click", () => {
  if (!lastSuggestion) return;
  sessAngle.value = lastSuggestion.angle;
  sessDist.value  = lastSuggestion.distance;
  document.getElementById("tab-scan").scrollTo({ top: 0, behavior: "smooth" });
});

function updateConfidence(msg) {
  const pct = Math.round((msg.overall ?? 0) * 100);
  confidencePct.textContent   = `${pct}%`;
  confidenceGrade.textContent = msg.grade ?? "–";
  const hue = pct >= 75 ? "#69f0ae" : pct >= 50 ? "#ffee58" : "#ef5350";
  confidencePct.style.color = confidenceGrade.style.color = hue;
  confidenceDet.textContent =
    `Coverage ${Math.round((msg.coverage ?? 0)*100)}% · High ${msg.high ?? 0} · Med ${msg.medium ?? 0} · Low ${msg.low ?? 0} · Unseen ${msg.unseen ?? 0}`;
  exportConfLabel.textContent = `Model confidence: ${pct}% (${msg.grade})`;
}

function updatePixelList(pixels) {
  const order = { unseen: 0, low: 1, medium: 2, high: 3 };
  const sorted = [...pixels].sort((a, b) => (order[a.grade] ?? 0) - (order[b.grade] ?? 0));
  pixelListEl.innerHTML = "";
  for (const p of sorted) {
    const row = document.createElement("div");
    row.className = "pixel-row";
    const conf = p.x != null ? `${Math.round((p.confidence ?? 0) * 100)}%` : "–";
    row.innerHTML = `<span>${p.index+1}</span><span class="status-${p.grade}">${p.grade}</span><span>${conf}</span><span>${p.sessions?.length ?? 0}</span>`;
    pixelListEl.appendChild(row);
  }
}

btnExportXmodel.addEventListener("click", () => send({ type: "export", format: "xmodel" }));
btnExportCsv.addEventListener("click",    () => send({ type: "export", format: "csv" }));

function triggerDownload(filename, content, mime) {
  const blob = new Blob([content], { type: mime });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

connect();
