/**
 * Node test for tilt.js. Run: node tests/test_tilt.mjs
 *
 * The closed form is checked against the full W3C rotation matrix rather than
 * against itself, so an algebra slip cannot pass by agreeing with a rewritten
 * copy of the same mistake.
 */
import assert from "node:assert/strict";
import { Tilt, heightAboveAim, MAX_PITCH_DEG } from "../www/blinkymap/tilt.js";

const d2r = (d) => (d * Math.PI) / 180;
const r2d = (r) => (r * 180) / Math.PI;
let passed = 0;
function test(name, fn) { fn(); passed++; console.log(`  ok  ${name}`); }

/** Full device→world matrix R = Rz(a)·Rx(b)·Ry(g); world Z is up. */
function fullMatrixDepression(a, b, g) {
  const [A, B, G] = [d2r(a), d2r(b), d2r(g)];
  const cA = Math.cos(A), sA = Math.sin(A);
  const cB = Math.cos(B), sB = Math.sin(B);
  const cG = Math.cos(G), sG = Math.sin(G);
  const R = [
    [cA * cG - sA * sB * sG, -sA * cB, cA * sG + sA * sB * cG],
    [sA * cG + cA * sB * sG,  cA * cB, sA * sG - cA * sB * cG],
    [-cB * sG,                sB,      cB * cG],
  ];
  // Rear camera looks along device -Z; take the world-up component.
  const up = -R[2][2];
  return r2d(Math.asin(-up));
}

/** Drive a Tilt instance the way the browser would. */
function feed(t, beta, gamma) { t._onEvent({ beta, gamma }); return t.pitch; }

test("closed form matches the full rotation matrix everywhere", () => {
  const t = new Tilt();
  let worst = 0;
  for (let b = -180; b < 180; b += 7) {
    for (let g = -90; g < 90; g += 5) {
      worst = Math.max(worst, Math.abs(feed(t, b, g) - fullMatrixDepression(0, b, g)));
    }
  }
  assert.ok(worst < 1e-9, `worst disagreement ${worst}`);
});

test("measurement is independent of alpha (no magnetometer dependence)", () => {
  for (const [b, g] of [[60, 0], [30, -40], [90, 12]]) {
    const ref = fullMatrixDepression(0, b, g);
    for (const a of [0, 45, 137, 280, 359]) {
      assert.ok(Math.abs(fullMatrixDepression(a, b, g) - ref) < 1e-9);
    }
  }
});

test("named poses", () => {
  const t = new Tilt();
  const near = (got, want) => assert.ok(Math.abs(got - want) < 1e-6, `${got} != ${want}`);
  near(feed(t, 90, 0), 0);      // portrait, aimed at the horizon
  near(feed(t, 0, 0), 90);      // flat on a table, screen up -> straight down
  near(feed(t, 0, -90), 0);     // landscape, aimed at the horizon
  near(feed(t, 0, 90), 0);      // the other landscape
  near(feed(t, 60, 0), 30);     // tipped 30 deg down
  near(feed(t, 105, 0), -15);   // aimed 15 deg UP
});

test("portrait and landscape agree for the same physical aim", () => {
  const t = new Tilt();
  // Same 30 deg of depression, reached two ways.
  assert.ok(Math.abs(feed(t, 60, 0) - feed(t, 0, -60)) < 1e-9);
});

test("garbage events are ignored rather than poisoning the reading", () => {
  const t = new Tilt();
  feed(t, 60, 0);
  const good = t.pitch;
  t._onEvent({ beta: null, gamma: 0 });
  t._onEvent({ beta: 60, gamma: NaN });
  t._onEvent({});
  assert.equal(t.pitch, good);
});

test("h - t = d·tan(theta), which is the whole point", () => {
  for (const [dist, camH, propH] of [[2.0, 1.5, 1.0], [3.5, 1.6, 0.0], [2.5, 1.2, 2.0]]) {
    const pitch = r2d(Math.atan2(camH - propH, dist));
    assert.ok(Math.abs(heightAboveAim(dist, pitch) - (camH - propH)) < 1e-9);
  }
});

test("steep aims are clamped, not allowed to blow up", () => {
  assert.ok(Tilt.tooSteep(61));
  assert.ok(Tilt.tooSteep(-61));
  assert.ok(!Tilt.tooSteep(59));
  const clamped = 2.0 * Math.tan(d2r(MAX_PITCH_DEG));
  assert.ok(Math.abs(heightAboveAim(2.0, 89) - clamped) < 1e-9);
  assert.ok(Math.abs(heightAboveAim(2.0, -89) + clamped) < 1e-9);
});

test("sampling reports peak-to-peak swing, not an average that hides it", () => {
  const t = new Tilt();
  feed(t, 90, 0);
  t.beginSample();
  for (const b of [90, 88, 92, 86]) feed(t, b, 0);   // 0, +2, -2, +4 deg
  const s = t.endSample();
  assert.equal(s.n, 5);                    // the pre-sample seed plus four
  // Peak-to-peak spans -2 to +4, so 6 — not the 4 deg of the largest single
  // excursion. The wider figure is the honest one: the frames at -2 and the
  // frames at +4 are both attributed to the same recorded pose.
  assert.ok(Math.abs(s.spread - 6) < 1e-6, `spread ${s.spread}`);
  assert.ok(s.max > s.mean && s.mean > s.min);
  assert.equal(t.endSample(), null);       // window is closed
});

test("a steady hand reports no spread", () => {
  const t = new Tilt();
  feed(t, 75, 0);
  t.beginSample();
  for (let i = 0; i < 20; i++) feed(t, 75, 0);
  assert.equal(t.endSample().spread, 0);
});

console.log(`\n${passed} passed`);
