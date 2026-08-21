/**
 * Node test for the frame-confirmation signature in coded.js.
 * Run: node tests/test_coded_signature.mjs
 *
 * The signature is what lets the phone answer "yes, the string is showing the
 * new pattern" instead of trusting a timer. Two properties carry that:
 *
 *   - it must read a pattern swap as a large change even though the LEDs cover
 *     a tiny part of the frame, which is why the change is measured against the
 *     LIT region and not the image area;
 *   - it must read a still scene as no change, or every frame would confirm the
 *     moment it was asked about and the handshake would be decoration.
 */
import assert from "node:assert/strict";
import { frameSignature, signatureChange } from "../www/blinkymap/coded.js";

let passed = 0;
function test(name, fn) { fn(); passed++; console.log(`  ok  ${name}`); }

const W = 160, H = 90;

/** A frame: `lit` maps point index -> [r,g,b]; everything else is near-black. */
function frame(lit = {}) {
  const data = new Uint8ClampedArray(W * H * 4);
  for (let p = 0; p < W * H; p++) {
    const [r, g, b] = lit[p] || [4, 4, 6];   // a dark room is never pure black
    data[p * 4] = r; data[p * 4 + 1] = g; data[p * 4 + 2] = b;
    data[p * 4 + 3] = 255;
  }
  return { width: W, height: H, data };
}

const RED = [255, 20, 20], GREEN = [20, 255, 20], BLUE = [20, 20, 255];

/** `n` lit points, all the same colour, at a fixed spread of positions. */
function litPoints(n, colour, offset = 0) {
  const lit = {};
  for (let i = 0; i < n; i++) lit[(i * 37 + offset) % (W * H)] = colour;
  return lit;
}

test("a still scene reads as no change at all", () => {
  const a = frameSignature(frame(litPoints(200, RED)));
  const b = frameSignature(frame(litPoints(200, RED)));
  assert.equal(signatureChange(a, b), 0);
});

test("a pattern swap reads as a total change", () => {
  const a = frameSignature(frame(litPoints(200, RED)));
  const b = frameSignature(frame(litPoints(200, GREEN)));
  assert.equal(signatureChange(a, b), 1);
});

test("a change is measured against the lit region, not the image", () => {
  // 200 lit points out of 14,400 is 1.4% of the frame. Normalised by area, a
  // full pattern swap would read as 1.4% "change" and no threshold could tell
  // it from sensor noise — the whole test of "did the string change" would be
  // unusable at the scale the prop actually occupies in the picture.
  const a = frameSignature(frame(litPoints(200, RED)));
  const b = frameSignature(frame(litPoints(200, BLUE)));
  assert.ok(a.litFrac < 0.02, `lit fraction ${a.litFrac}`);
  assert.equal(signatureChange(a, b), 1);
});

test("a partial swap scales with how much of the string changed", () => {
  const lit = litPoints(200, RED);
  const half = { ...lit };
  Object.keys(half).slice(0, 100).forEach(k => { half[k] = GREEN; });
  const change = signatureChange(frameSignature(frame(lit)),
                                 frameSignature(frame(half)));
  assert.ok(change > 0.4 && change < 0.6, `half a string swapped read as ${change}`);
});

test("two dark frames do not manufacture a change", () => {
  // The floor matters here: with nothing lit, dividing by the lit population
  // would turn a couple of noisy points into a 100% change and the dark
  // reference would confirm on the first poll every time.
  const a = frameSignature(frame());
  const b = frameSignature(frame({ 5: [90, 30, 30] }));
  assert.equal(a.litFrac, 0);
  assert.ok(signatureChange(a, b) < 0.05, "a single stray point is not a change");
});

test("dim and neutral regions are never counted as lit", () => {
  // White is the aim light, and a washed-out LED at close range. It has no
  // dominant channel, so it cannot carry a code and must not read as lit.
  const grey = {}, white = {};
  for (let i = 0; i < 300; i++) {
    grey[i * 11] = [30, 30, 30];
    white[i * 11 + 1] = [250, 250, 250];
  }
  assert.equal(frameSignature(frame(grey)).decided, 0);
  assert.equal(frameSignature(frame(white)).decided, 0);
});

test("the lit fraction is what tells a dark reference from a lit one", () => {
  const dark = frameSignature(frame(litPoints(20, RED)));
  const lit  = frameSignature(frame(litPoints(6000, RED)));
  assert.ok(dark.litFrac < 0.25, `dark reference read ${dark.litFrac} lit`);
  assert.ok(lit.litFrac > 0.25, `lit string read ${lit.litFrac} lit`);
});

test("signatures of different sizes are treated as a total change", () => {
  const a = frameSignature(frame(litPoints(200, RED)));
  const b = frameSignature({ width: 8, height: 8, data: new Uint8ClampedArray(8 * 8 * 4) });
  assert.equal(signatureChange(a, b), 1);
});

console.log(`\n${passed} checks passed.`);
