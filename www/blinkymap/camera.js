/**
 * camera.js — WebRTC camera access + canvas-based LED detection.
 */

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

export function captureBackground(videoEl, canvasEl) {
  const ctx = canvasEl.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(videoEl, 0, 0, canvasEl.width, canvasEl.height);
  return ctx.getImageData(0, 0, canvasEl.width, canvasEl.height);
}

export function detectLED(videoEl, canvasEl, bgImageData, threshold = 30) {
  const ctx = canvasEl.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(videoEl, 0, 0, canvasEl.width, canvasEl.height);
  const lit = ctx.getImageData(0, 0, canvasEl.width, canvasEl.height);
  const W = canvasEl.width, H = canvasEl.height;
  const bg = bgImageData.data, lit_d = lit.data;
  let sumW = 0, sumX = 0, sumY = 0, peakLum = 0;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      const dr = Math.max(0, lit_d[i]   - bg[i]);
      const dg = Math.max(0, lit_d[i+1] - bg[i+1]);
      const db = Math.max(0, lit_d[i+2] - bg[i+2]);
      const lum = 0.299 * dr + 0.587 * dg + 0.114 * db;
      if (lum >= threshold) {
        sumW += lum; sumX += x * lum; sumY += y * lum;
        if (lum > peakLum) peakLum = lum;
      }
    }
  }
  if (sumW < 1e-6) return { found: false };
  return { found: true, cx: sumX / sumW, cy: sumY / sumW, conf: Math.min(peakLum / 255, 1.0) };
}
