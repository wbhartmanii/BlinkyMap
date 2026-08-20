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
import { CodedScan } from "./coded.js";
import { Tilt, heightAboveAim, MAX_PITCH_DEG } from "./tilt.js";

export const BUILD = "v36";

// Peak-to-peak movement across a capture, beyond which the pose recorded for
// the session no longer describes all of its frames. Pitch comes from the
// accelerometer and is good to ~1-2 deg, so 3 deg is real movement rather than
// noise; heading is magnetometer-grade (~10 deg) and needs a looser bound.
const PITCH_DRIFT_WARN_DEG   = 3.0;
const HEADING_DRIFT_WARN_DEG = 8.0;
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
const manualHeightField = $("manual-height-field");
const tiltReadout = $("tilt-readout");
const aimPoint    = $("aim-point");
const aimLabel    = $("aim-label");
const tiltValue   = $("tilt-value");
const btnScanHere = $("btn-scan-here");
const btnAimLight = $("btn-aim-light");
const scanHint    = $("scan-hint");

const progressBlock = $("scan-progress-block");
const progressBar   = $("scan-progress-bar");
const progressLabel = $("scan-progress-label");
const camOverlay    = $("cam-overlay");
const camWrap       = document.getElementById("cam-wrap");
const hudAngle      = $("hud-angle");
const hudLabel      = $("hud-angle-label");
const setupStrip    = $("setup-strip");
const btnSetup      = $("btn-setup-toggle");
const btnStopScan   = $("btn-stop-scan");
const lastResult    = $("last-result");

const unitToggle = $("unit-toggle");

let ws = null;
let bgImageData = null;
let camWidth = 1280, camHeight = 720;
let units = "ft";
let scanning = false;
let minConf = 0.5;          // relayed from the control UI via sensor_config
let hfovDeg = 60;           // ditto
let hasCompass = false;
let hasTilt = false;
let headingSamples = null;  // non-null while a capture is being sampled
let lastDrift = null;
let headingRef = null;      // as reported back by the server
let liveAngle = null;
let targetAngle = null;   // suggested next position, from the server
let suggestReason = "";
let targetDist = null;   // suggested distance, metres
let lastScan = null;
let aimLightOn = false;
let coded = null;         // active CodedScan
let codedWords = null;    // pixel index -> base-3 code, supplied by the server

// ── Setup panel ───────────────────────────────────────────────────────────────
// Setup is per-session; the working screen is per-position. Collapse it as soon
// as the three prerequisites are met so the stage keeps the space.
function setSetupOpen(open) {
  setupStrip.classList.toggle("open", open);
  btnSetup.classList.toggle("open", open);
}
btnSetup.addEventListener("click", () => setSetupOpen(!setupStrip.classList.contains("open")));

function maybeCollapseSetup() {
  const ready = camPreview.srcObject &&
                (!hasCompass || (headingRef !== null && headingRef !== undefined));
  if (ready) setSetupOpen(false);
}

// ── Units ─────────────────────────────────────────────────────────────────────
function toMeters(v)   { return units === "ft" ? v / 3.28084 : v; }
function fromMeters(m) { return units === "ft" ? m * 3.28084 : m; }

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
  renderTilt();
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
    case "aim_light":
      aimLightOn = !!msg.on;
      btnAimLight.classList.toggle("btn-primary", aimLightOn);
      btnAimLight.classList.toggle("btn-secondary", !aimLightOn);
      btnAimLight.textContent = aimLightOn ? "Aim Light on" : "Aim Light";
      break;

    case "coded_begin":
      // Structured-light scan: a handful of frames, each lighting every pixel.
      codedWords = msg.words || {};
      coded = new CodedScan(camWidth, camHeight, msg.frames);
      setCamStatus(`Coded scan: ${msg.frames} frames for ${msg.pixel_count} pixels`,
                   "cam-status-bg");
      break;

    case "coded_dark": {
      // Everything is off: whatever still shows is not one of ours.
      if (!coded || !camPreview.srcObject) {
        send({ type: "coded_frame_captured", index: -1 });
        break;
      }
      await sleep(140);
      const dctx = camCanvas.getContext("2d", { willReadFrequently: true });
      dctx.drawImage(camPreview, 0, 0, camCanvas.width, camCanvas.height);
      const blocked = coded.setMask(
        dctx.getImageData(0, 0, camCanvas.width, camCanvas.height));
      setCamStatus(`Masked stray light (${(blocked * 100).toFixed(1)}% of frame)`,
                   blocked > 0.25 ? "cam-status-bg" : "cam-status-on");
      send({ type: "coded_frame_captured", index: -1 });
      break;
    }

    case "coded_frame": {
      if (!coded || !camPreview.srcObject) {
        send({ type: "coded_frame_captured", index: msg.index });
        break;
      }
      // Let the sensor settle on the newly-lit frame before sampling it.
      await sleep(140);
      const ctx = camCanvas.getContext("2d", { willReadFrequently: true });
      ctx.drawImage(camPreview, 0, 0, camCanvas.width, camCanvas.height);
      coded.addFrame(ctx.getImageData(0, 0, camCanvas.width, camCanvas.height), msg.index);
      setCamStatus(`Captured frame ${msg.index + 1} / ${msg.total}`, "cam-status-on");
      send({ type: "coded_frame_captured", index: msg.index });
      break;
    }

    case "coded_analyze": {
      if (!coded || !codedWords) {
        endCaptureSampling();
        lastDrift = null;
        send({ type: "coded_detections", detections: {} });
        break;
      }
      setCamStatus("Resolving pixel positions…", "cam-status-bg");
      const { found, misses, incomplete } = coded.resolve(codedWords);
      if (incomplete) {
        // Refusing beats returning nonsense: with a frame missing every code is
        // compared against the wrong digits and nothing would resolve anyway.
        setCamStatus(`Missed frame ${incomplete.map(i => i + 1).join(", ")} — scan again`,
                     "cam-status-off");
        send({ type: "coded_detections", detections: {}, incomplete });
        coded.dispose(); coded = null;
        break;
      }
      const out = {};
      for (const k of Object.keys(found)) {
        const p = found[k];
        out[k] = { cx: p.cx, cy: p.cy, conf: p.conf };
      }
      const nFound = Object.keys(found).length;
      const nMiss = Object.keys(misses).length;
      // Log why each pixel was missed — with coded scanning a miss is a real
      // statement about visibility, not a threshold artefact.
      for (const k of Object.keys(misses)) {
        console.log(`[BlinkyMap] pixel ${Number(k) + 1} not seen: ${misses[k]}`);
      }
      const drift = endCaptureSampling();
      send({ type: "coded_detections", detections: out,
             found: nFound, missed: nMiss,
             // Mean over the capture beats the instantaneous value taken when
             // the session was created, and this lands before triangulation.
             pitch_deg: drift.pitch ? drift.pitch.mean : null,
             pitch_spread_deg: drift.pitch ? drift.pitch.spread : 0 });
      drawFound(found);
      setCamStatus(`${nFound} seen · ${nMiss} not visible from here`,
                   nFound > 0 ? "cam-status-on" : "cam-status-off");
      coded.dispose();
      coded = null;
      break;
    }

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
          send({ type: "no_detection", index: idx,
                 conf: result.found ? result.conf : null,
                 sparsity: result.sparsity, compactness: result.compactness,
                 uniqueness: result.uniqueness });
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
      lastScan = msg;
      showNextStep();
      break;

    case "sensor_config":
      // Detection settings are owned by the control UI and relayed here.
      if (typeof msg.min_conf === "number") minConf = msg.min_conf;
      if (typeof msg.hfov_deg === "number") hfovDeg = msg.hfov_deg;
      break;

    case "next_suggestion":
      targetAngle = msg.angle ?? null;
      targetDist  = msg.distance ?? null;
      suggestReason = msg.reason || "";
      renderCompass();
      showNextStep();
      break;

    case "sensor_status":
      headingRef = msg.reference;
      liveAngle  = msg.angle;
      renderCompass();
      showNextStep();
      restoreAimIfMoved();
      maybeCollapseSetup();
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
    maybeCollapseSetup();
    setCamStatus(`Camera open: ${camWidth}x${camHeight}`, "cam-status-on");
    btnOpenCamera.textContent = "Restart Camera";
  } catch (e) {
    setCamStatus(`Camera error: ${e.message}`, "cam-status-off");
  }
});

// ── Tilt ──────────────────────────────────────────────────────────────────────
// The camera's depression below horizontal, which with the typed distance gives
// the camera's height above its own aim point — the only height the geometry
// uses. See tilt.js for why this replaces two typed numbers rather than adding
// a third.
const tilt = new Tilt();

function formatLen(m) {
  return units === "ft" ? `${(m * 3.28084).toFixed(1)}ft` : `${m.toFixed(2)}m`;
}

function renderTilt() {
  // Exactly one of these is live at a time, and the visible one is always the
  // one actually feeding the geometry.
  tiltReadout.style.display       = hasTilt ? "" : "none";
  manualHeightField.style.display = hasTilt ? "none" : "";

  if (!hasTilt || tilt.pitch === null) {
    aimPoint.className = "aim-idle";
    aimLabel.textContent = hasTilt ? "tilt —" : "no tilt";
    tiltValue.textContent = hasTilt ? "—" : "manual";
    tiltValue.className   = hasTilt ? "" : "tilt-warn";
    return;
  }
  const p = tilt.pitch;
  const steep = Tilt.tooSteep(p);
  const dist  = toMeters(parseFloat(sessDist.value) || 2.0);
  const rise  = heightAboveAim(dist, p);

  aimPoint.className = steep ? "aim-warn" : "aim-live";
  aimLabel.textContent = `${p >= 0 ? "▼" : "▲"} ${Math.abs(p).toFixed(1)}°`;

  // Show the derived quantity, not just the raw angle: "camera is 0.30m above
  // what the crosshair is on" is the thing the operator can sanity-check.
  tiltValue.textContent = steep
    ? `${p.toFixed(1)}° too steep`
    : `${p.toFixed(1)}° · ${rise >= 0 ? "+" : "−"}${formatLen(Math.abs(rise))}`;
  tiltValue.className = steep ? "tilt-warn" : "";
}

// Keep the derived height honest when the distance changes.
sessDist.addEventListener("input", () => renderTilt());

// ── Drift across a capture ────────────────────────────────────────────────────
// A coded scan spans several seconds and every frame is attributed to ONE pose.
// A hand that wanders does not average out — it smears the geometry — so the
// swing is measured and reported rather than silently absorbed.
function beginCaptureSampling() {
  lastDrift = null;
  if (hasTilt) tilt.beginSample();
  headingSamples = hasCompass && compass.heading !== null ? [compass.heading] : null;
}

function endCaptureSampling() {
  const pitch = hasTilt ? tilt.endSample() : null;
  let heading = null;
  if (headingSamples && headingSamples.length) {
    // Circular: measure every sample against the first, so a scan straddling
    // 360° does not report a 359° swing.
    const base = headingSamples[0];
    const deltas = headingSamples.map(h => angleDelta(h, base));
    heading = { spread: Math.max(...deltas) - Math.min(...deltas),
                n: headingSamples.length };
  }
  headingSamples = null;
  lastDrift = { pitch, heading };
  return lastDrift;
}

/** Human-readable drift complaint, or "" when the capture was steady. */
function driftWarning() {
  if (!lastDrift) return "";
  const bad = [];
  if (lastDrift.pitch && lastDrift.pitch.spread > PITCH_DRIFT_WARN_DEG) {
    bad.push(`tilt moved ${lastDrift.pitch.spread.toFixed(1)}°`);
  }
  if (lastDrift.heading && lastDrift.heading.spread > HEADING_DRIFT_WARN_DEG) {
    bad.push(`heading moved ${lastDrift.heading.spread.toFixed(0)}°`);
  }
  return bad.join(" · ");
}

// ── Compass ───────────────────────────────────────────────────────────────────
const compass = new Compass();
let lastPoseSent = 0;

btnEnableCompass.addEventListener("click", async () => {
  // Start the tilt sensor first and never gate it on the compass: it needs only
  // the accelerometer, so it still works on a device whose magnetometer is
  // absent or is being thrown by nearby metal.
  const tiltRes = await tilt.start();
  hasTilt = tiltRes === "ok";
  if (hasTilt) tilt.onPitch = () => renderTilt();
  renderTilt();

  const res = await compass.start();
  if (res === "ok") {
    hasCompass = true;
    btnEnableCompass.style.display = "none";
    btnEnableCompass.textContent = "Compass on";
    compassFallback.style.display = "none";
    compass.onHeading = (deg, acc) => {
      // Throttle: the sensor fires far faster than anyone needs.
      const now = Date.now();
      if (now - lastPoseSent > 250) {
        lastPoseSent = now;
        send({ type: "pose", heading: deg, accuracy: acc });
      }
      if (headingSamples) headingSamples.push(deg);
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
  if (!hasCompass) {
    hudAngle.textContent = "—";
    hudLabel.textContent = "manual angle";
    return;
  }
  if (headingRef === null || headingRef === undefined) {
    hudAngle.textContent = "—";
    hudLabel.textContent = "set 0° to begin";
    btnClearRef.style.display = "none";
  } else {
    const a = liveAngle !== null && liveAngle !== undefined
      ? liveAngle
      : (deg !== null ? ((deg - headingRef) % 360 + 360) % 360 : null);
    hudAngle.textContent = a === null ? "—" : `${Math.round(a)}°`;
    hudLabel.textContent = "around model";
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
  if (hasTilt && tilt.pitch !== null && Tilt.tooSteep(tilt.pitch)) {
    scanHint.textContent =
      `Aimed ${Math.abs(tilt.pitch).toFixed(0)}° from horizontal — past ` +
      `${MAX_PITCH_DEG}° the height is dominated by your distance estimate. ` +
      `Step back, or raise the crosshair onto the prop.`;
    return;
  }
  scanHint.textContent = "";

  const payload = {
    type: "set_session",
    distance: toMeters(parseFloat(sessDist.value) || (units === "ft" ? 6.56 : 2.0)),
    hfov_deg: hfovDeg,
    img_width: camWidth,
    img_height: camHeight,
  };
  if (hasTilt && tilt.pitch !== null) {
    // Instantaneous value; the mean over the capture follows with the
    // detections and supersedes it before anything is triangulated.
    payload.pitch_deg = tilt.pitch;
  } else {
    payload.height = toMeters(parseFloat(sessHeight.value) ||
                              (units === "ft" ? 4.92 : 1.5));
  }
  if (hasCompass) {
    payload.angle_source = "compass";   // server substitutes the measured angle
    payload.angle = liveAngle ?? 0;
  } else {
    payload.angle = parseFloat(sessAngle.value) || 0;
  }
  send(payload);

  setTimeout(() => {
    send({ type: "start_coded_scan" });
    scanning = true;
    btnScanHere.disabled = true;
    beginCaptureSampling();
    aimPoint.classList.remove("aim-hidden");
    lastResult.style.display = "none";
    progressBlock.style.display = "block";
    progressBar.style.width = "0%";
    progressLabel.textContent = "0 / 0";
  }, 250);
});

btnAimLight.addEventListener("click", () => {
  send({ type: "aim_light", on: !aimLightOn });
});

btnStopScan.addEventListener("click", () => {
  send({ type: "stop_scan" });
  endCaptureSampling();
  lastDrift = null;
  scanning = false;
  btnScanHere.disabled = false;
  progressBlock.style.display = "none";
});

// ── Tell the operator what to do next ─────────────────────────────────────────
// A scan is useless on its own: triangulation needs the same pixel from two
// positions. The suggested angle was previously only a small chip over the
// preview, which is easy to miss right after a scan completes.
function showNextStep() {
  if (!lastScan) return;
  lastResult.style.display = "block";
  const got = `Session ${lastScan.session} at ${Math.round(lastScan.angle)}°: ` +
              `${lastScan.detected}/${lastScan.total} seen`;

  // Every frame of a capture is attributed to ONE pose. If the phone wandered,
  // that pose no longer describes all of them and the session is smeared.
  // Re-scanning while still standing here costs three seconds; discovering it
  // from the reprojection error costs the model.
  const drift = driftWarning();
  const driftLine = drift
    ? `<strong>Held unsteady — ${drift}</strong><br>Brace against something and ` +
      `scan again from here before moving on.<br>`
    : "";

  if (targetAngle === null) {
    lastResult.className = drift
      ? "cam-status drift-warn"
      : "cam-status " + (lastScan.detected > 0 ? "cam-status-on" : "cam-status-off");
    lastResult.innerHTML = driftLine + got;
    return;
  }
  const here = (liveAngle !== null && liveAngle !== undefined) ? liveAngle : null;
  let move = "";
  if (here !== null) {
    const d = angleDelta(targetAngle, here);
    move = Math.abs(d) <= 5
      ? " — you are there, scan again"
      : ` — walk ${Math.abs(Math.round(d))}° ${d > 0 ? "clockwise" : "counter-clockwise"}`;
  }
  // Distance advice, but only when it is worth acting on. A prop filling a
  // sliver of frame wastes the sensor and magnifies every error in 3D; nobody
  // should be sent trudging back and forth over a few inches.
  let distAdvice = "";
  if (targetDist !== null) {
    const cur = toMeters(parseFloat(sessDist.value) || 0);
    if (cur > 0 && Math.abs(targetDist - cur) / cur > 0.2) {
      distAdvice = `<br>${targetDist < cur ? "Move closer" : "Back up"} to ` +
                   `<strong>${fromMeters(targetDist).toFixed(1)} ${units}</strong> — ` +
                   `${targetDist < cur ? "the prop is small in frame"
                                       : "the prop overfills the frame"}`;
    }
  }
  lastResult.className = drift ? "cam-status drift-warn" : "cam-status next-step";
  lastResult.innerHTML =
    driftLine + `${got}<br><strong>Next: move to ${Math.round(targetAngle)}°</strong>` +
    `${move}${distAdvice}`;
}

/**
 * Bring the aim point back once the operator has walked away from the scanned
 * position. The result circles belong to where they were standing; the crosshair
 * is what they need to frame the next position, and hiding it until the next tap
 * on Scan would take it away exactly when it is being used.
 */
function restoreAimIfMoved() {
  if (!lastScan || scanning) return;
  if (liveAngle === null || liveAngle === undefined) return;
  if (Math.abs(angleDelta(liveAngle, lastScan.angle)) <= 5) return;
  if (!aimPoint.classList.contains("aim-hidden")) return;
  aimPoint.classList.remove("aim-hidden");
  if (camOverlay && camWidth) {
    camOverlay.getContext("2d").clearRect(0, 0, camWidth, camHeight);
  }
}

// ── Show every resolved position after a coded scan ───────────────────────────
function drawFound(found) {
  if (!camOverlay || !camWidth) return;
  if (camOverlay.width !== camWidth || camOverlay.height !== camHeight) {
    camOverlay.width = camWidth; camOverlay.height = camHeight;
  }
  if (hasCompass) aimPoint.classList.add("aim-hidden");
  const ctx = camOverlay.getContext("2d");
  ctx.clearRect(0, 0, camWidth, camHeight);
  const r = Math.max(5, Math.round(Math.min(camWidth, camHeight) * 0.012));
  ctx.lineWidth = Math.max(2, Math.round(r / 3));
  for (const k of Object.keys(found)) {
    const p = found[k];
    ctx.strokeStyle = p.conf >= 0.5 ? "#69f0ae" : "#ffee58";
    ctx.beginPath(); ctx.arc(p.cx, p.cy, r, 0, Math.PI * 2); ctx.stroke();
  }
}

// ── Detection marker, drawn over the live preview ─────────────────────────────
function drawDiff(result) {
  if (!camOverlay || !camWidth) return;
  // Backing store IS the camera frame. CSS applies the same object-fit as the
  // video, so the browser places this canvas pixel-for-pixel over the preview
  // and detector coordinates can be drawn verbatim.
  if (camOverlay.width !== camWidth || camOverlay.height !== camHeight) {
    camOverlay.width  = camWidth;
    camOverlay.height = camHeight;
  }
  const ctx = camOverlay.getContext("2d");
  ctx.clearRect(0, 0, camWidth, camHeight);
  if (!result || !result.found) return;

  const x = result.cx, y = result.cy;
  // Marker sized relative to the frame so it stays legible at any resolution.
  const r = Math.max(6, Math.round(Math.min(camWidth, camHeight) * 0.025));
  const accepted = (result.conf ?? 0) >= minConf;

  ctx.strokeStyle = accepted ? "#69f0ae" : "#ffee58";
  ctx.lineWidth = Math.max(2, Math.round(r / 5));
  ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(x - r * 1.6, y); ctx.lineTo(x - r * 0.5, y);
  ctx.moveTo(x + r * 0.5, y); ctx.lineTo(x + r * 1.6, y);
  ctx.moveTo(x, y - r * 1.6); ctx.lineTo(x, y - r * 0.5);
  ctx.moveTo(x, y + r * 0.5); ctx.lineTo(x, y + r * 1.6);
  ctx.stroke();
}

// First run: setup is the only thing to do, so lead with it.
compassFallback.style.display = "none";
renderTilt();
setSetupOpen(true);
hudLabel.title = BUILD;
document.getElementById("build-tag").textContent = BUILD;
connect();
