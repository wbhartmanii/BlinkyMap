"""
Camera management and LED detection.

Supports USB webcams (integer index) and IP cameras (RTSP/HTTP URL).
LED detection uses background-subtraction + brightest-centroid.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

log = logging.getLogger(__name__)

Point2D = Tuple[float, float]


# ── Camera ────────────────────────────────────────────────────────────────────

@dataclass
class CameraInfo:
    cam_id: str                    # unique label, e.g. "cam0" or "cam_ip_1"
    source: object                 # int index or URL string
    label: str = ""
    width: int = 0
    height: int = 0

    def is_usb(self) -> bool:
        return isinstance(self.source, int)


class Camera:
    """Wraps OpenCV VideoCapture; thread-safe frame grab."""

    def __init__(self, info: CameraInfo):
        self.info = info
        self._cap: Optional[cv2.VideoCapture] = None
        self._lock = threading.Lock()

    def open(self) -> bool:
        cap = cv2.VideoCapture(self.info.source)
        if not cap.isOpened():
            log.warning("Cannot open camera %s (%s)", self.info.cam_id, self.info.source)
            return False
        self._cap = cap
        self.info.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.info.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if not self.info.label:
            self.info.label = f"Camera {self.info.cam_id}"
        log.info("Opened %s  %dx%d", self.info.label, self.info.width, self.info.height)
        return True

    def close(self):
        with self._lock:
            if self._cap:
                self._cap.release()
                self._cap = None

    def read(self) -> Optional[np.ndarray]:
        """Return latest BGR frame or None on error."""
        if self._cap is None:
            return None
        with self._lock:
            ok, frame = self._cap.read()
        return frame if ok else None

    def read_gray(self) -> Optional[np.ndarray]:
        frame = self.read()
        if frame is None:
            return None
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    @property
    def is_open(self) -> bool:
        return self._cap is not None and self._cap.isOpened()


# ── Camera manager ────────────────────────────────────────────────────────────

class CameraManager:
    """Collection of cameras; open/close/enumerate."""

    def __init__(self):
        self._cameras: Dict[str, Camera] = {}
        self._counter = 0

    def add_usb(self, index: int, label: str = "") -> Camera:
        cam_id = f"usb_{index}"
        info = CameraInfo(cam_id=cam_id, source=index,
                          label=label or f"USB cam {index}")
        cam = Camera(info)
        self._cameras[cam_id] = cam
        return cam

    def add_ip(self, url: str, label: str = "") -> Camera:
        self._counter += 1
        cam_id = f"ip_{self._counter}"
        info = CameraInfo(cam_id=cam_id, source=url,
                          label=label or f"IP cam {self._counter}")
        cam = Camera(info)
        self._cameras[cam_id] = cam
        return cam

    def open_all(self) -> Dict[str, bool]:
        return {cid: cam.open() for cid, cam in self._cameras.items()}

    def close_all(self):
        for cam in self._cameras.values():
            cam.close()

    def cameras(self) -> List[Camera]:
        return list(self._cameras.values())

    def get(self, cam_id: str) -> Optional[Camera]:
        return self._cameras.get(cam_id)

    def remove(self, cam_id: str):
        cam = self._cameras.pop(cam_id, None)
        if cam:
            cam.close()

    def __len__(self):
        return len(self._cameras)

    # ── USB discovery ─────────────────────────────────────────────────────────

    @staticmethod
    def list_usb_cameras(max_check: int = 8) -> List[int]:
        """Return list of available USB camera indices."""
        available = []
        for i in range(max_check):
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                available.append(i)
                cap.release()
        return available


# ── LED detector ──────────────────────────────────────────────────────────────

@dataclass
class DetectionResult:
    found: bool
    centroid: Optional[Point2D] = None   # (col, row) in image space
    confidence: float = 0.0              # 0-1 based on brightness


class LEDDetector:
    """
    Detect a single lit pixel by comparing a lit frame against a background.

    Algorithm:
      1. Convert frames to grayscale.
      2. Subtract background; apply threshold.
      3. Find the largest bright blob.
      4. Return its centroid.
    """

    def __init__(self, threshold: int = 30, min_area: int = 2,
                 blur_ksize: int = 5):
        self.threshold = threshold
        self.min_area = min_area
        self.blur_ksize = blur_ksize

    def detect(self, background: np.ndarray,
               lit_frame: np.ndarray) -> DetectionResult:
        bg = self._to_gray(background)
        lit = self._to_gray(lit_frame)

        diff = cv2.absdiff(lit, bg)
        if self.blur_ksize > 1:
            diff = cv2.GaussianBlur(diff, (self.blur_ksize, self.blur_ksize), 0)

        _, mask = cv2.threshold(diff, self.threshold, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            return DetectionResult(found=False)

        # Largest contour by area
        best = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(best)
        if area < self.min_area:
            return DetectionResult(found=False)

        M = cv2.moments(best)
        if M["m00"] == 0:
            return DetectionResult(found=False)

        cx = M["m10"] / M["m00"]
        cy = M["m01"] / M["m00"]

        # Confidence: normalise peak brightness of the diff under the blob
        blob_mask = np.zeros_like(diff)
        cv2.drawContours(blob_mask, [best], -1, 255, cv2.FILLED)
        mean_val = cv2.mean(diff, mask=blob_mask)[0]
        confidence = min(mean_val / 255.0, 1.0)

        return DetectionResult(found=True, centroid=(cx, cy),
                               confidence=confidence)

    @staticmethod
    def _to_gray(img: np.ndarray) -> np.ndarray:
        if img.ndim == 3:
            return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return img

    def annotate(self, frame: np.ndarray, result: DetectionResult,
                 color=(0, 255, 0)) -> np.ndarray:
        """Draw detection result onto a copy of frame."""
        out = frame.copy()
        if result.found and result.centroid:
            cx, cy = int(result.centroid[0]), int(result.centroid[1])
            cv2.drawMarker(out, (cx, cy), color,
                           cv2.MARKER_CROSS, 20, 2, cv2.LINE_AA)
        return out


# ── Capture session ───────────────────────────────────────────────────────────

@dataclass
class PixelCapture:
    """All camera observations for one pixel."""
    pixel_index: int
    # cam_id → (col, row) image coordinates
    detections: Dict[str, Point2D] = field(default_factory=dict)
    confidences: Dict[str, float] = field(default_factory=dict)

    @property
    def camera_count(self) -> int:
        return len(self.detections)


def capture_backgrounds(cameras: List[Camera],
                        settle_frames: int = 3) -> Dict[str, np.ndarray]:
    """
    Capture background frames with all pixels off.
    Discards `settle_frames` frames first to let cameras adjust exposure.
    """
    for cam in cameras:
        for _ in range(settle_frames):
            cam.read()

    backgrounds = {}
    for cam in cameras:
        frame = cam.read_gray()
        if frame is not None:
            backgrounds[cam.info.cam_id] = frame
        else:
            log.warning("Background capture failed for %s", cam.info.cam_id)
    return backgrounds


def scan_pixel(pixel_index: int,
               cameras: List[Camera],
               backgrounds: Dict[str, np.ndarray],
               detector: LEDDetector,
               settle_frames: int = 1) -> PixelCapture:
    """
    Capture and detect a single lit pixel across all cameras.
    The caller is responsible for actually turning the pixel on/off via the controller.
    """
    # Discard settle frames
    for _ in range(settle_frames):
        for cam in cameras:
            cam.read()

    result = PixelCapture(pixel_index=pixel_index)
    for cam in cameras:
        frame = cam.read_gray()
        bg = backgrounds.get(cam.info.cam_id)
        if frame is None or bg is None:
            continue
        det = detector.detect(bg, frame)
        if det.found and det.centroid:
            result.detections[cam.info.cam_id] = det.centroid
            result.confidences[cam.info.cam_id] = det.confidence

    return result
