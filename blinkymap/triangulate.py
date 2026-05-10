"""
Camera calibration and 3D triangulation.

Two calibration modes:
  - SimpleStereo: user provides baseline distance + approximate FOV.
                  Good enough for most holiday-light use cases.
  - CheckerboardStereo: full OpenCV stereo calibration from captured images.
                        Accurate, requires printed checkerboard.

Triangulation uses OpenCV's DLT (triangulatePoints).
With 3+ cameras the result is averaged across all camera pairs.
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
    """Intrinsic + extrinsic parameters for one camera."""
    cam_id: str
    K: np.ndarray          # 3×3 intrinsic matrix
    dist: np.ndarray       # distortion coefficients (1×5)
    R: np.ndarray          # 3×3 rotation  (world → camera)
    t: np.ndarray          # 3×1 translation

    def projection_matrix(self) -> np.ndarray:
        """Return 3×4 projection matrix P = K @ [R | t]."""
        Rt = np.hstack([self.R, self.t.reshape(3, 1)])
        return self.K @ Rt

    def undistort_point(self, pt: Tuple[float, float]) -> Tuple[float, float]:
        """Undistort a single image point."""
        pts = np.array([[[pt[0], pt[1]]]], dtype=np.float32)
        ud = cv2.undistortPoints(pts, self.K, self.dist, P=self.K)
        return float(ud[0, 0, 0]), float(ud[0, 0, 1])


def _make_K(width: int, height: int, hfov_deg: float) -> np.ndarray:
    """Build intrinsic matrix K from image size + horizontal FOV."""
    hfov_rad = math.radians(hfov_deg)
    fx = (width / 2.0) / math.tan(hfov_rad / 2.0)
    fy = fx  # assume square pixels
    cx, cy = width / 2.0, height / 2.0
    return np.array([[fx, 0, cx],
                     [0, fy, cy],
                     [0,  0,  1]], dtype=np.float64)


# ── Simple stereo setup ───────────────────────────────────────────────────────

@dataclass
class SimpleStereoConfig:
    """
    Minimal stereo setup: two cameras on a horizontal baseline.

    Assumptions:
      - Both cameras face the same direction (Z-axis, toward the tree).
      - Camera 1 is at the world origin.
      - Camera 2 is displaced `baseline_m` metres to the right (+X).
      - Both are at the same height.
      - Optionally a vertical offset `cam2_height_offset_m` if cameras differ in height.
    """
    cam1_id: str
    cam2_id: str
    cam1_width: int
    cam1_height: int
    cam2_width: int
    cam2_height: int
    baseline_m: float = 1.5          # horizontal distance between cameras
    hfov_deg: float = 70.0           # horizontal field of view (both cams same)
    cam2_height_offset_m: float = 0.0


def build_simple_stereo(cfg: SimpleStereoConfig) -> Dict[str, CameraCalib]:
    """Return calibration dicts for both cameras from basic measurements."""
    K1 = _make_K(cfg.cam1_width, cfg.cam1_height, cfg.hfov_deg)
    K2 = _make_K(cfg.cam2_width, cfg.cam2_height, cfg.hfov_deg)
    dist = np.zeros(5, dtype=np.float64)
    I3 = np.eye(3, dtype=np.float64)

    # Camera 1: at origin, looking forward (-Z in OpenCV convention means into scene)
    # We use the convention where the scene is at positive Z, camera looks toward +Z.
    calib1 = CameraCalib(
        cam_id=cfg.cam1_id, K=K1, dist=dist,
        R=I3.copy(),
        t=np.zeros(3, dtype=np.float64)
    )

    # Camera 2: displaced along +X axis (and optionally +Y)
    t2 = np.array([-cfg.baseline_m, -cfg.cam2_height_offset_m, 0.0],
                  dtype=np.float64)
    calib2 = CameraCalib(
        cam_id=cfg.cam2_id, K=K2, dist=dist,
        R=I3.copy(),
        t=t2
    )

    return {cfg.cam1_id: calib1, cfg.cam2_id: calib2}


# ── Checkerboard stereo calibration ──────────────────────────────────────────

@dataclass
class CheckerboardConfig:
    rows: int = 9          # inner corners (rows)
    cols: int = 6          # inner corners (cols)
    square_size_m: float = 0.025  # 25 mm squares


def calibrate_camera_intrinsics(images: List[np.ndarray],
                                 cfg: CheckerboardConfig) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute K and distortion coefficients from checkerboard images.
    Returns (K, dist).
    """
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
        raise ValueError(f"Only {len(obj_pts)} valid checkerboard images; need ≥4")

    h, w = images[0].shape[:2]
    _, K, dist, _, _ = cv2.calibrateCamera(obj_pts, img_pts, (w, h), None, None)
    return K, dist


def calibrate_stereo_pair(cam1_images: List[np.ndarray],
                           cam2_images: List[np.ndarray],
                           cfg: CheckerboardConfig,
                           cam1_id: str = "cam1",
                           cam2_id: str = "cam2") -> Dict[str, CameraCalib]:
    """
    Full stereo calibration from matched checkerboard image pairs.
    cam1_images[i] and cam2_images[i] must be captured simultaneously.
    """
    pattern = (cfg.cols, cfg.rows)
    objp = np.zeros((cfg.rows * cfg.cols, 3), np.float32)
    objp[:, :2] = np.mgrid[0:cfg.cols, 0:cfg.rows].T.reshape(-1, 2)
    objp *= cfg.square_size_m

    obj_pts, pts1, pts2 = [], [], []
    for img1, img2 in zip(cam1_images, cam2_images):
        g1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY) if img1.ndim == 3 else img1
        g2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY) if img2.ndim == 3 else img2
        ok1, c1 = cv2.findChessboardCorners(g1, pattern, None)
        ok2, c2 = cv2.findChessboardCorners(g2, pattern, None)
        if ok1 and ok2:
            cv2.cornerSubPix(g1, c1, (11, 11), (-1, -1),
                             (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001))
            cv2.cornerSubPix(g2, c2, (11, 11), (-1, -1),
                             (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001))
            obj_pts.append(objp)
            pts1.append(c1)
            pts2.append(c2)

    if len(obj_pts) < 4:
        raise ValueError(f"Only {len(obj_pts)} matched pairs; need ≥4")

    h1, w1 = cam1_images[0].shape[:2]
    h2, w2 = cam2_images[0].shape[:2]
    _, K1, d1, _, _ = cv2.calibrateCamera(obj_pts, pts1, (w1, h1), None, None)
    _, K2, d2, _, _ = cv2.calibrateCamera(obj_pts, pts2, (w2, h2), None, None)

    _, K1, d1, K2, d2, R, T, _, _ = cv2.stereoCalibrate(
        obj_pts, pts1, pts2, K1, d1, K2, d2, (w1, h1),
        flags=cv2.CALIB_FIX_INTRINSIC
    )

    dist_zero = np.zeros(5, dtype=np.float64)
    I3 = np.eye(3, dtype=np.float64)
    calib1 = CameraCalib(cam_id=cam1_id, K=K1, dist=d1, R=I3, t=np.zeros(3))
    calib2 = CameraCalib(cam_id=cam2_id, K=K2, dist=d2, R=R,
                          t=T.flatten())
    return {cam1_id: calib1, cam2_id: calib2}


# ── Triangulation ─────────────────────────────────────────────────────────────

def _triangulate_pair(P1: np.ndarray, pt1: Tuple[float, float],
                       P2: np.ndarray, pt2: Tuple[float, float]) -> np.ndarray:
    """DLT triangulation for one point pair. Returns (x, y, z)."""
    p1 = np.array([[pt1[0]], [pt1[1]]], dtype=np.float64)
    p2 = np.array([[pt2[0]], [pt2[1]]], dtype=np.float64)
    pts4d = cv2.triangulatePoints(P1, P2, p1, p2)
    w = pts4d[3, 0]
    if abs(w) < 1e-10:
        return np.array([np.nan, np.nan, np.nan])
    return (pts4d[:3, 0] / w).astype(np.float64)


def triangulate_capture(capture: PixelCapture,
                         calibrations: Dict[str, CameraCalib]) -> Optional[np.ndarray]:
    """
    Reconstruct 3D position from one PixelCapture.

    Uses all available camera pairs and averages valid results.
    Returns (x, y, z) numpy array or None if fewer than 2 cameras detected.
    """
    cam_ids = [cid for cid in capture.detections if cid in calibrations]
    if len(cam_ids) < 2:
        return None

    # Undistort all points first
    undist: Dict[str, Tuple[float, float]] = {}
    projs: Dict[str, np.ndarray] = {}
    for cid in cam_ids:
        calib = calibrations[cid]
        pt = capture.detections[cid]
        undist[cid] = calib.undistort_point(pt)
        projs[cid] = calib.projection_matrix()

    # Average over all camera pairs (n*(n-1)/2 combinations)
    results = []
    for i in range(len(cam_ids)):
        for j in range(i + 1, len(cam_ids)):
            a, b = cam_ids[i], cam_ids[j]
            pt3d = _triangulate_pair(projs[a], undist[a], projs[b], undist[b])
            if not np.any(np.isnan(pt3d)):
                results.append(pt3d)

    if not results:
        return None

    return np.mean(results, axis=0)


def triangulate_all(captures: List[PixelCapture],
                    calibrations: Dict[str, CameraCalib]) -> Dict[int, np.ndarray]:
    """
    Triangulate every pixel.  Returns {pixel_index: (x, y, z)}.
    Pixels with fewer than 2 camera observations are skipped.
    """
    positions: Dict[int, np.ndarray] = {}
    for cap in captures:
        pt = triangulate_capture(cap, calibrations)
        if pt is not None:
            positions[cap.pixel_index] = pt
    log.info("Triangulated %d / %d pixels", len(positions), len(captures))
    return positions


# ── Post-processing ───────────────────────────────────────────────────────────

def remove_outliers(positions: Dict[int, np.ndarray],
                    sigma: float = 3.0) -> Dict[int, np.ndarray]:
    """Remove points more than `sigma` standard deviations from centroid."""
    if len(positions) < 4:
        return positions
    pts = np.array(list(positions.values()))
    centroid = pts.mean(axis=0)
    dists = np.linalg.norm(pts - centroid, axis=1)
    threshold = dists.mean() + sigma * dists.std()
    return {idx: pt for idx, pt in positions.items()
            if np.linalg.norm(pt - centroid) <= threshold}


def normalize_positions(positions: Dict[int, np.ndarray]) -> Dict[int, np.ndarray]:
    """
    Translate so the bottom of the tree is at Y=0 and centre the X/Z axes
    over the tree's footprint centroid.
    """
    if not positions:
        return positions
    pts = np.array(list(positions.values()))
    min_y = pts[:, 1].min()
    cx = (pts[:, 0].max() + pts[:, 0].min()) / 2
    cz = (pts[:, 2].max() + pts[:, 2].min()) / 2
    offset = np.array([cx, min_y, cz])
    return {idx: pt - offset for idx, pt in positions.items()}
