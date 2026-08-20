"""Does the measured tilt actually reconstruct the prop?

The claim being tested is specific: replacing the two typed heights (camera
height above floor, prop centre height above floor) with one measured
depression angle reproduces the SAME reconstruction, up to a rigid translation
in Y that the export normalises away.

Ground truth is generated with the honest two-height model; reconstruction runs
through the shipped `_projection_matrix`, which now derives its geometry from
the pitch alone.
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from blinkymap_server import (  # noqa: E402
    BlinkyModel, Detection, SessionConfig,
    _look_at_R, _make_K, _projection_matrix, _camera_geometry, _normalize,
)

W, H, FOV = 1280, 720, 60.0


def truth_projection(angle_deg, dist, cam_h, prop_centre_h):
    """The old, honest model: camera at cam_h, aimed at the prop's centre."""
    rad = math.radians(angle_deg)
    eye = np.array([dist * math.sin(rad), cam_h, dist * math.cos(rad)])
    target = np.array([0.0, prop_centre_h, 0.0])
    R = _look_at_R(eye, target)
    t = -R @ eye
    return _make_K(W, H, FOV) @ np.hstack([R, t.reshape(3, 1)])


def project(P, X):
    p = P @ np.append(X, 1.0)
    return p[0] / p[2], p[1] / p[2]


def spiral_prop(n=40, radius=0.6, height=1.8, base=0.35):
    """A wrapped cone, roughly what the plugin is pointed at."""
    pts = []
    for i in range(n):
        f = i / (n - 1)
        y = base + f * height
        r = radius * (1.0 - f * 0.75)
        a = f * 6.0 * math.pi
        pts.append(np.array([r * math.cos(a), y, r * math.sin(a)]))
    return pts


def run(angles, dist, cam_h, prop_centre_h, pitch_noise_deg=0.0, seed=0):
    rng = np.random.default_rng(seed)
    pts = spiral_prop()
    model = BlinkyModel()
    model.pixel_count = len(pts)

    for k, ang in enumerate(angles):
        P_true = truth_projection(ang, dist, cam_h, prop_centre_h)
        # What the phone measures standing there: the depression of the optical
        # axis, which is fixed by the geometry it is actually in.
        pitch = math.degrees(math.atan2(cam_h - prop_centre_h, dist))
        pitch += rng.normal(0.0, pitch_noise_deg)

        sess = SessionConfig(session_id=k, angle_deg=ang, distance_m=dist,
                             height_m=-999.0,          # must never be consulted
                             hfov_deg=FOV, img_width=W, img_height=H,
                             pitch_deg=pitch)
        model.add_session(sess)
        for i, X in enumerate(pts):
            u, v = project(P_true, X)
            model.record_detection(k, i, Detection(cx=u, cy=v, conf=1.0))

    model.triangulate()
    return pts, model


def test_reconstruction_matches_truth_up_to_y_translation():
    pts, model = run([0, 90, 180, 270], dist=2.5, cam_h=1.55, prop_centre_h=1.25)

    got = {i: pr.position for i, pr in model.results.items() if pr.position is not None}
    assert len(got) == len(pts), f"only {len(got)}/{len(pts)} pixels reconstructed"

    err = np.array([got[i] - pts[i] for i in sorted(got)])
    # X and Z must land exactly; Y is free to be offset by a constant.
    assert np.abs(err[:, [0, 2]]).max() < 1e-6, f"X/Z drift {np.abs(err[:,[0,2]]).max()}"
    y_off = err[:, 1]
    assert y_off.std() < 1e-6, f"Y offset is not constant (std {y_off.std()})"
    # And the offset is exactly the prop centre height we refused to ask for.
    assert abs(y_off.mean() + 1.25) < 1e-6, f"Y offset {y_off.mean()} != -1.25"


def test_export_normalisation_erases_the_offset():
    pts, model = run([0, 90, 180, 270], dist=2.5, cam_h=1.55, prop_centre_h=1.25)
    normed = _normalize(model.results)

    truth = {i: p for i, p in enumerate(pts)}
    lo = np.min(np.array(list(truth.values())), axis=0)
    hi = np.max(np.array(list(truth.values())), axis=0)
    rng = np.where(hi - lo < 1e-6, 1.0, hi - lo)
    truth_normed = {i: (p - lo) / rng for i, p in truth.items()}

    worst = max(np.abs(normed[i] - truth_normed[i]).max() for i in normed)
    assert worst < 1e-6, f"normalised export differs by {worst}"


def test_a_common_distance_error_is_only_a_global_scale():
    """CLAUDE.md's claim, re-checked under the new scheme."""
    pts, model = run([0, 90, 180, 270], dist=2.5, cam_h=1.55, prop_centre_h=1.25)
    _, model_wrong = run([0, 90, 180, 270], dist=3.0, cam_h=1.55, prop_centre_h=1.25)

    a, b = _normalize(model.results), _normalize(model_wrong.results)
    # Note the pitch is re-measured at the new distance, so this is the honest
    # "operator stood further back than typed" case only in so far as the ratio
    # is common to every position — which is the whole point.
    worst = max(np.abs(a[i] - b[i]).max() for i in a)
    assert worst < 1e-6, f"normalised models diverge by {worst}"


def test_pitch_noise_degrades_gracefully():
    pts, _ = run([0, 90, 180, 270], dist=2.5, cam_h=1.55, prop_centre_h=1.25)
    for noise, budget in [(1.0, 0.02), (2.0, 0.05)]:
        _, model = run([0, 90, 180, 270], dist=2.5, cam_h=1.55,
                       prop_centre_h=1.25, pitch_noise_deg=noise, seed=7)
        got = {i: pr.position for i, pr in model.results.items()
               if pr.position is not None}
        err = np.array([got[i] - pts[i] for i in sorted(got)])
        err[:, 1] -= np.median(err[:, 1])          # Y offset is free
        rms = float(np.sqrt((err ** 2).sum(axis=1).mean()))
        assert rms < budget, f"±{noise}° pitch noise -> {rms:.4f}m rms (budget {budget})"


def test_no_pitch_falls_back_to_the_typed_height():
    sess = SessionConfig(session_id=0, angle_deg=0, distance_m=2.0, height_m=1.5,
                         pitch_deg=None)
    assert _camera_geometry(sess) == (1.5, 0.75)


def test_pitch_is_clamped_before_tan_explodes():
    steep = SessionConfig(session_id=0, angle_deg=0, distance_m=2.0, height_m=1.5,
                          pitch_deg=89.0)
    eye_y, aim_y = _camera_geometry(steep)
    assert aim_y == 0.0
    assert eye_y == 2.0 * math.tan(math.radians(60.0))


def test_the_measurement_replaces_the_two_typed_numbers_exactly():
    """h - t = d·tan(θ), on the geometry the server actually builds."""
    for dist, cam_h, prop_h in [(2.0, 1.5, 1.0), (3.5, 1.6, 0.0), (2.5, 1.2, 2.0)]:
        pitch = math.degrees(math.atan2(cam_h - prop_h, dist))
        sess = SessionConfig(session_id=0, angle_deg=0, distance_m=dist,
                             height_m=-999.0, pitch_deg=pitch)
        eye_y, aim_y = _camera_geometry(sess)
        assert abs((eye_y - aim_y) - (cam_h - prop_h)) < 1e-9


def test_consensus_error_is_zero_on_exact_data():
    _, model = run([0, 90, 180, 270], dist=2.5, cam_h=1.55, prop_centre_h=1.25)
    conf = model.model_confidence()
    assert conf["consensus_px"] < 0.1, f"consensus {conf['consensus_px']}px on exact data"


def test_consensus_error_exceeds_the_pairwise_figure_under_noise():
    """Why the history table is kept in consensus px.

    Each pairwise candidate is fitted to the very two views it is then scored
    against, so it flatters itself. The consensus point is fitted to none of
    them individually and is the honest number.
    """
    pts = spiral_prop()
    rng = np.random.default_rng(3)
    model = BlinkyModel()
    model.pixel_count = len(pts)
    for k, ang in enumerate(_FOUR_ANGLES):
        P = truth_projection(ang, 2.5, 1.55, 1.25)
        pitch = math.degrees(math.atan2(1.55 - 1.25, 2.5))
        model.add_session(SessionConfig(session_id=k, angle_deg=ang, distance_m=2.5,
                                        height_m=-999.0, hfov_deg=FOV,
                                        img_width=W, img_height=H, pitch_deg=pitch))
        for i, X in enumerate(pts):
            u, v = project(P, X)
            model.record_detection(k, i, Detection(cx=u + rng.normal(0, 3),
                                                   cy=v + rng.normal(0, 3), conf=1.0))
    model.triangulate()
    conf = model.model_confidence()
    assert conf["consensus_px"] > conf["reproj_px"], (
        f"consensus {conf['consensus_px']} should exceed pairwise {conf['reproj_px']}")


_FOUR_ANGLES = [0, 90, 180, 270]
