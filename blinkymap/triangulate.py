"""
Camera calibration and 3D triangulation — session-based single-camera workflow.

Workflow:
  1. Run a scan from position A  → ScanSession(angle=0,   dist=2m, height=1.2m)
  2. Run a scan from position B  → ScanSession(angle=120°, dist=2m, height=1.2m)
     After step 2: triangulate sessions A+B → partial model with confidence scores.
  3. Optional: run more sessions.
     Sessions 3+: auto-locate camera via PnP from already-known pixels — no
     manual position entry needed.
  4. Keep adding sessions until overall confidence is acceptable.

Camera position is specified in polar coordinates centred on the tree:
  angle_deg  = 0° is "front of tree", increases clockwise when viewed from above
  distance_m = horizontal distance from tree centre
  height_m   = camera lens height above ground
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from .capture import PixelCapture

log = logging.getLogger(__name__)

Vec3 = Tuple[float, float, float]


# ── Camera calibration data ───────────────────────────────────────────────────

@dataclass
class CameraCalib:
    """Intrinsic + extrinsic parameters for one camera position."""
    cam_id: str
    K: np.ndarray          # 3×3 intrinsic matrix
    dist: np.ndarray       # distortion coefficients (1×5)
    R: np.ndarray          # 3×3 rotation  (world → camera)
    t: np.ndarray          # 3×1 translation (world → camera)

    def projection_matrix(self) -> np.ndarray:
        """Return 3×4 projection matrix P = K @ [R | t]."""
        Rt = np.hstack([self.R, self.t.reshape(3, 1)])
        return self.K @ Rt

    def undistort_point(self, pt: Tuple[float, float]) -> Tuple[float, float]:
        pts = np.array([[[pt[0], pt[1]]]], dtype=np.float32)
        ud = cv2.undistortPoints(pts, self.K, self.dist, P=self.K)
        return float(ud[0, 0, 0]), float(ud[0, 0, 1])

    def project_point(self, world_pt: np.ndarray) -> Tuple[float, float]:
        """Project a 3D world point onto the image plane."""
        pts, _ = cv2.projectPoints(
            world_pt.reshape(1, 3),
            cv2.Rodrigues(self.R)[0], self.t,
            self.K, self.dist
        )
        return float(pts[0, 0, 0]), float(pts[0, 0, 1])

    @property
    def world_position(self) -> np.ndarray:
        """Camera centre in world coordinates."""
        return (-self.R.T @ self.t).flatten()


def _make_K(width: int, height: int, hfov_deg: float) -> np.ndarray:
    hfov_rad = math.radians(hfov_deg)
    fx = (width / 2.0) / math.tan(hfov_rad / 2.0)
    cx, cy = width / 2.0, height / 2.0
    return np.array([[fx, 0, cx], [0, fx, cy], [0, 0, 1]], dtype=np.float64)


def _look_at_rotation(eye: np.ndarray,
                       target: np.ndarray = np.zeros(3),
                       world_up: np.ndarray = np.array([0., 1., 0.])) -> np.ndarray:
    """
    Rotation matrix R (world→camera) for a camera at `eye` looking at `target`.
    OpenCV convention: camera Z forward, Y down in image.
    """
    z = target - eye
    norm = np.linalg.norm(z)
    if norm < 1e-8:
        return np.eye(3)
    z /= norm

    x = np.cross(z, world_up)
    xn = np.linalg.norm(x)
    if xn < 1e-8:
        world_up = np.array([0., 0., 1.])
        x = np.cross(z, world_up)
        xn = np.linalg.norm(x)
    x /= xn

    y = np.cross(z, x)   # points downward in image (OpenCV Y convention)
    return np.vstack([x, y, z]).astype(np.float64)


# ── Scan session ──────────────────────────────────────────────────────────────

@dataclass
class ScanSession:
    """One camera position + the pixel captures from that position."""
    session_id: int

    # Polar position around the tree (user-supplied for sessions 1-2)
    angle_deg: float = 0.0       # 0° = front, clockwise from above
    distance_m: float = 2.0      # horizontal dist from tree centre
    height_m: float = 1.2        # camera lens height

    captures: List[PixelCapture] = field(default_factory=list)
    calib: Optional[CameraCalib] = None
    auto_located: bool = False   # True when positioned via PnP

    @property
    def detected_pixels(self) -> List[int]:
        return [c.pixel_index for c in self.captures if c.camera_count > 0]

    @property
    def label(self) -> str:
        loc = "auto" if self.auto_located else f"{self.angle_deg:.0f}°"
        return f"Session {self.session_id}  [{loc}, {self.distance_m:.1f}m]"


def build_session_calib(session: ScanSession,
                         K: np.ndarray,
                         dist: np.ndarray) -> CameraCalib:
    """
    Build CameraCalib from a session's polar position around the tree.
    Tree centre assumed at world origin.
    """
    angle_rad = math.radians(session.angle_deg)
    eye = np.array([
        session.distance_m * math.sin(angle_rad),
        session.height_m,
        session.distance_m * math.cos(angle_rad),
    ], dtype=np.float64)

    R = _look_at_rotation(eye)
    t = (-R @ eye).astype(np.float64)

    return CameraCalib(
        cam_id=f"session_{session.session_id}",
        K=K.copy(), dist=dist.copy(), R=R, t=t
    )


# ── PnP auto-location ─────────────────────────────────────────────────────────

def locate_session_pnp(session: ScanSession,
                        known_positions: Dict[int, np.ndarray],
                        K: np.ndarray,
                        dist: np.ndarray,
                        min_points: int = 6) -> Optional[CameraCalib]:
    """
    Auto-locate the camera for `session` using pixels that are already
    triangulated in `known_positions`.

    Returns a CameraCalib or None if not enough correspondences.
    """
    obj_pts, img_pts = [], []
    for cap in session.captures:
        if cap.pixel_index not in known_positions:
            continue
        if not cap.detections:
            continue
        # Use the first (and only) camera's detection for this session
        det = next(iter(cap.detections.values()))
        obj_pts.append(known_positions[cap.pixel_index].astype(np.float64))
        img_pts.append(np.array(det, dtype=np.float64))

    if len(obj_pts) < min_points:
        log.warning("PnP: only %d correspondences (need %d)", len(obj_pts), min_points)
        return None

    obj_arr = np.array(obj_pts, dtype=np.float64)
    img_arr = np.array(img_pts, dtype=np.float64)

    ok, rvec, tvec, inliers = cv2.solvePnPRansac(
        obj_arr, img_arr, K, dist,
        iterationsCount=200, reprojectionError=8.0,
        flags=cv2.SOLVEPNP_ITERATIVE
    )
    if not ok:
        log.warning("PnP RANSAC failed")
        return None

    R, _ = cv2.Rodrigues(rvec)
    n_in = len(inliers) if inliers is not None else 0
    log.info("PnP session %d: %d/%d inliers", session.session_id, n_in, len(obj_pts))

    return CameraCalib(
        cam_id=f"session_{session.session_id}_pnp",
        K=K.copy(), dist=dist.copy(),
        R=R.astype(np.float64),
        t=tvec.flatten().astype(np.float64)
    )


# ── Triangulation ─────────────────────────────────────────────────────────────

def _triangulate_pair(P1: np.ndarray, pt1: Tuple[float, float],
                       P2: np.ndarray, pt2: Tuple[float, float]) -> Optional[np.ndarray]:
    """DLT triangulation. Returns (x,y,z) or None."""
    p1 = np.array([[pt1[0]], [pt1[1]]], dtype=np.float64)
    p2 = np.array([[pt2[0]], [pt2[1]]], dtype=np.float64)
    pts4d = cv2.triangulatePoints(P1, P2, p1, p2)
    w = pts4d[3, 0]
    if abs(w) < 1e-10:
        return None
    pt = (pts4d[:3, 0] / w).astype(np.float64)
    # Reject points behind either camera (negative depth)
    cam1_z = (P1 @ np.append(pt, 1))[2]
    cam2_z = (P2 @ np.append(pt, 1))[2]
    if cam1_z < 0 or cam2_z < 0:
        return None
    return pt


def _reprojection_error(pt3d: np.ndarray,
                         sessions_with_calib: List[Tuple[CameraCalib, Tuple[float, float]]]) -> float:
    """Mean reprojection error in pixels across all session views."""
    errors = []
    for calib, detected in sessions_with_calib:
        proj, _ = cv2.projectPoints(
            pt3d.reshape(1, 3).astype(np.float32),
            cv2.Rodrigues(calib.R)[0], calib.t,
            calib.K, calib.dist
        )
        dx = proj[0, 0, 0] - detected[0]
        dy = proj[0, 0, 1] - detected[1]
        errors.append(math.sqrt(dx * dx + dy * dy))
    return float(np.mean(errors)) if errors else float("inf")


# ── Per-pixel result ──────────────────────────────────────────────────────────

@dataclass
class PixelResult:
    index: int
    position: Optional[np.ndarray] = None   # triangulated (x, y, z)
    confidence: float = 0.0                 # 0-1
    session_count: int = 0                  # how many sessions saw it
    reprojection_error: float = float("inf")
    sessions_detected: List[int] = field(default_factory=list)

    @property
    def confidence_label(self) -> str:
        if self.confidence >= 0.75:
            return "high"
        if self.confidence >= 0.40:
            return "medium"
        if self.position is not None:
            return "low"
        return "unseen"


def _angular_spread(camera_positions: List[np.ndarray],
                    pixel_position: np.ndarray) -> float:
    """
    Measure how spread out the camera viewpoints are around this pixel.
    Returns 0-1: 1 = 180° spread (ideal), 0 = all cameras in the same direction.
    """
    if len(camera_positions) < 2:
        return 0.0
    dirs = [c - pixel_position for c in camera_positions]
    dirs = [d / (np.linalg.norm(d) + 1e-9) for d in dirs]
    angles = []
    for i in range(len(dirs)):
        for j in range(i + 1, len(dirs)):
            cos_a = np.clip(np.dot(dirs[i], dirs[j]), -1, 1)
            angles.append(math.acos(cos_a))
    if not angles:
        return 0.0
    max_angle = max(angles)
    return min(max_angle / math.pi, 1.0)  # normalise to [0,1]


def _pixel_confidence(session_count: int,
                       angular_spread: float,
                       reprojection_error: float,
                       max_reproj_px: float = 20.0) -> float:
    """
    Combine detection count, geometry quality, and reprojection error
    into a single [0,1] confidence score.
    """
    # Coverage: diminishing returns after 3 sessions
    cov = min(session_count / 3.0, 1.0)
    # Geometry: angular spread of viewpoints
    geom = angular_spread
    # Accuracy: reprojection error (lower is better)
    acc = max(0.0, 1.0 - reprojection_error / max_reproj_px)
    return 0.3 * cov + 0.35 * geom + 0.35 * acc


# ── Multi-session triangulation ───────────────────────────────────────────────

def triangulate_sessions(sessions: List[ScanSession],
                          pixel_count: int) -> Dict[int, PixelResult]:
    """
    Triangulate pixel positions from all sessions that have calibration data.

    For each pixel, uses every pair of sessions that both detected it,
    averages the results, and computes a confidence score.

    Returns {pixel_index: PixelResult}.
    """
    # Only use sessions that have been calibrated
    calibrated = [(s, s.calib) for s in sessions if s.calib is not None]
    if len(calibrated) < 2:
        log.warning("Need ≥2 calibrated sessions to triangulate")
        return {}

    # Build per-pixel index: pixel → [(session, calib, detected_pt)]
    pixel_views: Dict[int, List[Tuple[ScanSession, CameraCalib, Tuple[float, float]]]] = {}
    for sess, calib in calibrated:
        for cap in sess.captures:
            if not cap.detections:
                continue
            pt = next(iter(cap.detections.values()))  # single camera per session
            pixel_views.setdefault(cap.pixel_index, []).append((sess, calib, pt))

    results: Dict[int, PixelResult] = {}

    for idx in range(pixel_count):
        views = pixel_views.get(idx, [])
        result = PixelResult(
            index=idx,
            session_count=len(views),
            sessions_detected=[s.session_id for s, _, _ in views],
        )

        if len(views) < 2:
            results[idx] = result
            continue

        # Triangulate all pairs
        tri_pts = []
        for i in range(len(views)):
            for j in range(i + 1, len(views)):
                s1, c1, pt1 = views[i]
                s2, c2, pt2 = views[j]
                ud1 = c1.undistort_point(pt1)
                ud2 = c2.undistort_point(pt2)
                pt3d = _triangulate_pair(c1.projection_matrix(), ud1,
                                         c2.projection_matrix(), ud2)
                if pt3d is not None:
                    tri_pts.append(pt3d)

        if not tri_pts:
            results[idx] = result
            continue

        # Average triangulations (robust: use median per axis)
        arr = np.array(tri_pts)
        position = np.median(arr, axis=0)

        # Reprojection error
        reproj_views = [(c, pt) for _, c, pt in views]
        reproj_err = _reprojection_error(position, reproj_views)

        # Angular spread
        cam_positions = [c.world_position for _, c, _ in views]
        spread = _angular_spread(cam_positions, position)

        confidence = _pixel_confidence(len(views), spread, reproj_err)

        result.position = position
        result.reprojection_error = reproj_err
        result.confidence = confidence
        results[idx] = result

    detected = sum(1 for r in results.values() if r.position is not None)
    log.info("Triangulated %d / %d pixels from %d sessions",
             detected, pixel_count, len(calibrated))
    return results


# ── Model-level confidence ────────────────────────────────────────────────────

@dataclass
class ModelConfidence:
    overall: float              # 0-1 overall score
    coverage: float             # fraction of pixels triangulated
    mean_confidence: float      # average per-pixel confidence (triangulated only)
    mean_reprojection_px: float # average reprojection error in pixels
    high_count: int             # confidence ≥ 0.75
    medium_count: int           # 0.40 ≤ confidence < 0.75
    low_count: int              # triangulated but confidence < 0.40
    unseen_count: int           # not triangulated

    @property
    def score_pct(self) -> int:
        return int(self.overall * 100)

    @property
    def grade(self) -> str:
        if self.overall >= 0.80:
            return "Excellent"
        if self.overall >= 0.60:
            return "Good"
        if self.overall >= 0.40:
            return "Fair — add more sessions"
        return "Poor — need more sessions"


def compute_model_confidence(results: Dict[int, PixelResult],
                              total_pixels: int) -> ModelConfidence:
    triangulated = [r for r in results.values() if r.position is not None]
    coverage = len(triangulated) / max(total_pixels, 1)

    if not triangulated:
        return ModelConfidence(0, 0, 0, float("inf"), 0, 0, 0, total_pixels)

    confs = [r.confidence for r in triangulated]
    reproj = [r.reprojection_error for r in triangulated
              if r.reprojection_error < float("inf")]

    mean_conf = float(np.mean(confs))
    mean_reproj = float(np.mean(reproj)) if reproj else 0.0

    high   = sum(1 for c in confs if c >= 0.75)
    medium = sum(1 for c in confs if 0.40 <= c < 0.75)
    low    = sum(1 for c in confs if c < 0.40)
    unseen = total_pixels - len(triangulated)

    # Overall: weight coverage and mean confidence
    overall = 0.4 * coverage + 0.6 * mean_conf
    return ModelConfidence(
        overall=overall,
        coverage=coverage,
        mean_confidence=mean_conf,
        mean_reprojection_px=mean_reproj,
        high_count=high,
        medium_count=medium,
        low_count=low,
        unseen_count=unseen,
    )


# ── Post-processing ───────────────────────────────────────────────────────────

def extract_positions(results: Dict[int, PixelResult]) -> Dict[int, np.ndarray]:
    """Extract {index: position} for triangulated pixels only."""
    return {idx: r.position for idx, r in results.items() if r.position is not None}


def normalize_positions(positions: Dict[int, np.ndarray]) -> Dict[int, np.ndarray]:
    """Translate so bottom of tree is Y=0, centre over footprint centroid."""
    if not positions:
        return positions
    pts = np.array(list(positions.values()))
    min_y = pts[:, 1].min()
    cx = (pts[:, 0].max() + pts[:, 0].min()) / 2
    cz = (pts[:, 2].max() + pts[:, 2].min()) / 2
    offset = np.array([cx, min_y, cz])
    return {idx: pt - offset for idx, pt in positions.items()}


def remove_outliers(positions: Dict[int, np.ndarray],
                    sigma: float = 3.0) -> Dict[int, np.ndarray]:
    if len(positions) < 4:
        return positions
    pts = np.array(list(positions.values()))
    centroid = pts.mean(axis=0)
    dists = np.linalg.norm(pts - centroid, axis=1)
    threshold = dists.mean() + sigma * dists.std()
    return {idx: pt for idx, pt in positions.items()
            if np.linalg.norm(pt - centroid) <= threshold}


# ── Legacy helpers (kept for compatibility) ───────────────────────────────────

def _make_K_public(width: int, height: int, hfov_deg: float) -> np.ndarray:
    return _make_K(width, height, hfov_deg)


SimpleStereoConfig = None   # replaced by ScanSession workflow


def calibrate_stereo_pair(*args, **kwargs):
    raise NotImplementedError("Use the ScanSession workflow instead")


def build_simple_stereo(*args, **kwargs):
    raise NotImplementedError("Use the ScanSession workflow instead")


# expose for checkerboard calibration (still used inside app.py)
def calibrate_camera_intrinsics(images, cfg):
    from dataclasses import dataclass as _dc
    pattern = (cfg.cols, cfg.rows)
    objp = np.zeros((cfg.rows * cfg.cols, 3), np.float32)
    objp[:, :2] = np.mgrid[0:cfg.cols, 0:cfg.rows].T.reshape(-1, 2)
    objp *= cfg.square_size_m
    obj_pts, img_pts = [], []
    for img in images:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        found, corners = cv2.findChessboardCorners(gray, pattern, None)
        if found:
            cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1),
                             (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001))
            obj_pts.append(objp)
            img_pts.append(corners)
    if len(obj_pts) < 4:
        raise ValueError(f"Only {len(obj_pts)} valid images (need ≥4)")
    h, w = images[0].shape[:2]
    _, K, dist, _, _ = cv2.calibrateCamera(obj_pts, img_pts, (w, h), None, None)
    return K, dist


@dataclass
class CheckerboardConfig:
    rows: int = 9
    cols: int = 6
    square_size_m: float = 0.025
