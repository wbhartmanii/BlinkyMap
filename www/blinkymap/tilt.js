/**
 * tilt.js — how far below horizontal the rear camera is aimed.
 *
 * WHY THIS EXISTS
 * ---------------
 * `_projection_matrix` places the camera at (d·sinφ, h, d·cosφ) and aims it at
 * (0, t, 0). The rotation it derives depends on `h` and `t` only through their
 * DIFFERENCE. Replacing (h, t) with (h−t, 0) yields an identical rotation and
 * translates the eye by a constant −t; applied at every position that is a rigid
 * translation of the whole reconstruction, and `_normalize` subtracts the
 * per-axis minimum before export, so it vanishes entirely.
 *
 * So the geometry never needed two typed heights. It needed one number:
 *
 *     h − t = distance · tan(depression)
 *
 * which is what this module measures. The operator centres the prop in the
 * viewfinder, and the aim point defines itself — no one has to know or agree on
 * how high the prop's middle is.
 *
 * WHY THIS IS NOT THE TILT WORK THAT FAILED BEFORE
 * ------------------------------------------------
 * An earlier attempt bolted a measured rotation ON TOP of a look-at built from a
 * typed position, and made things worse: look-at is self-consistent under
 * position error, and adding rotation double-counts the tilt and destroys that
 * compensation. This uses the same measurement to infer the POSITION instead.
 * The camera still aims exactly at the target; nothing is applied twice.
 *
 * THE MEASUREMENT
 * ---------------
 * From the W3C device→world rotation R = Rz(α)·Rx(β)·Ry(γ), the world-up
 * component of a device vector is the third row:
 *
 *     [ −cosβ·sinγ,   sinβ,   cosβ·cosγ ]
 *
 * The rear camera looks along device −Z, so its vertical component is
 * −cosβ·cosγ and the depression angle below horizontal is
 *
 *     θ = asin(cosβ · cosγ)
 *
 * Two things follow, both of which matter:
 *   - **α does not appear.** The tilt is a pure accelerometer reading and is
 *     completely independent of the magnetometer — so it stays accurate
 *     (±1–2°) on a device whose compass is being thrown by metal, and it works
 *     even when `Compass` reports "no-signal".
 *   - **It is orientation-agnostic.** β and γ are reported in the device's
 *     natural frame no matter how the screen is rotated, and rotating the
 *     screen does not move the physical camera relative to the device body.
 *     Portrait and landscape need no special case; naive use of β alone does.
 *
 * Sanity: upright portrait aimed at the horizon (β=90, γ=0) → 0°. Flat on a
 * table, screen up (β=0, γ=0) → 90° down. Landscape at the horizon
 * (β=0, γ=−90) → 0°.
 */

/** Beyond this the tan() blows up and any distance error amplifies with it. */
export const MAX_PITCH_DEG = 60;

export class Tilt {
  constructor() {
    this.onPitch  = null;
    this.devicePitch    = null;   // degrees; + = aimed below horizontal
    this.running  = false;
    this._handler = null;
    this._samples = null;   // non-null while a scan is being sampled
  }

  get supported() {
    return typeof window !== "undefined" && "DeviceOrientationEvent" in window;
  }

  /** Same iOS gate as the compass — start both from one user gesture. */
  get needsPermission() {
    return this.supported &&
           typeof DeviceOrientationEvent.requestPermission === "function";
  }

  /** Resolves to "ok" | "denied" | "unsupported" | "no-signal". */
  async start() {
    if (!this.supported) return "unsupported";

    if (this.needsPermission) {
      let res;
      try {
        res = await DeviceOrientationEvent.requestPermission();
      } catch {
        return "denied";     // not called from a genuine user gesture
      }
      if (res !== "granted") return "denied";
    }

    // Plain `deviceorientation` on purpose: it fires on both platforms and
    // carries beta/gamma. The absolute variant adds only a trustworthy alpha,
    // which this measurement does not use.
    this._handler = (e) => this._onEvent(e);
    window.addEventListener("deviceorientation", this._handler, true);
    this.running = true;

    // A listener can attach and stay silent on hardware with no accelerometer.
    const gotSignal = await new Promise((resolve) => {
      const t = setTimeout(() => resolve(this.devicePitch !== null), 1500);
      const probe = setInterval(() => {
        if (this.devicePitch !== null) { clearTimeout(t); clearInterval(probe); resolve(true); }
      }, 100);
      setTimeout(() => clearInterval(probe), 1600);
    });

    if (!gotSignal) { this.stop(); return "no-signal"; }
    return "ok";
  }

  stop() {
    if (this._handler) {
      window.removeEventListener("deviceorientation", this._handler, true);
    }
    this._handler = null;
    this.running  = false;
  }

  _onEvent(e) {
    if (typeof e.beta !== "number" || isNaN(e.beta)) return;
    if (typeof e.gamma !== "number" || isNaN(e.gamma)) return;

    const b = e.beta  * Math.PI / 180;
    const g = e.gamma * Math.PI / 180;
    // Clamp before asin: rounding can push the product a hair past ±1.
    const up  = Math.max(-1, Math.min(1, Math.cos(b) * Math.cos(g)));
    const deg = Math.asin(up) * 180 / Math.PI;

    this.devicePitch = deg;
    if (this._samples) this._samples.push(deg);
    if (this.onPitch) this.onPitch(deg);
  }

  /** Begin accumulating samples for the scan about to run. */
  beginSample() { this._samples = this.devicePitch === null ? [] : [this.devicePitch]; }

  /**
   * Close the sampling window.
   *
   * `spread` is the peak-to-peak swing across the capture, which is the figure
   * that matters: the frames are taken over several seconds and every one of
   * them is attributed to a single pose. A drifting hand does not average out,
   * it smears the geometry.
   */
  endSample() {
    const s = this._samples || [];
    this._samples = null;
    if (!s.length) return null;
    const min  = Math.min(...s);
    const max  = Math.max(...s);
    const mean = s.reduce((a, b) => a + b, 0) / s.length;
    return { mean, min, max, spread: max - min, n: s.length };
  }

  /** True when the phone is aimed steeply enough that tan() stops behaving. */
  static tooSteep(deg) { return Math.abs(deg) > MAX_PITCH_DEG; }
}

/** Camera height above its own aim point, from the leg you typed and the leg measured. */
export function heightAboveAim(distance_m, device_pitch_deg) {
  const clamped = Math.max(-MAX_PITCH_DEG, Math.min(MAX_PITCH_DEG, device_pitch_deg));
  return distance_m * Math.tan(clamped * Math.PI / 180);
}
