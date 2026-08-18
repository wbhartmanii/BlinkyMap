/**
 * sensor.js — the phone half of BlinkyMap.
 *
 * This client owns the camera and the compass. It reports detections and
 * heading; it never renders the model. Because the operator is physically at
 * the scan position holding this device, it also starts the scan — walking back
 * to the laptop for every angle would be absurd.
 *
 * The server only accepts detections from a client that declared role "sensor",
 * so a stray camera on the control machine cannot race these observations.
 */

import { openCamera, captureBackground, detectLED } from "./camera.js";
import { Compass, angleDelta } from "./compass.js";

const WS_URL = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/blinkymap-ws`;

const $ = (id) => document.getElementById(id);

const wsIndicator   = $("ws-indicator");
const camPreview    = $("cam-preview");
const camCanvas     = $("cam-canvas");
const camStatusBar  = $("cam-status-bar");
const btnOpenCamera = $("btn-open-camera");

const btnEnableCompass = $("btn-enable-compass");
const compassBlock     = $("compass-block");
const compassAngle     = $("compass-angle");
const compassSub       = $("compass-sub");
const compassRaw       = $("compass-raw");
const btnSetRef        = $("btn-set-reference");
const btnClearRef      = $("btn-clear-reference");
const compassFallback  = $("compass-fallback");
const compassFallbackMsg = $("compass-fallback-msg");

const sessAngle   = $("sess-angle");
const sessDist    = $("sess-dist");
const sessHeight  = $("sess-height");
const btnScanHere = $("btn-scan-here");
const scanHint    = $("scan-hint");

const progressBlock = $("scan-progress-block");
const progressBar   = $("scan-progress-bar");
const progressLabel = $("scan-progress-label");
const camOverlay    = $("cam-overlay");
const camWrap       = document.getElementById("cam-wrap");
const btnStopScan   = $("btn-stop-scan");
const lastResult    = $("last-result");

const unitToggle = $("unit-toggle");

let ws = null;
let bgImageData = null;
let camWidth = 1280, camHeight = 720;
let units = "m";
let scanning = false;
let minConf = 0.5;          // relayed from the control UI via sensor_config
let hfovDeg = 60;           // ditto
let hasCompass = false;
let headingRef = null;      // as reported back by the server
let liveAngle = null;
let targetAngle = null;   // suggested next position, from the server

// ── Units ─────────────────────────────────────────────────────────────────────
function toMeters(v) { return units === "ft" ? v / 3.28084 : v; }

unitToggle.addEventListener("click", (e) => {
  const btn = e.target.closest(".unit-btn");
  if (!btn || btn.dataset.unit === units) return;
  const factor = btn.dataset.unit === "ft" ? 3.28084 : 1 / 3.28084;
  units = btn.dataset.unit;
  unitToggle.querySelectorAll(".unit-btn").forEach(b =>
    b.classList.toggle("active", b.dataset.unit === units));
  document.querySelectorAll(".unit-sfx").forEach(el => el.textContent = units);
  for (const el of [sessDist, sessHeight]) {
    const v = parseFloat(el.value);
    if (!isNaN(v)) el.value = (v * factor).toFixed(2);
  }
});

// ── WebSocket ─────────────────────────────────────────────────────────────────
function connect() {
  ws = new WebSocket(WS_URL);
  ws.onopen = () => {
    wsIndicator.className = "dot dot-green";
    send({ type: "hello", role: "sensor" });
  };
  ws.onclose = () => {
    wsIndicator.className = "dot dot-red";
    setTimeout(connect, 3000);
  };
  ws.onerror = () => { wsIndicator.className = "dot dot-yellow"; };
  ws.onmessage = async (ev) => {
    let msg; try { msg = JSON.parse(ev.data); } catch { return; }
    await onMessage(msg);
  };
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

async function onMessage(msg) {
  switch (msg.type) {
    case "capture_background":
      if (camPreview.srcObject) {
        setCamStatus("Capturing baseline (hold still)…", "cam-status-bg");
        bgImageData = await captureBackground(camPreview, camCanvas);
        setCamStatus("Baseline captured — scanning…", "cam-status-bg");
        send({ type: "background_ready" });
      } else {
        setCamStatus("Camera not open — detections will be skipped", "cam-status-off");
        send({ type: "background_ready" });   // don't stall the scan
      }
      break;

    case "pixel_on": {
      // Capture msg.index, never shared state: detection is async and pixel_off
      // can land first.
      const idx = msg.index;
      if (bgImageData && camPreview.srcObject) {
        await sleep(80);
        const result = detectLED(camPreview, camCanvas, bgImageData, 25);
        drawDiff(result);
        // Live feedback so the confidence gate can be judged against reality.
        if (!result.found) {
          setCamStatus(`Pixel ${idx + 1}: not visible`, "cam-status-off");
        } else if (result.conf < minConf) {
          setCamStatus(
            `Pixel ${idx + 1}: rejected — conf ${result.conf.toFixed(2)} ` +
            `(sparse ${result.sparsity.toFixed(2)} · compact ` +
            `${result.compactness.toFixed(2)} · unique ${result.uniqueness.toFixed(2)})`,
            "cam-status-off");
        } else {
          setCamStatus(`Pixel ${idx + 1}: seen — conf ${result.conf.toFixed(2)}`,
                       "cam-status-on");
        }
        if (result.found && result.conf >= minConf) {
          send({ type: "detection", index: idx, cx: result.cx, cy: result.cy, conf: result.conf });
        } else {
          send({ type: "no_detection", index: idx });
        }
      } else {
        send({ type: "no_detection", index: idx });
      }
      break;
    }

    case "progress":
      progressBar.style.width = `${((msg.index + 1) / msg.total) * 100}%`;
      progressLabel.textContent = `${msg.index + 1} / ${msg.total}`;
      break;

    case "scan_complete":
      scanning = false;
      progressBlock.style.display = "none";
      btnScanHere.disabled = false;
      lastResult.style.display = "block";
      lastResult.className = "cam-status " + (msg.detected > 0 ? "cam-status-on" : "cam-status-off");
      lastResult.textContent =
        `Session ${msg.session} at ${Math.round(msg.angle)}°: ${msg.detected}/${msg.total} pixels seen`;
      break;

    case "sensor_config":
      // Detection settings are owned by the control UI and relayed here.
      if (typeof msg.min_conf === "number") minConf = msg.min_conf;
      if (typeof msg.hfov_deg === "number") hfovDeg = msg.hfov_deg;
      break;

    case "next_suggestion":
      targetAngle = msg.angle ?? null;
      renderCompass();
      break;

    case "sensor_status":
      headingRef = msg.reference;
      liveAngle  = msg.angle;
      renderCompass();
      break;

    case "status":
      if (msg.message) console.log("[BlinkyMap]", msg.message);
      break;
  }
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

function setCamStatus(text, cls) {
  camStatusBar.textContent = text;
  camStatusBar.className = "cam-status " + cls;
}

// ── Camera ────────────────────────────────────────────────────────────────────
btnOpenCamera.addEventListener("click", async () => {
  try {
    const dim = await openCamera(camPreview, camCanvas);
    camWidth = dim.width; camHeight = dim.height;
    camWrap.classList.add("live");
    setCamStatus(`Camera open: ${camWidth}x${camHeight}`, "cam-status-on");
    btnOpenCamera.textContent = "Restart Camera";
  } catch (e) {
    setCamStatus(`Camera error: ${e.message}`, "cam-status-off");
  }
});

// ── Compass ───────────────────────────────────────────────────────────────────
const compass = new Compass();
let lastPoseSent = 0;

btnEnableCompass.addEventListener("click", async () => {
  const res = await compass.start();
  if (res === "ok") {
    hasCompass = true;
    btnEnableCompass.style.display = "none";
    compassBlock.style.display = "block";
    compassFallback.style.display = "none";
    compass.onHeading = (deg, acc) => {
      // Throttle: the sensor fires far faster than anyone needs.
      const now = Date.now();
      if (now - lastPoseSent > 250) {
        lastPoseSent = now;
        send({ type: "pose", heading: deg, accuracy: acc });
      }
      renderCompass(deg, acc);
    };
  } else {
    hasCompass = false;
    showFallback(res);
  }
});

function showFallback(reason) {
  btnEnableCompass.style.display = "none";
  compassBlock.style.display = "none";
  compassFallback.style.display = "block";
  compassFallbackMsg.textContent = {
    denied:      "Compass permission denied — enter the angle manually.",
    unsupported: "This browser exposes no orientation sensor — enter the angle manually.",
    "no-signal": "No magnetometer signal — enter the angle manually.",
  }[reason] || "Compass unavailable — enter the angle manually.";
}

function renderCompass(deg = compass.heading, acc = compass.accuracy) {
  if (!hasCompass) return;
  if (headingRef === null || headingRef === undefined) {
    compassAngle.textContent = "—";
    compassSub.textContent   = "set a 0° reference to begin";
    btnClearRef.style.display = "none";
  } else {
    const a = liveAngle !== null && liveAngle !== undefined
      ? liveAngle
      : (deg !== null ? ((deg - headingRef) % 360 + 360) % 360 : null);
    compassAngle.textContent = a === null ? "—" : `${Math.round(a)}°`;
    compassSub.textContent   = "angle around model";
    btnClearRef.style.display = "block";
  }
  compassRaw.textContent = deg === null
    ? ""
    : `heading ${Math.round(deg)}°${acc ? ` · ±${Math.round(acc)}°` : ""}`;
  if (acc && acc > 20) {
    compassRaw.textContent += " — poor accuracy, move away from metal";
  }

  // Live guidance toward the server's suggested next position.
  const guide = document.getElementById("compass-guide");
  if (!guide) return;
  if (targetAngle === null || liveAngle === null || liveAngle === undefined) {
    guide.style.display = "none";
    return;
  }
  const d = angleDelta(targetAngle, liveAngle);
  guide.style.display = "block";
  if (Math.abs(d) <= 5) {
    guide.className   = "cam-status cam-status-on";
    guide.textContent = `In position for ${Math.round(targetAngle)}° — scan now`;
  } else {
    guide.className   = "cam-status cam-status-bg";
    guide.textContent =
      `Target ${Math.round(targetAngle)}° — move ${Math.abs(Math.round(d))}° ` +
      (d > 0 ? "clockwise" : "counter-clockwise");
  }
}

btnSetRef.addEventListener("click", () => {
  if (compass.heading === null) return;
  send({ type: "pose", heading: compass.heading, accuracy: compass.accuracy,
         set_reference: true });
});

btnClearRef.addEventListener("click", () => send({ type: "clear_reference" }));

// ── Start a scan from here ────────────────────────────────────────────────────
btnScanHere.addEventListener("click", () => {
  if (scanning) return;
  if (!camPreview.srcObject) {
    scanHint.textContent = "Open the camera first.";
    return;
  }
  if (hasCompass && (headingRef === null || headingRef === undefined)) {
    scanHint.textContent = "Set a 0° reference before scanning.";
    return;
  }
  scanHint.textContent = "";

  const payload = {
    type: "set_session",
    distance: toMeters(parseFloat(sessDist.value) || (units === "ft" ? 6.56 : 2.0)),
    height:   toMeters(parseFloat(sessHeight.value) || (units === "ft" ? 4.92 : 1.5)),
    hfov_deg: hfovDeg,
    img_width: camWidth,
    img_height: camHeight,
  };
  if (hasCompass) {
    payload.angle_source = "compass";   // server substitutes the measured angle
    payload.angle = liveAngle ?? 0;
  } else {
    payload.angle = parseFloat(sessAngle.value) || 0;
  }
  send(payload);

  setTimeout(() => {
    send({ type: "start_scan" });
    scanning = true;
    btnScanHere.disabled = true;
    lastResult.style.display = "none";
    progressBlock.style.display = "block";
    progressBar.style.width = "0%";
    progressLabel.textContent = "0 / 0";
  }, 250);
});

btnStopScan.addEventListener("click", () => {
  send({ type: "stop_scan" });
  scanning = false;
  btnScanHere.disabled = false;
  progressBlock.style.display = "none";
});

// ── Detection marker, drawn over the live preview ─────────────────────────────
function drawDiff(result) {
  if (!camOverlay || !camPreview.videoWidth) return;
  // Match the overlay's backing store to its displayed size so the marker lands
  // exactly where the LED appears, independent of CSS scaling.
  const rect = camPreview.getBoundingClientRect();
  if (camOverlay.width !== rect.width || camOverlay.height !== rect.height) {
    camOverlay.width  = rect.width;
    camOverlay.height = rect.height;
  }
  const ctx = camOverlay.getContext("2d");
  ctx.clearRect(0, 0, camOverlay.width, camOverlay.height);
  if (!result || !result.found) return;

  const sx = camOverlay.width  / camWidth;
  const sy = camOverlay.height / camHeight;
  const x  = result.cx * sx;
  const y  = result.cy * sy;

  // Purity is the share of lit energy inside the detection window; a low value
  // means other bright things were in frame, so flag the reading as suspect.
  // Green = accepted, yellow = seen but below the confidence gate.
  const accepted = (result.conf ?? 0) >= minConf;
  ctx.strokeStyle = accepted ? "#69f0ae" : "#ffee58";
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.arc(x, y, 12, 0, Math.PI * 2); ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(x - 18, y); ctx.lineTo(x - 5, y);
  ctx.moveTo(x + 5, y);  ctx.lineTo(x + 18, y);
  ctx.moveTo(x, y - 18); ctx.lineTo(x, y - 5);
  ctx.moveTo(x, y + 5);  ctx.lineTo(x, y + 18);
  ctx.stroke();
}

// Show manual entry until the compass is proven to work.
compassFallback.style.display = "none";
connect();
