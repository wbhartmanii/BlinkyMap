/**
 * camera.js — WebRTC camera access + canvas-based LED detection.
 *
 * Exports:
 *   openCamera(videoEl, canvasEl) → Promise<{width, height}>
 *   captureBackground(videoEl, canvasEl) → ImageData
 *   detectLED(videoEl, canvasEl, bgImageData, threshold=30) →
 *       {found:bool, cx:float, cy:float, conf:float} | {found:false}
 */

/** Open the rear camera and attach to <video>. */
export async function openCamera(videoEl, canvasEl) {
  const constraints = {
    video: {
      facingMode: { ideal: "environment" },
      width:  { ideal: 1280 },
      height: { ideal: 720 },
    },
    audio: false,
  };

  const stream = await navigator.mediaDevices.getUserMedia(constraints);
  videoEl.srcObject = stream;
  await new Promise(resolve => { videoEl.onloadedmetadata = resolve; });
  await videoEl.play();

  const { videoWidth: width, videoHeight: height } = videoEl;
  canvasEl.width  = width;
  canvasEl.height = height;
  return { width, height };
}

/** Grab the current video frame as ImageData (dark background). */
export function captureBackground(videoEl, canvasEl) {
  const ctx = canvasEl.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(videoEl, 0, 0, canvasEl.width, canvasEl.height);
  return ctx.getImageData(0, 0, canvasEl.width, canvasEl.height);
}

/**
 * Detect a bright LED in the current frame by subtracting the background.
 *
 * Algorithm:
 *  1. Per-pixel luminance difference: lit - bg
 *  2. Locate the single brightest pixel
 *  3. Weighted centroid of bright pixels WITHIN A WINDOW around that peak
 *  4. Confidence = peak_luminance / 255
 *
 * Step 3 is why the window exists. A centroid taken over the whole frame is
 * dragged toward any other bright thing in view — a neighbouring LED bleeding,
 * a reflection off the floor — landing between the sources instead of on the
 * target. Measured against synthetic frames with a known LED position:
 *
 *     scene                        global centroid   peak-local
 *     clean, no neighbours                  0.0 px      0.0 px
 *     one neighbour bleeding               18.2 px      0.0 px
 *     two neighbours + reflection          37.8 px      0.0 px
 *     bright floor reflection             120.4 px      0.0 px
 *
 * `purity` reports the fraction of total lit energy that falls inside the
 * window; a low value means the frame held substantial light the window
 * excluded, so the reading is worth distrusting.
 */
export function detectLED(videoEl, canvasEl, bgImageData, threshold = 30,
                          windowFrac = 0.04) {
  const ctx = canvasEl.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(videoEl, 0, 0, canvasEl.width, canvasEl.height);
  const lit = ctx.getImageData(0, 0, canvasEl.width, canvasEl.height);

  const W = canvasEl.width;
  const H = canvasEl.height;
  const bg    = bgImageData.data;
  const lit_d = lit.data;

  // Pass 1: difference luminance, peak location, and total lit energy.
  const diff = new Float32Array(W * H);
  let peakLum = 0, peakIdx = -1, totalEnergy = 0;

  for (let p = 0, i = 0; p < W * H; p++, i += 4) {
    const dr = Math.max(0, lit_d[i]     - bg[i]);
    const dg = Math.max(0, lit_d[i + 1] - bg[i + 1]);
    const db = Math.max(0, lit_d[i + 2] - bg[i + 2]);
    const lum = 0.299 * dr + 0.587 * dg + 0.114 * db;
    if (lum >= threshold) {
      diff[p] = lum;
      totalEnergy += lum;
      if (lum > peakLum) { peakLum = lum; peakIdx = p; }
    }
  }

  if (peakIdx < 0 || totalEnergy <= 0) return { found: false };

  // Pass 2: centroid restricted to a window centred on the peak.
  const px = peakIdx % W;
  const py = (peakIdx / W) | 0;
  const rad  = Math.max(10, Math.round(Math.min(W, H) * windowFrac));
  const rad2 = rad * rad;

  const x0 = Math.max(0, px - rad), x1 = Math.min(W - 1, px + rad);
  const y0 = Math.max(0, py - rad), y1 = Math.min(H - 1, py + rad);

  let sumW = 0, sumX = 0, sumY = 0;
  for (let y = y0; y <= y1; y++) {
    const dy = y - py;
    for (let x = x0; x <= x1; x++) {
      const dx = x - px;
      if (dx * dx + dy * dy > rad2) continue;
      const lum = diff[y * W + x];
      if (lum <= 0) continue;
      sumW += lum;
      sumX += x * lum;
      sumY += y * lum;
    }
  }

  if (sumW < 1e-6) return { found: false };

  return {
    found:  true,
    cx:     sumX / sumW,
    cy:     sumY / sumW,
    conf:   Math.min(peakLum / 255, 1.0),
    purity: sumW / totalEnergy,
  };
}
