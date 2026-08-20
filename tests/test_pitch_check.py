"""Wire-length sanity check: pixel pitch as an upper bound on neighbour distance.

Run: python3 tests/test_pitch_check.py

The point of these is not the arithmetic — it is pinning the ASYMMETRY. Pitch
bounds the chord from above only, so a model that comes out too big is provably
wrong while one that comes out too small is merely unproven. Several tests below
assert that we stay silent in the second case; if a future change makes them
"pass" by flagging those, the check has started lying.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import blinkymap_server as B  # noqa: E402

PIXEL_PITCH = 0.1  # 100mm
FAILS = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label:38} {got!r}")
    if not ok:
        FAILS.append(f"{label}: got {got!r}, want {want!r}")


def model(positions, pixel_pitch=PIXEL_PITCH, breaks=()):
    m = B.BlinkyModel()
    m.pixel_count = len(positions)
    m.pixel_pitch_m = pixel_pitch
    m.string_breaks = breaks
    m.sessions = {s: (B.SessionConfig(s, s * 90, 2, 1.5), {}) for s in (1, 2)}
    for i, p in enumerate(positions):
        pr = B.PixelResult(index=i, confidence=0.9, sessions_detected=[1, 2])
        if p is not None:
            pr.position = np.array(p, dtype=float)
        m.results[i] = pr
    return m


def line(n, gap):
    return [(0, i * gap, 0) for i in range(n)]


print("\n_parse_breaks — 1-based in, 0-based out, junk dropped")
check("text", B._parse_breaks("13, 25 ; x, 0, 1, 99", 24), (12,))
check("list", B._parse_breaks([13, 25], 24), (12,))
check("empty", B._parse_breaks("", 24), ())
check("none", B._parse_breaks(None, 24), ())

print("\nchord stats")
check("pitch unset disables it", B._neighbour_chords(model(line(24, .1)).results, 24, 0.0), None)
check("taut string reads exactly pitch",
      B._neighbour_chords(model(line(24, .1)).results, 24, PIXEL_PITCH)["median_mm"], 100.0)
check("taut string flags nothing impossible",
      B._neighbour_chords(model(line(24, .1)).results, 24, PIXEL_PITCH)["over_pitch"], 0.0)
check("all pixels at one point -> no bound",
      B._neighbour_chords(model([(0, 0, 0)] * 24).results, 24, PIXEL_PITCH)["max_scale"], None)
check("unseen pixels are skipped, not spanned",
      B._neighbour_chords(model(line(24, .1)[:5] + [None] + line(24, .1)[6:]).results,
                          24, PIXEL_PITCH)["pairs"], 21)

print("\nlimiting factor")
check("taut", model(line(24, .1)).model_confidence()["limiting"], "none")
check("inflated 2x", model(line(24, .2)).model_confidence()["limiting"], "scale")
# The one-sided half: a slack string is legitimate and must never be flagged.
check("slack string stays silent", model(line(24, .05)).model_confidence()["limiting"], "none")

flyers = line(24, .1)
flyers[5], flyers[17] = (3., .5, 0.), (-3., 1.7, 0.)
check("scattered flyers != scale error",
      model(flyers).model_confidence()["limiting"], "impossible")
check("...and the median survives them",
      model(flyers).model_confidence()["chords"]["median_mm"], 100.0)

# A string joined to a second one: the jump across the join is not a real pair.
two = line(12, .1) + [(5, i * .1, 0) for i in range(12)]
check("unmarked join looks impossible",
      model(two).model_confidence()["limiting"], "impossible")
check("marked join is skipped",
      model(two, breaks=B._parse_breaks("13", 24)).model_confidence()["limiting"], "none")

# Too short for the statistical claim that the whole model is mis-scaled, but
# the per-pair geometry still holds: these gaps are impossible either way.
check("short string: no scale verdict",
      model(line(5, .2)).model_confidence()["limiting"], "impossible")
check("...same string, long enough to judge",
      model(line(24, .2)).model_confidence()["limiting"], "scale")

print("\nend-to-end through the real projection + triangulation code")
# Taut helix: lateral step held inside pitch so the prop obeys its own bound.
N, R, DTH = 24, 0.35, 0.2
pts, ang, y = [], 0.0, 0.4
for _ in range(N):
    pts.append(np.array([R * np.sin(ang), y, R * np.cos(ang)]))
    lat = 2 * R * np.sin(DTH / 2)
    ang += DTH
    y += np.sqrt(PIXEL_PITCH ** 2 - lat ** 2)
pts = np.array(pts)
assert abs(np.linalg.norm(pts[1] - pts[0]) - PIXEL_PITCH) < 1e-9, "fixture must be taut"


def reconstruct(true_fov, assumed_fov, d_typed, d_true):
    m = B.BlinkyModel()
    m.pixel_count = N
    m.pixel_pitch_m = PIXEL_PITCH
    for sid, a in enumerate([0, 75, 150, 225], start=1):
        m.add_session(B.SessionConfig(sid, a, d_typed, 1.5, hfov_deg=assumed_fov,
                                      device_pitch_deg=15.0))
        P = B._projection_matrix(B.SessionConfig(sid, a, d_true, 1.5, hfov_deg=true_fov,
                                                 device_pitch_deg=15.0))
        for i, X in enumerate(pts):
            p = P @ np.append(X, 1.0)
            if p[2] > 0:
                m.record_detection(sid, i, B.Detection(p[0] / p[2], p[1] / p[2], 0.9))
    m.triangulate()
    return m.model_confidence()


check("clean scan", reconstruct(60, 60, 2.0, 2.0)["limiting"], "none")
check("typed 3m, stood at 2m", reconstruct(60, 60, 3.0, 2.0)["limiting"], "scale")
# FOV understated -> model inflated. Reprojection barely moves here (~8px) while
# a third of the string goes impossible: this is the case pitch catches and
# reprojection error does not.
check("true FOV 45, assumed 60", reconstruct(45, 60, 2.0, 2.0)["limiting"], "scale")
check("true FOV 50, assumed 60", reconstruct(50, 60, 2.0, 2.0)["limiting"], "scale")
# The blind half, stated as an expectation rather than a bug: these overstate
# FOV / distance, shrinking the model, and pitch cannot see it. Assert only that
# the pitch check stays SILENT — other terms may still object (an overstated FOV
# does raise reprojection error), and pinning the whole verdict here would make
# this test fail for reasons that have nothing to do with wire length.
PITCH_VERDICTS = ("scale", "impossible")
check("true FOV 75 invisible (shrinks)",
      reconstruct(75, 60, 2.0, 2.0)["limiting"] in PITCH_VERDICTS, False)
check("typed 2m, stood at 3m invisible",
      reconstruct(60, 60, 2.0, 3.0)["limiting"] in PITCH_VERDICTS, False)

print(f"\n{'FAILED: ' + '; '.join(FAILS) if FAILS else 'All checks passed.'}")
sys.exit(1 if FAILS else 0)
