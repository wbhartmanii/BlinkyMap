/**
 * coded.js — structured-light ("coded") pixel detection.
 *
 * Every frame lights all pixels at once, each coloured by one digit of its
 * base-3 index (0 red, 1 green, 2 blue). A pixel is located by finding the
 * image region whose observed colour matches that pixel's code in EVERY frame.
 *
 * Why this beats lighting one pixel at a time:
 *   - A reflection has to coincidentally show the right colour in every frame.
 *     At 5 frames that is 1 in 243; at 8 frames, 1 in 6561.
 *   - An occluded pixel simply has no region carrying its code, so "not seen"
 *     is a geometric fact rather than a tuned confidence threshold.
 *   - Every frame lights the same number of LEDs, so scene brightness is
 *     constant and the camera does not re-expose between frames.
 *   - log3(n)+2 frames instead of n: 5 rather than 24, 8 rather than 500.
 *
 * Implementation note: the obvious approach — build a mask per pixel and
 * intersect — costs pixels x frames x imageArea, which is ~110M operations for
 * 24 pixels at 720x1280. Instead each image pixel's per-frame colour classes
 * are folded into a single base-3 code, accumulating centroids in one pass.
 * That is one traversal per frame regardless of how many pixels are being
 * mapped, so a 500-pixel prop costs the same per frame as a 24-pixel one.
 */

const CLASS_NONE = 3;

// Defaults for the colour classifier. A region counts as red/green/blue only if
// that channel clearly leads: dim and near-neutral regions are left undecided so
// they can never satisfy a code. Shared so that the coarse signature used to
// CONFIRM a frame classifies exactly the way the capture that follows it will —
// a confirmation made under different thresholds would be confirming a
// different image.
const CLASS_MIN_LEVEL = 40;
const CLASS_MARGIN    = 1.25;

/**
 * Classify every pixel of `data` by dominant colour channel into `out`.
 *
 * One implementation, two callers (the capture path and the confirmation
 * signature) — kept as a free function with everything passed in so the hot
 * loop stays monomorphic; this runs once per image pixel per frame.
 */
function classifyInto(data, n, out, minLevel, margin, mask) {
  let decided = 0;
  for (let p = 0, i = 0; p < n; p++, i += 4) {
    if (mask && mask[p]) { out[p] = CLASS_NONE; continue; }
    const r = data[i], g = data[i + 1], b = data[i + 2];
    let c = CLASS_NONE;
    if (r >= minLevel && r >= g * margin && r >= b * margin)      c = 0;
    else if (g >= minLevel && g >= r * margin && g >= b * margin) c = 1;
    else if (b >= minLevel && b >= r * margin && b >= g * margin) c = 2;
    if (c !== CLASS_NONE) decided++;
    out[p] = c;
  }
  return decided;
}

/**
 * Coarse colour-class map of one video frame, for confirming that the string
 * actually changed.
 *
 * Intended to be fed a heavily downscaled frame (the GPU does the averaging in
 * drawImage), so a poll costs a small readback rather than a full 720p one and
 * can run several times a second while the operator waits.
 */
export function frameSignature(imageData, minLevel = CLASS_MIN_LEVEL,
                               margin = CLASS_MARGIN) {
  const n = imageData.width * imageData.height;
  const cls = new Uint8Array(n);
  const decided = classifyInto(imageData.data, n, cls, minLevel, margin, null);
  return { cls, n, decided, litFrac: decided / n };
}

/**
 * How much two signatures differ, as a fraction of the lit region.
 *
 * Measured against the LIT population rather than the whole image: the prop
 * occupies a small part of the frame, so a change normalised by image area
 * would be a fraction of a percent whether the pattern changed or not. The
 * floor keeps a nearly dark pair — where a couple of noisy points would
 * otherwise read as a total change — from claiming a change it cannot support.
 */
export function signatureChange(a, b) {
  if (!a || !b || a.n !== b.n) return 1;
  let differ = 0;
  for (let p = 0; p < a.n; p++) if (a.cls[p] !== b.cls[p]) differ++;
  const pop = Math.max(a.decided, b.decided, a.n * 0.005);
  return Math.min(1, differ / pop);
}

/** Accumulates classified frames and resolves pixel positions at the end. */
export class CodedScan {
  /**
   * @param {number} width   camera frame width
   * @param {number} height  camera frame height
   * @param {number} frames  number of coded frames expected
   */
  constructor(width, height, frames) {
    this.w = width;
    this.h = height;
    this.frames = frames;
    this.captured = 0;
    // One class byte per image pixel per frame: 0=R, 1=G, 2=B, 3=undecided.
    // Sparse and index-addressed, so a missed frame is detectable.
    this.classes = new Array(frames);
    this.mask = null;   // set by setMask() from an all-dark reference frame
  }

  /**
   * Mask out everything already glowing before any pixel is lit.
   *
   * A steady light in view — a controller status LED, a standby lamp, a window
   * — is not simply ignored by the coding. Close to it the camera sees that
   * light PLUS whatever the string reflects, so as the string cycles red, green
   * and blue the dominant channel flips and the region produces a varying code
   * that can land on a real pixel index. A perfectly steady source is rejected
   * by the checksum (all-one-digit codes are never valid words), but a
   * contaminated one is not.
   *
   * Masking those pixels up front removes the whole class of problem, and costs
   * one frame.
   */
  setMask(imageData, level = 30) {
    const d = imageData.data;
    const n = this.w * this.h;
    const mask = new Uint8Array(n);
    let blocked = 0;
    for (let p = 0, i = 0; p < n; p++, i += 4) {
      if (d[i] >= level || d[i + 1] >= level || d[i + 2] >= level) {
        mask[p] = 1;
        blocked++;
      }
    }
    this.mask = mask;
    return blocked / n;
  }

  /** Indices of frames that were requested but never captured. */
  missingFrames() {
    const out = [];
    for (let f = 0; f < this.frames; f++) if (!this.classes[f]) out.push(f);
    return out;
  }

  /**
   * Classify one captured frame by dominant colour channel.
   *
   * A pixel counts as red/green/blue only if that channel clearly leads: dim
   * regions and near-neutral ones are marked undecided so they can never
   * satisfy a code. `margin` is how far ahead the winner must be — raising it
   * trades detections for certainty.
   */
  addFrame(imageData, index, minLevel = CLASS_MIN_LEVEL, margin = CLASS_MARGIN) {
    const n = this.w * this.h;
    const cls = new Uint8Array(n);
    classifyInto(imageData.data, n, cls, minLevel, margin, this.mask);
    // Stored BY INDEX, not appended. A frame the phone never captured — the
    // server gives up after a timeout and lights the next pattern regardless —
    // would otherwise shift every later frame into the wrong digit position,
    // and since a code is only satisfied when every digit matches, the scan
    // resolves nothing at all rather than failing loudly.
    this.classes[index] = cls;
    this.captured = this.classes.filter(Boolean).length;
    return this.captured;
  }

  /**
   * Fold every image pixel's per-frame classes into one code, accumulating
   * position statistics per code. Single pass; independent of pixel count.
   */
  _accumulate() {
    const n = this.w * this.h;
    const F = this.classes.length;
    const stats = new Map();   // code -> [count, sumX, sumY, sumXX, sumYY]

    for (let p = 0; p < n; p++) {
      let code = 0, ok = true;
      for (let f = 0; f < F; f++) {   // F === this.frames once complete
        const c = this.classes[f][p];
        if (c === CLASS_NONE) { ok = false; break; }
        code = code * 3 + c;
      }
      if (!ok) continue;
      const x = p % this.w, y = (p / this.w) | 0;
      let s = stats.get(code);
      if (s === undefined) { s = [0, 0, 0, 0, 0]; stats.set(code, s); }
      s[0]++; s[1] += x; s[2] += y; s[3] += x * x; s[4] += y * y;
    }
    return stats;
  }

  /**
   * Resolve positions.
   *
   * @param {Object} words     pixel index -> base-3 code string, from the server
   * @param {number} minArea   reject a code carried by fewer pixels than this
   * @param {number} maxSpread reject a code whose pixels are scattered rather
   *                           than forming one blob, as a fraction of frame
   * @returns {{found: Object, misses: Object}}
   */
  resolve(words, minArea = 6, maxSpread = 0.06, edgeFrac = 0.02) {
    const missing = this.missingFrames();
    if (missing.length) {
      // Refuse rather than return confident nonsense: with a frame missing,
      // every code is compared against the wrong digits.
      return { found: {}, misses: {}, incomplete: missing };
    }
    const stats = this._accumulate();
    const found = {}, misses = {};
    const diag = Math.hypot(this.w, this.h);

    for (const key of Object.keys(words)) {
      const word = words[key];
      const code = parseInt(word, 3);
      const s = stats.get(code);
      if (s === undefined || s[0] === 0) {
        misses[key] = "no region carries this code";
        continue;
      }
      const [count, sx, sy, sxx, syy] = s;
      if (count < minArea) {
        misses[key] = `only ${count}px carry this code`;
        continue;
      }
      const cx = sx / count, cy = sy / count;
      // A blob touching the frame border is clipped, so its centroid is pulled
      // inward and no longer marks the LED. Worse, stray light entering at the
      // edge can satisfy a code by coincidence: one such reading at v=7, with
      // 0.94 confidence, stretched a real reconstruction's height from 67cm to
      // 95cm on its own. Whether real-but-clipped or spurious, an edge reading
      // cannot be trusted, and a pixel at the very edge of view is one the
      // operator should capture from a better position anyway.
      const margin = Math.max(6, Math.round(Math.min(this.w, this.h) * edgeFrac));
      if (cx < margin || cy < margin ||
          cx > this.w - margin || cy > this.h - margin) {
        misses[key] = `at the frame edge (${Math.round(cx)},${Math.round(cy)}) — reframe`;
        continue;
      }
      // Scatter check: a genuine pixel is one compact blob. A code satisfied by
      // unrelated specks dotted around the frame is noise that happened to line
      // up, and its centroid would be meaningless.
      const spread = Math.sqrt(
        Math.max(0, sxx / count - cx * cx) + Math.max(0, syy / count - cy * cy)
      ) / diag;
      if (spread > maxSpread) {
        misses[key] = `scattered (spread ${(spread * 100).toFixed(1)}%)`;
        continue;
      }
      // Confidence from compactness: tight blob -> near 1, diffuse -> near 0.
      const conf = Math.max(0, Math.min(1, 1 - spread / maxSpread));
      found[key] = { cx, cy, conf, area: count, spread };
    }
    return { found, misses };
  }

  /**
   * Why a scan resolved what it did.
   *
   * A scan that lights correctly and still returns nothing is otherwise a dead
   * end: the failure could be masking, colour classification, or the codes
   * themselves, and they are indistinguishable from the outside.
   */
  stats() {
    const n = this.w * this.h;
    const F = this.classes.length;
    let masked = 0;
    if (this.mask) for (let p = 0; p < n; p++) if (this.mask[p]) masked++;

    // Per-frame: how much of the frame had a decidable colour at all
    const perFrame = [];
    const tally = [0, 0, 0];
    for (let f = 0; f < F; f++) {
      const cls = this.classes[f];
      if (!cls) { perFrame.push(null); continue; }
      let decided = 0;
      for (let p = 0; p < n; p++) {
        if (cls[p] !== CLASS_NONE) { decided++; tally[cls[p]]++; }
      }
      perFrame.push(+(decided / n * 100).toFixed(2));
    }

    // How many image pixels carried a complete code, and the commonest ones
    const stats = this._accumulate();
    let coded = 0;
    for (const [, v] of stats) coded += v[0];
    const top = [...stats.entries()]
      .sort((a, b) => b[1][0] - a[1][0]).slice(0, 5)
      .map(([code, v]) => ({ code: code.toString(3).padStart(F, "0"), px: v[0] }));

    return {
      maskedPct: +(masked / n * 100).toFixed(2),
      decidedPctPerFrame: perFrame,
      classTally: { red: tally[0], green: tally[1], blue: tally[2] },
      pixelsWithCompleteCode: coded,
      distinctCodes: stats.size,
      topCodes: top,
    };
  }

  /** Free the per-frame buffers; they are large. */
  dispose() {
    this.classes = [];
  }
}
