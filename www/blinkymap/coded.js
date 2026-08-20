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
  addFrame(imageData, index, minLevel = 40, margin = 1.25) {
    const d = imageData.data;
    const n = this.w * this.h;
    const cls = new Uint8Array(n);
    const mask = this.mask;
    for (let p = 0, i = 0; p < n; p++, i += 4) {
      if (mask && mask[p]) { cls[p] = CLASS_NONE; continue; }
      const r = d[i], g = d[i + 1], b = d[i + 2];
      let c = CLASS_NONE;
      if (r >= minLevel && r >= g * margin && r >= b * margin)      c = 0;
      else if (g >= minLevel && g >= r * margin && g >= b * margin) c = 1;
      else if (b >= minLevel && b >= r * margin && b >= g * margin) c = 2;
      cls[p] = c;
    }
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

  /** Free the per-frame buffers; they are large. */
  dispose() {
    this.classes = [];
  }
}
