#!/usr/bin/env python3
"""
BlinkyMap WebSocket server — runs on the FPP Raspberry Pi.

Responsibilities:
  • Fire pixels one-at-a-time via E1.31/sACN to localhost
  • Coordinate with the browser (phone) which handles camera detection
  • Accumulate per-session detections and triangulate 3D positions
  • Export .xmodel / CSV when requested

WebSocket message protocol (JSON):
  Server → Browser
    {"type": "status",           "message": str}
    {"type": "pixel_on",         "index": int}        # light pixel, browser should capture
    {"type": "capture_background"}                     # browser captures dark frame
    {"type": "pixel_off"}
    {"type": "progress",         "index": int, "total": int}
    {"type": "scan_complete",    "session": int, "detected": int}
    {"type": "model",            "pixels": [...]}      # after triangulation
    {"type": "confidence",       "overall": float, "grade": str, "detail": {...}}
    {"type": "next_suggestion",  "angle": float, "distance": float,
                                  "score": float, "unseen_count": int, "reason": str}
    {"type": "export_ready",     "xmodel": str, "csv": str}   # file content inline

  Browser → Server
    {"type": "set_config",       "host": str, "universe": int, "start_ch": int,
                                  "pixel_count": int, "delay": float}
    {"type": "set_session",      "angle": float, "distance": float, "height": float}
    {"type": "set_fov",          "hfov_deg": float, "width": int, "height": int}
    {"type": "start_scan"}
    {"type": "detection",        "index": int, "cx": float, "cy": float, "conf": float}
    {"type": "no_detection",     "index": int}
    {"type": "stop_scan"}
    {"type": "export",           "format": "xmodel"|"csv"|"both"}
"""

import argparse
import asyncio
import json
import logging
import math
import os
import socket
import struct
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import requests

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
except ImportError:
    raise SystemExit("websockets package not installed. Run: pip3 install websockets")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [BlinkyMap] %(levelname)s %(message)s",
)
log = logging.getLogger("blinkymap")

# ── E1.31 / sACN ─────────────────────────────────────────────────────────────

_CID = uuid.uuid4().bytes  # stable per-process controller ID


def _abs_to_universe(abs_ch: int) -> Tuple[int, int]:
    """Convert 1-based absolute channel to (universe, 0-based offset)."""
    universe = (abs_ch - 1) // 512 + 1
    offset   = (abs_ch - 1) % 512
    return universe, offset


def _build_e131_packet(universe: int, channel_data: bytes) -> bytes:
    """Build a minimal E1.31 sACN UDP packet."""
    slots = len(channel_data)  # 1-512
    seq = 0

    dmp = (
        struct.pack("!H", 0x7000 | (11 + slots))
        + b"\x02\xa1"
        + struct.pack("!HHH", 0x0000, 0x0001, slots)
        + channel_data
    )

    source_name = b"BlinkyMap\x00" + b"\x00" * (64 - len("BlinkyMap\x00"))
    framing_pdu_length = 77 + len(dmp)
    framing = (
        struct.pack("!H", 0x7000 | framing_pdu_length)
        + b"\x00\x00\x00\x04"
        + source_name
        + struct.pack("!BBHB", 100, 0, universe, seq)
        + b"\x00"
        + dmp
    )

    root_pdu_length = 22 + len(framing)
    root = (
        b"\x00\x10"
        + b"\x00\x00"
        + b"ASC-E1.17\x00\x00\x00"
        + struct.pack("!H", 0x7000 | root_pdu_length)
        + b"\x00\x00\x00\x04"
        + _CID
        + framing
    )
    return root


# ── Pixel output abstraction ──────────────────────────────────────────────────

class E131Output:
    """Fires pixels via raw E1.31/sACN UDP — works with any E1.31 receiver."""

    def __init__(self, host: str, start_channel: int, pixel_count: int,
                 color: Tuple[int, int, int]):
        self._universe, self._offset = _abs_to_universe(start_channel)
        self._color = color
        self._buf_len = min(pixel_count * 3 + self._offset, 512)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._host = host

    def _send_raw(self, data: bytes):
        pkt = _build_e131_packet(self._universe, data)
        self._sock.sendto(pkt, (self._host, 5568))

    def pixel_on(self, idx: int):
        data = bytearray(self._buf_len)
        ch = self._offset + idx * 3
        if ch + 2 < self._buf_len:
            data[ch], data[ch+1], data[ch+2] = self._color
        self._send_raw(bytes(data))

    def all_off(self):
        self._send_raw(bytes(self._buf_len))

    def close(self):
        self.all_off()
        self._sock.close()


class FPPOutput:
    """Fires pixels via FPP's HTTP command API.

    Works transparently with multisync — FPP propagates Test Start/Stop to
    all configured remotes automatically (multisyncCommand: true).
    start_channel is the absolute FPP channel number (e.g. 9004).
    """

    def __init__(self, host: str, start_channel: int,
                 color: Tuple[int, int, int]):
        self._url   = f"http://{host}/api/command"
        self._start = start_channel
        self._color = color
        self._sess  = requests.Session()

    def pixel_on(self, idx: int):
        r, g, b = self._color
        ch_start = self._start + idx * 3
        ch_end   = ch_start + 2
        self._sess.post(self._url, json={
            "command": "Test Start",
            "multisyncCommand": True,
            "multisyncHosts": "",
            "args": ["100", "RGB Single Color",
                     f"{ch_start}-{ch_end}", f"#{r:02x}{g:02x}{b:02x}"],
        }, timeout=3)

    def all_off(self):
        self._sess.post(self._url, json={
            "command": "Test Stop",
            "multisyncCommand": True,
            "multisyncHosts": "",
            "args": [],
        }, timeout=3)

    def close(self):
        self.all_off()
        self._sess.close()


def _is_fpp(host: str) -> bool:
    """Return True if host is a reachable FPP instance."""
    try:
        r = requests.get(f"http://{host}/api/fppd/status", timeout=1.5)
        return r.status_code == 200
    except Exception:
        return False


def _make_output(cfg: "ControllerConfig"):
    """Factory: auto-detect FPP or fall back to E1.31."""
    use_fpp = (
        cfg.output_mode == "fpp"
        or (cfg.output_mode == "auto" and _is_fpp(cfg.host))
    )
    if use_fpp:
        log.info("Output: FPP API → http://%s (abs ch %d)", cfg.host, cfg.start_channel)
        return FPPOutput(cfg.host, cfg.start_channel, cfg.pixel_color)
    log.info("Output: E1.31 → %s universe %d offset %d",
             cfg.host, *_abs_to_universe(cfg.start_channel))
    return E131Output(cfg.host, cfg.start_channel, cfg.pixel_count, cfg.pixel_color)


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class ControllerConfig:
    host: str = "127.0.0.1"
    start_channel: int = 1       # 1-based absolute FPP channel
    pixel_count: int = 100
    inter_pixel_delay: float = 0.15
    pixel_color: Tuple[int, int, int] = (255, 255, 255)
    output_mode: str = "auto"    # "auto" | "fpp" | "e131"


# ── Camera / session geometry ─────────────────────────────────────────────────

@dataclass
class SessionConfig:
    session_id: int
    angle_deg: float   # horizontal angle around tree (0 = front)
    distance_m: float  # metres from trunk centre
    height_m: float    # camera height above ground
    hfov_deg: float = 60.0
    img_width: int = 1280
    img_height: int = 720


@dataclass
class Detection:
    cx: float   # pixel x in image (0…img_width)
    cy: float   # pixel y in image (0…img_height)
    conf: float # 0..1


# ── Triangulation (pure numpy — no OpenCV) ────────────────────────────────────

def _make_K(width: int, height: int, hfov_deg: float) -> np.ndarray:
    fx = (width / 2.0) / math.tan(math.radians(hfov_deg / 2.0))
    fy = fx
    cx = width / 2.0
    cy = height / 2.0
    return np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float64)


def _look_at_R(eye: np.ndarray, target: np.ndarray) -> np.ndarray:
    z = target - eye
    z /= np.linalg.norm(z)
    up = np.array([0.0, 1.0, 0.0])
    x = np.cross(z, up)
    if np.linalg.norm(x) < 1e-6:
        up = np.array([0.0, 0.0, 1.0])
        x = np.cross(z, up)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    return np.vstack([x, y, z])


def _projection_matrix(sess: SessionConfig) -> np.ndarray:
    K = _make_K(sess.img_width, sess.img_height, sess.hfov_deg)
    rad = math.radians(sess.angle_deg)
    eye = np.array([
        sess.distance_m * math.sin(rad),
        sess.height_m,
        sess.distance_m * math.cos(rad),
    ])
    target = np.array([0.0, sess.height_m * 0.5, 0.0])
    R = _look_at_R(eye, target)
    t = -R @ eye
    Rt = np.hstack([R, t.reshape(3, 1)])
    return K @ Rt  # 3×4


def _triangulate_pair(
    P1: np.ndarray, pt1: Tuple[float, float],
    P2: np.ndarray, pt2: Tuple[float, float],
) -> Optional[np.ndarray]:
    """DLT triangulation for one pair of 2D observations."""
    A = np.array([
        pt1[1] * P1[2] - P1[1],
        P1[0] - pt1[0] * P1[2],
        pt2[1] * P2[2] - P2[1],
        P2[0] - pt2[0] * P2[2],
    ])
    _, _, Vt = np.linalg.svd(A)
    X = Vt[-1]
    if abs(X[3]) < 1e-10:
        return None
    X = X[:3] / X[3]
    # Reject points behind either camera
    def depth(P, Xh):
        w = P[2] @ np.append(Xh, 1.0)
        return w
    if depth(P1, X) < 0 or depth(P2, X) < 0:
        return None
    return X


def _reprojection_error(P: np.ndarray, X: np.ndarray, pt: Tuple[float, float]) -> float:
    Xh = np.append(X, 1.0)
    proj = P @ Xh
    proj /= proj[2]
    return float(np.hypot(proj[0] - pt[0], proj[1] - pt[1]))


def _angular_spread(projections: List[Tuple[np.ndarray, SessionConfig]]) -> float:
    """Mean angular separation between all camera pairs (0..1 normalised)."""
    if len(projections) < 2:
        return 0.0
    angles = [math.radians(s.angle_deg) for _, s in projections]
    max_sep = 0.0
    for i in range(len(angles)):
        for j in range(i + 1, len(angles)):
            sep = abs(angles[i] - angles[j]) % (2 * math.pi)
            if sep > math.pi:
                sep = 2 * math.pi - sep
            max_sep = max(max_sep, sep)
    return min(max_sep / math.pi, 1.0)


# ── Model state ───────────────────────────────────────────────────────────────

@dataclass
class PixelResult:
    index: int
    position: Optional[np.ndarray] = None
    confidence: float = 0.0
    reprojection_error: float = 0.0
    sessions_detected: List[int] = field(default_factory=list)

    @property
    def grade(self) -> str:
        if self.confidence >= 0.75:
            return "high"
        if self.confidence >= 0.4:
            return "medium"
        if self.position is not None:
            return "low"
        return "unseen"


class BlinkyModel:
    """Holds all session data and does triangulation on demand."""

    def __init__(self):
        # session_id → (SessionConfig, Dict[pixel_index, Detection])
        self.sessions: Dict[int, Tuple[SessionConfig, Dict[int, Detection]]] = {}
        self.results: Dict[int, PixelResult] = {}
        self.pixel_count: int = 0

    def add_session(self, sess: SessionConfig):
        self.sessions[sess.session_id] = (sess, {})

    def record_detection(self, session_id: int, pixel_index: int, det: Detection):
        if session_id in self.sessions:
            self.sessions[session_id][1][pixel_index] = det

    def triangulate(self):
        if not self.sessions or self.pixel_count == 0:
            return

        # Build projection matrices once per session
        proj: Dict[int, Tuple[np.ndarray, SessionConfig]] = {
            sid: (_projection_matrix(sc), sc)
            for sid, (sc, _) in self.sessions.items()
        }

        results: Dict[int, PixelResult] = {}

        for idx in range(self.pixel_count):
            # Gather all observations for this pixel
            obs: List[Tuple[int, Detection]] = []
            for sid, (_, detections) in self.sessions.items():
                if idx in detections:
                    obs.append((sid, detections[idx]))

            pr = PixelResult(index=idx, sessions_detected=[s for s, _ in obs])

            if len(obs) >= 2:
                # All pairs → triangulate, take median
                candidates: List[np.ndarray] = []
                reproj_errors: List[float] = []
                paired_projections: List[Tuple[np.ndarray, SessionConfig]] = []

                for i in range(len(obs)):
                    for j in range(i + 1, len(obs)):
                        sid_a, det_a = obs[i]
                        sid_b, det_b = obs[j]
                        P_a, sc_a = proj[sid_a]
                        P_b, sc_b = proj[sid_b]
                        X = _triangulate_pair(
                            P_a, (det_a.cx, det_a.cy),
                            P_b, (det_b.cx, det_b.cy),
                        )
                        if X is not None:
                            candidates.append(X)
                            err_a = _reprojection_error(P_a, X, (det_a.cx, det_a.cy))
                            err_b = _reprojection_error(P_b, X, (det_b.cx, det_b.cy))
                            reproj_errors.append((err_a + err_b) / 2.0)
                            if (P_a, sc_a) not in paired_projections:
                                paired_projections.append((P_a, sc_a))
                            if (P_b, sc_b) not in paired_projections:
                                paired_projections.append((P_b, sc_b))

                if candidates:
                    pos = np.median(np.array(candidates), axis=0)
                    mean_err = float(np.median(reproj_errors))
                    spread = _angular_spread(paired_projections)
                    n = len(obs)
                    coverage = min(n / max(len(self.sessions), 1), 1.0)
                    # Normalise reprojection error (0px→1.0, 20px→0.0)
                    accuracy = max(0.0, 1.0 - mean_err / 20.0)
                    conf = 0.30 * coverage + 0.35 * spread + 0.35 * accuracy
                    pr.position = pos
                    pr.confidence = float(np.clip(conf, 0.0, 1.0))
                    pr.reprojection_error = mean_err

            results[idx] = pr

        self.results = results

    def model_confidence(self) -> dict:
        if not self.results:
            return {"overall": 0.0, "grade": "Poor", "coverage": 0.0,
                    "mean_confidence": 0.0, "high": 0, "medium": 0, "low": 0, "unseen": 0}

        grades = {"high": 0, "medium": 0, "low": 0, "unseen": 0}
        confs = []
        for pr in self.results.values():
            grades[pr.grade] += 1
            if pr.position is not None:
                confs.append(pr.confidence)

        n = len(self.results)
        coverage = (grades["high"] + grades["medium"] + grades["low"]) / max(n, 1)
        mean_conf = float(np.mean(confs)) if confs else 0.0
        overall = 0.5 * coverage + 0.5 * mean_conf

        if overall >= 0.80:
            grade = "Excellent"
        elif overall >= 0.60:
            grade = "Good"
        elif overall >= 0.35:
            grade = "Fair"
        else:
            grade = "Poor"

        return {
            "overall": round(overall, 3),
            "grade": grade,
            "coverage": round(coverage, 3),
            "mean_confidence": round(mean_conf, 3),
            **grades,
        }

    def to_json_pixels(self) -> list:
        out = []
        for pr in self.results.values():
            entry = {
                "index": pr.index,
                "grade": pr.grade,
                "confidence": round(pr.confidence, 3),
                "sessions": pr.sessions_detected,
            }
            if pr.position is not None:
                entry["x"] = round(float(pr.position[0]), 4)
                entry["y"] = round(float(pr.position[1]), 4)
                entry["z"] = round(float(pr.position[2]), 4)
            out.append(entry)
        return out


# ── Export ────────────────────────────────────────────────────────────────────

def _normalize(results: Dict[int, PixelResult]) -> Dict[int, np.ndarray]:
    positioned = {i: pr.position for i, pr in results.items() if pr.position is not None}
    if not positioned:
        return {}
    pts = np.array(list(positioned.values()))
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    rng = hi - lo
    rng[rng < 1e-6] = 1.0
    return {i: (p - lo) / rng for i, p in positioned.items()}


def export_csv(model: BlinkyModel) -> str:
    lines = ["Channel,X,Y,Z"]
    normed = _normalize(model.results)
    for idx in sorted(normed):
        p = normed[idx]
        lines.append(f"{idx+1},{p[0]:.4f},{p[1]:.4f},{p[2]:.4f}")
    return "\n".join(lines)


def export_xmodel(model: BlinkyModel, pixel_count: int) -> str:
    normed = _normalize(model.results)
    if not normed:
        return ""

    # Cylindrical unwrap: angle→col, height→row
    grid: Dict[Tuple[int, int], int] = {}
    cx = float(np.mean([p[0] for p in normed.values()]))
    cz = float(np.mean([p[2] for p in normed.values()]))

    n_cols = 36
    n_rows = 20
    for idx, p in normed.items():
        angle = math.atan2(float(p[0]) - cx, float(p[2]) - cz)
        col = int((angle + math.pi) / (2 * math.pi) * n_cols) % n_cols
        row = int((1.0 - float(p[1])) * (n_rows - 1))
        row = max(0, min(row, n_rows - 1))
        key = (row, col)
        # last writer wins (could also pick by confidence)
        grid[key] = idx + 1  # 1-based channel

    rows_data = []
    for r in range(n_rows):
        cols_data = []
        for c in range(n_cols):
            cols_data.append(str(grid.get((r, c), 0)))
        rows_data.append(",".join(cols_data))
    custom_model = ";".join(rows_data)

    root = ET.Element("custommodel")
    root.set("name", "BlinkyTree")
    root.set("parm1", str(n_cols))
    root.set("parm2", str(n_rows))
    root.set("CustomModel", custom_model)
    root.set("SourceVersion", "2016.39")
    root.set("CustomModelCompressed", "false")
    return ET.tostring(root, encoding="unicode", xml_declaration=False)


# ── Next-session suggester ────────────────────────────────────────────────────

def _angular_dist(a: float, b: float) -> float:
    """Shortest angular distance in degrees, result in [0, 180]."""
    diff = abs(a - b) % 360.0
    return diff if diff <= 180.0 else 360.0 - diff


def suggest_next_angle(model: BlinkyModel, sessions: Dict) -> dict:
    """
    Score every 5° candidate angle and recommend the best next camera position.

    Three weighted sub-scores (height assumed constant):
      gap_score      (0.30) — prefer angles far from every existing session
      coverage_score (0.50) — prefer facing tree sectors with few detected pixels
      spread_score   (0.20) — prefer angles that widen triangulation baseline
                              for already-positioned low-confidence pixels

    Returns a dict suitable for JSON broadcast.
    """
    existing = [(sc.angle_deg, sc.distance_m)
                for _, (sc, _) in sessions.items()]
    existing_angles   = [a for a, _ in existing]
    existing_distances = [d for _, d in existing]
    suggested_distance = float(np.median(existing_distances)) if existing_distances else 2.0

    # ── Trivial cases ─────────────────────────────────────────────────────────
    if not existing_angles:
        return _suggestion(0.0, suggested_distance, 0.5, 0,
                           "Start anywhere — first session")

    if not model.results:
        opp = (existing_angles[-1] + 180.0) % 360.0
        return _suggestion(opp, suggested_distance, 0.6, 0,
                           "Opposite side — needed for first triangulation")

    # ── Build density map of positioned pixels by XZ angle ───────────────────
    N_BINS   = 36
    bin_size = 360.0 / N_BINS
    bin_counts   = [0] * N_BINS   # how many pixels detected in each sector
    bin_low_conf = [0] * N_BINS   # of those, how many have low confidence

    positioned = [(pr.position, pr.confidence)
                  for pr in model.results.values() if pr.position is not None]

    # Estimate tree centre from median of positioned pixels
    if positioned:
        xs = [p[0] for p, _ in positioned]
        zs = [p[2] for p, _ in positioned]
        cx, cz = float(np.median(xs)), float(np.median(zs))
    else:
        cx, cz = 0.0, 0.0

    for pos, conf in positioned:
        phi = math.degrees(math.atan2(pos[0] - cx, pos[2] - cz)) % 360.0
        b   = int(phi / bin_size) % N_BINS
        bin_counts[b]   += 1
        if conf < 0.60:
            bin_low_conf[b] += 1

    unseen_count = sum(1 for pr in model.results.values() if pr.position is None)
    max_count    = max(bin_counts) if any(c > 0 for c in bin_counts) else 1

    # ── Score each candidate angle ────────────────────────────────────────────
    best_angle  = existing_angles[-1]   # fallback
    best_score  = -1.0
    best_parts  = {}

    for theta in range(0, 360, 5):
        theta_f = float(theta)

        # 1. Gap from existing sessions
        gap       = min(_angular_dist(theta_f, a) for a in existing_angles)
        gap_score = gap / 180.0

        # 2. Coverage: camera at theta sees tree sectors within ±90° of theta.
        #    Weight each covered sector by how sparse it is (1 - fraction of max).
        coverage_score = 0.0
        for b in range(N_BINS):
            sector_centre = b * bin_size + bin_size / 2.0
            if _angular_dist(theta_f, sector_centre) <= 90.0:
                sparsity = 1.0 - bin_counts[b] / max_count
                coverage_score += sparsity
        coverage_score /= N_BINS   # normalise to [0, 1]

        # 3. Spread improvement for low-conf pixels:
        #    Use the minimum angular gap theta adds to any existing session —
        #    larger gap = more orthogonal baseline = better triangulation.
        new_min_gap  = min(_angular_dist(theta_f, a) for a in existing_angles)
        spread_score = new_min_gap / 180.0

        total = 0.30 * gap_score + 0.50 * coverage_score + 0.20 * spread_score

        if total > best_score:
            best_score  = total
            best_angle  = theta_f
            best_parts  = {"gap": gap_score, "coverage": coverage_score, "spread": spread_score}

    # ── Build human-readable reason ───────────────────────────────────────────
    dominant = max(best_parts, key=best_parts.get)
    if dominant == "coverage" and unseen_count > 0:
        reason = (f"{unseen_count} pixels not yet seen — "
                  f"this angle faces their likely hiding spot")
    elif dominant == "coverage":
        reason = "Faces the least-scanned side of the tree"
    elif dominant == "gap":
        reason = "Largest unexplored viewing angle around the tree"
    else:
        reason = "Adds the widest new baseline for low-confidence pixels"

    return _suggestion(best_angle, suggested_distance, best_score, unseen_count, reason)


def _suggestion(angle: float, distance: float, score: float,
                unseen: int, reason: str) -> dict:
    return {
        "angle":        round(angle, 1),
        "distance":     round(distance, 2),
        "score":        round(score, 3),
        "unseen_count": unseen,
        "reason":       reason,
    }


# ── Session ID counter ────────────────────────────────────────────────────────

_next_session_id = 1


def _new_session_id() -> int:
    global _next_session_id
    sid = _next_session_id
    _next_session_id += 1
    return sid


# ── WebSocket handler ─────────────────────────────────────────────────────────

class BlinkyServer:
    def __init__(self):
        self.model = BlinkyModel()
        self.config = ControllerConfig()
        self.current_session: Optional[SessionConfig] = None
        self.scan_task: Optional[asyncio.Task] = None
        self.test_task: Optional[asyncio.Task] = None
        self.clients: Set[WebSocketServerProtocol] = set()

        # Per-pixel detection response — set by incoming "detection"/"no_detection"
        self._detection_event: asyncio.Event = asyncio.Event()
        self._last_detection: Optional[Detection] = None

    async def broadcast(self, msg: dict):
        if self.clients:
            data = json.dumps(msg)
            await asyncio.gather(*(c.send(data) for c in self.clients), return_exceptions=True)

    async def handler(self, ws: WebSocketServerProtocol):
        self.clients.add(ws)
        log.info("Client connected (%d total)", len(self.clients))
        try:
            # Send current state summary
            await ws.send(json.dumps({"type": "status", "message": "BlinkyMap ready"}))
            async for raw in ws:
                await self._handle_message(ws, raw)
        except Exception as e:
            log.debug("Client error: %s", e)
        finally:
            self.clients.discard(ws)
            log.info("Client disconnected (%d total)", len(self.clients))

    async def _handle_message(self, ws: WebSocketServerProtocol, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        t = msg.get("type")

        if t == "set_config":
            self.config.host              = msg.get("host", self.config.host)
            self.config.start_channel     = int(msg.get("start_ch", self.config.start_channel))
            self.config.pixel_count       = int(msg.get("pixel_count", self.config.pixel_count))
            self.config.inter_pixel_delay = float(msg.get("delay", self.config.inter_pixel_delay))
            self.config.output_mode       = msg.get("output_mode", self.config.output_mode)
            self.model.pixel_count        = self.config.pixel_count
            await ws.send(json.dumps({"type": "status", "message": "Config saved"}))
            asyncio.create_task(self._probe_controller(ws))

        elif t == "set_session":
            sid = _new_session_id()
            self.current_session = SessionConfig(
                session_id=sid,
                angle_deg=float(msg.get("angle", 0)),
                distance_m=float(msg.get("distance", 2.0)),
                height_m=float(msg.get("height", 1.5)),
                hfov_deg=float(msg.get("hfov_deg", self.current_session.hfov_deg
                                       if self.current_session else 60.0)),
                img_width=int(msg.get("img_width", 1280)),
                img_height=int(msg.get("img_height", 720)),
            )
            self.model.add_session(self.current_session)
            await ws.send(json.dumps({"type": "status",
                                       "message": f"Session {sid} ready"}))

        elif t == "set_fov":
            hfov = float(msg.get("hfov_deg", 60))
            w = int(msg.get("width", 1280))
            h = int(msg.get("height", 720))
            if self.current_session:
                self.current_session.hfov_deg = hfov
                self.current_session.img_width = w
                self.current_session.img_height = h

        elif t == "start_scan":
            if self.scan_task and not self.scan_task.done():
                await ws.send(json.dumps({"type": "status", "message": "Scan already running"}))
                return
            if not self.current_session:
                await ws.send(json.dumps({"type": "status",
                                           "message": "Set session position first"}))
                return
            self.scan_task = asyncio.create_task(self._run_scan())

        elif t == "detection":
            idx = int(msg["index"])
            det = Detection(
                cx=float(msg["cx"]),
                cy=float(msg["cy"]),
                conf=float(msg.get("conf", 1.0)),
            )
            self._last_detection = (idx, det)
            self._detection_event.set()
            log.info("Detection received: pixel %d conf=%.2f", idx, det.conf)

        elif t == "no_detection":
            self._last_detection = None
            self._detection_event.set()
            log.debug("No-detection received: pixel %d", msg.get("index", -1))

        elif t == "test_sweep":
            if self.test_task and not self.test_task.done():
                return
            self.test_task = asyncio.create_task(self._run_test_sweep(ws))

        elif t == "stop_test":
            if self.test_task:
                self.test_task.cancel()

        elif t == "stop_scan":
            if self.scan_task:
                self.scan_task.cancel()
            await ws.send(json.dumps({"type": "status", "message": "Scan stopped"}))

        elif t == "export":
            fmt = msg.get("format", "both")
            resp: dict = {"type": "export_ready"}
            if fmt in ("xmodel", "both"):
                resp["xmodel"] = export_xmodel(self.model, self.config.pixel_count)
            if fmt in ("csv", "both"):
                resp["csv"] = export_csv(self.model)
            await ws.send(json.dumps(resp))

    async def _run_test_sweep(self, ws: WebSocketServerProtocol):
        cfg  = self.config
        loop = asyncio.get_running_loop()
        output = None

        try:
            output = await loop.run_in_executor(None, lambda: _make_output(cfg))
            mode = "FPP API" if isinstance(output, FPPOutput) else "E1.31"
            n = cfg.pixel_count

            for idx in range(n):
                def _one(i=idx, o=output):
                    o.pixel_on(i)
                    time.sleep(0.15)

                await loop.run_in_executor(None, _one)
                await ws.send(json.dumps({
                    "type": "test_sweep_progress",
                    "index": idx,
                    "total": n,
                    "mode": mode,
                    "start_ch": cfg.start_channel,
                }))

            await loop.run_in_executor(None, output.all_off)
            await ws.send(json.dumps({
                "type": "test_sweep_done", "ok": True,
                "message": f"Sweep complete — {n} pixels via {mode} (ch {cfg.start_channel}+)",
            }))

        except asyncio.CancelledError:
            if output:
                try:
                    await loop.run_in_executor(None, output.all_off)
                except Exception:
                    pass
            await ws.send(json.dumps({
                "type": "test_sweep_done", "ok": True, "message": "Stopped",
            }))

        except Exception as e:
            await ws.send(json.dumps({
                "type": "test_sweep_done", "ok": False, "message": f"Error: {e}",
            }))

        finally:
            if output:
                try:
                    await loop.run_in_executor(None, output.close)
                except Exception:
                    pass

    async def _probe_controller(self, ws: WebSocketServerProtocol):
        cfg  = self.config
        loop = asyncio.get_running_loop()
        try:
            if cfg.output_mode == "e131":
                msg = f"E1.31 mode → {cfg.host} (ch {cfg.start_channel})"
                ok  = True
            else:
                is_fpp = await loop.run_in_executor(None, lambda: _is_fpp(cfg.host))
                if is_fpp:
                    msg = f"FPP API connected @ {cfg.host} (ch {cfg.start_channel})"
                    ok  = True
                else:
                    msg = f"FPP not reachable @ {cfg.host} — will use E1.31"
                    ok  = False
            await ws.send(json.dumps({"type": "controller_status", "ok": ok, "message": msg}))
        except Exception as e:
            await ws.send(json.dumps({
                "type": "controller_status", "ok": False, "message": f"Error: {e}",
            }))

    async def _run_scan(self):
        sess = self.current_session
        cfg  = self.config
        loop = asyncio.get_running_loop()
        output = await loop.run_in_executor(None, lambda: _make_output(cfg))

        total    = cfg.pixel_count
        detected = 0

        try:
            # Request background capture
            await self.broadcast({"type": "capture_background"})
            await asyncio.sleep(0.5)

            for idx in range(total):
                await loop.run_in_executor(None, lambda i=idx: output.pixel_on(i))
                # Allow FPP multisync to propagate to remotes before browser captures
                await asyncio.sleep(cfg.inter_pixel_delay)
                await self.broadcast({"type": "pixel_on", "index": idx})

                # Wait for browser detection response (or timeout)
                self._detection_event.clear()
                self._last_detection = None
                try:
                    await asyncio.wait_for(self._detection_event.wait(),
                                           timeout=cfg.inter_pixel_delay + 2.0)
                except asyncio.TimeoutError:
                    log.warning("Scan pixel %d: timed out waiting for browser", idx)

                if self._last_detection is not None:
                    det_idx, det = self._last_detection
                    if det_idx == idx:
                        self.model.record_detection(sess.session_id, idx, det)
                        detected += 1
                        log.info("Scan pixel %d: COUNTED conf=%.2f", idx, det.conf)
                    else:
                        log.warning("Scan pixel %d: index mismatch got=%d", idx, det_idx)
                else:
                    log.debug("Scan pixel %d: no detection (timeout or no_detection)", idx)

                await self.broadcast({"type": "pixel_off"})
                await self.broadcast({"type": "progress", "index": idx, "total": total})
                await asyncio.sleep(0.02)

            await loop.run_in_executor(None, output.all_off)
            await self.broadcast({
                "type": "scan_complete",
                "session": sess.session_id,
                "detected": detected,
                "total": total,
            })

            # Triangulate and broadcast model
            self.model.triangulate()
            await self.broadcast({
                "type": "model",
                "pixels": self.model.to_json_pixels(),
            })
            await self.broadcast({
                "type": "confidence",
                **self.model.model_confidence(),
            })

            # Suggest best next camera position
            suggestion = suggest_next_angle(self.model, self.model.sessions)
            await self.broadcast({
                "type": "next_suggestion",
                **suggestion,
            })

        except asyncio.CancelledError:
            await loop.run_in_executor(None, output.all_off)
            log.info("Scan cancelled")
        finally:
            await loop.run_in_executor(None, output.close)


# ── Entry point ───────────────────────────────────────────────────────────────

async def main_async(port: int):
    server = BlinkyServer()
    log.info("BlinkyMap WebSocket server starting on port %d", port)
    async with websockets.serve(server.handler, "0.0.0.0", port):
        await asyncio.Future()   # run forever


def main():
    parser = argparse.ArgumentParser(description="BlinkyMap WebSocket server")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        asyncio.run(main_async(args.port))
    except KeyboardInterrupt:
        log.info("Shutting down")


if __name__ == "__main__":
    main()
