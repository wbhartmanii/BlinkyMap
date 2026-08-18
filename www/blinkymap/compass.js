/**
 * compass.js — device heading, normalised across iOS and Android.
 *
 * The two platforms disagree in ways that matter:
 *
 *   iOS Safari   `deviceorientation` carries `webkitCompassHeading`, which is
 *                degrees clockwise from MAGNETIC NORTH. Its `alpha` is relative
 *                to wherever the device happened to be when listening started,
 *                so alpha must NOT be used here.
 *   Android      `deviceorientationabsolute` carries an absolute `alpha`, which
 *                is degrees COUNTER-clockwise from north — hence 360 - alpha.
 *
 * iOS 13+ also requires DeviceOrientationEvent.requestPermission(), which needs
 * transient activation: it must be called from inside a real user gesture.
 *
 * All of this needs a secure context, which the plugin already has.
 *
 * Usage:
 *   import { Compass } from './compass.js';
 *   const c = new Compass();
 *   if (c.supported) {
 *     await c.start();                  // call from a click handler
 *     c.onHeading = (deg, accuracy) => { ... };
 *   }
 */

export class Compass {
  constructor() {
    this.onHeading = null;
    this.heading   = null;   // degrees clockwise from magnetic north
    this.accuracy  = null;   // +/- degrees where the platform reports it
    this.running   = false;
    this._handler  = null;
    this._event    = null;
  }

  /** Whether this browser exposes device orientation at all. */
  get supported() {
    return typeof window !== "undefined" && "DeviceOrientationEvent" in window;
  }

  /** True when the platform gates access behind an explicit permission prompt. */
  get needsPermission() {
    return this.supported &&
           typeof DeviceOrientationEvent.requestPermission === "function";
  }

  /**
   * Begin listening. Must be called from a user gesture on iOS.
   * Resolves to one of: "ok", "denied", "unsupported", "no-signal".
   */
  async start() {
    if (!this.supported) return "unsupported";

    if (this.needsPermission) {
      let res;
      try {
        res = await DeviceOrientationEvent.requestPermission();
      } catch {
        // Throws when not called from a genuine user gesture.
        return "denied";
      }
      if (res !== "granted") return "denied";
    }

    // Prefer the absolute event where it exists; iOS only fires `deviceorientation`.
    this._event = ("ondeviceorientationabsolute" in window)
      ? "deviceorientationabsolute"
      : "deviceorientation";

    this._handler = (e) => this._onEvent(e);
    window.addEventListener(this._event, this._handler, true);
    this.running = true;

    // Confirm real data actually arrives — a listener can attach and stay silent
    // on a device with no magnetometer.
    const gotSignal = await new Promise((resolve) => {
      const t = setTimeout(() => resolve(this.heading !== null), 1500);
      const probe = () => {
        if (this.heading !== null) { clearTimeout(t); resolve(true); }
      };
      this._probe = setInterval(probe, 100);
      setTimeout(() => clearInterval(this._probe), 1600);
    });

    if (!gotSignal) { this.stop(); return "no-signal"; }
    return "ok";
  }

  stop() {
    if (this._handler && this._event) {
      window.removeEventListener(this._event, this._handler, true);
    }
    if (this._probe) clearInterval(this._probe);
    this._handler = null;
    this.running  = false;
  }

  _onEvent(e) {
    let deg = null;

    if (typeof e.webkitCompassHeading === "number" && !isNaN(e.webkitCompassHeading)) {
      // iOS: already clockwise from magnetic north.
      deg = e.webkitCompassHeading;
      if (typeof e.webkitCompassAccuracy === "number" && e.webkitCompassAccuracy >= 0) {
        this.accuracy = e.webkitCompassAccuracy;
      }
    } else if (typeof e.alpha === "number" && !isNaN(e.alpha)) {
      // Only trust alpha when the platform says it is absolute; a relative alpha
      // (iOS, or `deviceorientation` on Android) is meaningless as a heading.
      const isAbsolute = e.absolute === true ||
                         this._event === "deviceorientationabsolute";
      if (!isAbsolute) return;
      deg = (360 - e.alpha) % 360;   // alpha runs counter-clockwise
    }

    if (deg === null) return;
    deg = ((deg % 360) + 360) % 360;
    this.heading = deg;
    if (this.onHeading) this.onHeading(deg, this.accuracy);
  }
}

/** Smallest signed difference a-b, wrapped to (-180, 180]. */
export function angleDelta(a, b) {
  let d = (a - b) % 360;
  if (d > 180)  d -= 360;
  if (d <= -180) d += 360;
  return d;
}
