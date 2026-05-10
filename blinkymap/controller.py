"""
Pixel controller: fire individual pixels via E1.31/sACN.
Auto-detects FPP; falls back to direct unicast E1.31.
"""

import socket
import struct
import time
import uuid
import logging
from dataclasses import dataclass, field
from typing import Optional

import requests

log = logging.getLogger(__name__)

# ── E1.31 packet builder ──────────────────────────────────────────────────────

_VECTOR_ROOT = 0x00000004
_VECTOR_FRAMING = 0x00000002
_VECTOR_DMP = 0x02


def _build_e131_packet(universe: int, dmx: bytes, seq: int, cid: bytes,
                       source: str = "BlinkyMap") -> bytes:
    slots = (dmx + bytes(512))[:512]
    n = len(slots)
    prop_count = n + 1  # +1 for start code

    dmp_len = 11 + n
    framing_len = 77 + dmp_len
    root_len = 22 + framing_len

    src_name = source.encode()[:63].ljust(64, b"\x00")

    pkt = bytearray()
    # Preamble / postamble / ACN ID
    pkt += struct.pack("!HH", 0x0010, 0x0000)
    pkt += b"ASC-E1.17\x00\x00\x00"
    # Root PDU
    pkt += struct.pack("!H", 0x7000 | root_len)
    pkt += struct.pack("!I", _VECTOR_ROOT)
    pkt += cid[:16]
    # Framing PDU
    pkt += struct.pack("!H", 0x7000 | framing_len)
    pkt += struct.pack("!I", _VECTOR_FRAMING)
    pkt += src_name
    pkt += struct.pack("!BHBBH", 100, 0, seq & 0xFF, 0, universe)
    # DMP PDU
    pkt += struct.pack("!H", 0x7000 | dmp_len)
    pkt += struct.pack("!BBHHH", _VECTOR_DMP, 0xA1, 0x0000, 0x0001, prop_count)
    pkt += b"\x00"   # DMX start code
    pkt += slots
    return bytes(pkt)


# ── FPP REST helper ───────────────────────────────────────────────────────────

class FPPClient:
    """Lightweight FPP REST client — used only for connection probe and config."""

    def __init__(self, host: str, port: int = 80, timeout: float = 5.0):
        self.base = f"http://{host}:{port}"
        self.timeout = timeout
        self._session = requests.Session()

    def ping(self) -> Optional[dict]:
        try:
            r = self._session.get(f"{self.base}/api/system/status",
                                  timeout=self.timeout)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    def get_version(self) -> Optional[str]:
        info = self.ping()
        return info.get("fppVersion") if info else None


# ── High-level pixel controller ───────────────────────────────────────────────

@dataclass
class ControllerConfig:
    host: str = "192.168.1.100"
    universe: int = 1
    start_channel: int = 1        # 1-based DMX channel for pixel 0 red
    pixel_count: int = 100
    pixel_color: tuple = (255, 255, 255)   # default white
    inter_pixel_delay: float = 0.15        # seconds between fire and capture


class PixelController:
    """
    Fire individual pixels one at a time for camera mapping.

    Priority: FPP REST health-check → E1.31 unicast to controller host.
    Actual pixel data is always sent via E1.31 (sACN) for reliability.
    """

    def __init__(self, config: ControllerConfig):
        self.cfg = config
        self._cid = uuid.uuid4().bytes[:16]
        self._seq = 0
        self._sock: Optional[socket.socket] = None
        self._fpp: Optional[FPPClient] = None
        self.fpp_detected = False

    # ── connection ────────────────────────────────────────────────────────────

    def connect(self) -> dict:
        """Open UDP socket and probe for FPP. Returns status dict."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        self._fpp = FPPClient(self.cfg.host)
        info = self._fpp.ping()
        self.fpp_detected = info is not None

        result = {
            "e131_ready": True,
            "fpp_detected": self.fpp_detected,
            "fpp_version": info.get("fppVersion") if info else None,
            "host": self.cfg.host,
        }
        log.info("Controller connect: %s", result)
        return result

    def disconnect(self):
        if self._sock:
            self.blackout()
            self._sock.close()
            self._sock = None

    # ── E1.31 send ────────────────────────────────────────────────────────────

    def _send(self, dmx: bytes):
        if self._sock is None:
            raise RuntimeError("Call connect() first")
        pkt = _build_e131_packet(
            self.cfg.universe, dmx, self._seq, self._cid
        )
        self._sock.sendto(pkt, (self.cfg.host, 5568))
        self._seq = (self._seq + 1) & 0xFF

    def _make_dmx(self, pixel_index: Optional[int],
                  color: tuple = (255, 255, 255)) -> bytes:
        """Build 512-byte DMX frame with one pixel lit (or all-off)."""
        buf = bytearray(512)
        if pixel_index is not None:
            ch = (self.cfg.start_channel - 1) + pixel_index * 3
            if 0 <= ch + 2 < 512:
                buf[ch], buf[ch + 1], buf[ch + 2] = color
        return bytes(buf)

    # ── public API ────────────────────────────────────────────────────────────

    def blackout(self):
        self._send(bytes(512))

    def light_pixel(self, index: int, color: Optional[tuple] = None):
        c = color or self.cfg.pixel_color
        self._send(self._make_dmx(index, c))

    def flash_pixel(self, index: int, color: Optional[tuple] = None,
                    duration: Optional[float] = None) -> None:
        """Light pixel, wait, then blackout — blocking."""
        self.light_pixel(index, color)
        time.sleep(duration or self.cfg.inter_pixel_delay)
        self.blackout()

    def test_pattern(self, count: int = 5):
        """Quick chase across first `count` pixels for visual check."""
        for i in range(min(count, self.cfg.pixel_count)):
            self.light_pixel(i)
            time.sleep(0.1)
        self.blackout()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.disconnect()
