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
 *  1. Compute per-pixel luminance difference: lit - bg
 *  2. Threshold at `threshold` (default 30, 0-255)
 *  3. Find weighted centroid of bright pixels
 *  4. Confidence = peak_luminance / 255
 */
export function detectLED(videoEl, canvasEl, bgImageData, threshold = 30) {
  const ctx = canvasEl.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(videoEl, 0, 0, canvasEl.width, canvasEl.height);
  const lit = ctx.getImageData(0, 0, canvasEl.width, canvasEl.height);

  const W = canvasEl.width;
  const H = canvasEl.height;
  const bg  = bgImageData.data;
  const lit_d = lit.data;

  let sumW = 0, sumX = 0, sumY = 0, peakLum = 0;

  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      // Luminance of difference (clamped to 0)
      const dr = Math.max(0, lit_d[i]   - bg[i]);
      const dg = Math.max(0, lit_d[i+1] - bg[i+1]);
      const db = Math.max(0, lit_d[i+2] - bg[i+2]);
      const lum = 0.299 * dr + 0.587 * dg + 0.114 * db;

      if (lum >= threshold) {
        sumW += lum;
        sumX += x * lum;
        sumY += y * lum;
        if (lum > peakLum) peakLum = lum;
      }
    }
  }

  if (sumW < 1e-6) return { found: false };

  const cx   = sumX / sumW;
  const cy   = sumY / sumW;
  const conf = Math.min(peakLum / 255, 1.0);

  return { found: true, cx, cy, conf };
}
