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

/**
 * Grab a baseline frame with every pixel off.
 *
 * Takes the per-channel MAXIMUM across several frames rather than a single
 * snapshot. A steady light subtracts out of a single frame fine, but anything
 * that blinks or flickers — a controller status LED, a TV, a phone charger —
 * is dark in the one frame captured and then appears as a bright positive in
 * the difference later, which the peak-finder will happily lock onto. Baking
 * each source in at its brightest guarantees it can never produce a positive
 * difference, at the cost of slightly desensitising those regions.
 */
export async function captureBackground(videoEl, canvasEl, frames = 8, gapMs = 60) {
  const ctx = canvasEl.getContext("2d", { willReadFrequently: true });
  const W = canvasEl.width, H = canvasEl.height;

  ctx.drawImage(videoEl, 0, 0, W, H);
  const acc = ctx.getImageData(0, 0, W, H);
  const a = acc.data;

  for (let f = 1; f < frames; f++) {
    await new Promise(r => setTimeout(r, gapMs));
    ctx.drawImage(videoEl, 0, 0, W, H);
    const cur = ctx.getImageData(0, 0, W, H).data;
    for (let i = 0; i < a.length; i += 4) {
      if (cur[i]     > a[i])     a[i]     = cur[i];
      if (cur[i + 1] > a[i + 1]) a[i + 1] = cur[i + 1];
      if (cur[i + 2] > a[i + 2]) a[i + 2] = cur[i + 2];
    }
  }
  return acc;
}

/**
 * Detect a lit LED in the current frame by subtracting the baseline.
 *
 * Returns a confidence that measures DISCRIMINABILITY, not brightness. The
 * previous score was peak_luminance/255, which pegged near 1.0 for anything
 * bright — a reflection, a glow on a neighbouring pixel, or a global exposure
 * shift all scored ~0.98, so the minimum-confidence gate never rejected
 * anything and every scan reported every pixel as seen. A pixel genuinely
 * hidden behind the prop is a normal, useful outcome; reporting it as found
 * poisons triangulation with a point that is not the pixel.
 *
 * Three independent signals, multiplied:
 *
 *   sparsity    how little of the frame lit up at all. One LED changes a tiny
 *               patch; an exposure shift or auto-white-balance change lifts the
 *               whole frame and scores zero.
 *   compactness a point source occupies a tiny share of frame. Diffuse glow or
 *               a whole-frame shift does not.
 *   uniqueness  penalises a rival peak of similar brightness elsewhere, which
 *               means the choice between them was arbitrary.
 *
 * `purity` (energy inside the detection window over total lit energy) is
 * reported separately as a scene-quality hint.
 */
export function detectLED(videoEl, canvasEl, bgImageData, threshold = 30,
                          windowFrac = 0.04) {
  const ctx = canvasEl.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(videoEl, 0, 0, canvasEl.width, canvasEl.height);
  const lit = ctx.getImageData(0, 0, canvasEl.width, canvasEl.height);

  const W = canvasEl.width, H = canvasEl.height, N = W * H;
  const bg = bgImageData.data, lit_d = lit.data;

  // Pass 1: difference luminance, peak, histogram (for a cheap percentile).
  const diff = new Float32Array(N);
  let peakLum = 0, peakIdx = -1, totalEnergy = 0, litCount = 0;

  for (let p = 0, i = 0; p < N; p++, i += 4) {
    const dr = Math.max(0, lit_d[i]     - bg[i]);
    const dg = Math.max(0, lit_d[i + 1] - bg[i + 1]);
    const db = Math.max(0, lit_d[i + 2] - bg[i + 2]);
    const lum = 0.299 * dr + 0.587 * dg + 0.114 * db;
    if (lum >= threshold) {
      diff[p] = lum;
      totalEnergy += lum;
      litCount++;
      if (lum > peakLum) { peakLum = lum; peakIdx = p; }
    }
  }

  if (peakIdx < 0) return { found: false, reason: "nothing above threshold" };

  // Sparsity: a single LED lights a tiny patch. If a large share of the frame
  // moved, the camera re-exposed or white-balanced and the whole difference is
  // meaningless — 2% of frame is already far more than any point source needs.
  // A real LED plus the glow it throws on its neighbours can legitimately light
  // a few percent of frame; only a whole-frame move means the camera re-exposed.
  const litFrac  = litCount / N;
  const sparsity = Math.max(0, Math.min(1, 1 - litFrac / 0.08));

  // Pass 2: centroid in a window on the peak, plus blob area and rival peak.
  const px = peakIdx % W, py = (peakIdx / W) | 0;
  const rad = Math.max(10, Math.round(Math.min(W, H) * windowFrac));
  const rad2 = rad * rad;
  const half = peakLum * 0.5;

  let sumW = 0, sumX = 0, sumY = 0, blob = 0, rival = 0;
  for (let y = 0; y < H; y++) {
    const dy = y - py;
    for (let x = 0; x < W; x++) {
      const lum = diff[y * W + x];
      if (lum <= 0) continue;
      const dx = x - px;
      const inWin = dx * dx + dy * dy <= rad2;
      if (inWin) {
        sumW += lum; sumX += x * lum; sumY += y * lum;
        if (lum >= half) blob++;
      } else if (lum > rival) {
        rival = lum;
      }
    }
  }

  if (sumW < 1e-6) return { found: false, reason: "no energy in window" };

  // A point source covers a tiny share of frame; 1% is already generous.
  const areaFrac    = blob / N;
  const compactness = Math.max(0, Math.min(1, 1 - areaFrac / 0.01));

  // Only penalise a rival bright enough to have been a plausible alternative.
  // A neighbour glowing at a third of the peak is normal on any real prop and
  // must not count against the reading; a rival at 80% means the peak-finder's
  // choice between them was close to arbitrary.
  const rivalFrac  = peakLum > 0 ? rival / peakLum : 1;
  const uniqueness = Math.max(0, Math.min(1, (1 - rivalFrac) / 0.5));

  return {
    found: true,
    cx: sumX / sumW,
    cy: sumY / sumW,
    // Geometric mean, not a product: three independent 0.85s describe a good
    // detection, but their product is 0.61 and reads like a bad one. The mean
    // keeps a single zero decisive while staying interpretable.
    conf: Math.cbrt(sparsity * compactness * uniqueness),
    purity: sumW / totalEnergy,
    peak: peakLum,
    sparsity, compactness, uniqueness,
  };
}
