"""
3D point cloud viewer.

Tries vispy first (GPU-accelerated, smooth TurntableCamera — click-drag to
orbit, scroll to zoom, right-drag to pan).  Falls back to matplotlib if vispy
is not installed (matplotlib also supports click-drag rotation, just softer).
"""

import logging
from typing import TYPE_CHECKING, Dict

import numpy as np
import tkinter as tk
from tkinter import ttk

if TYPE_CHECKING:
    from .triangulate import PixelResult

log = logging.getLogger(__name__)


# ── colour tables ─────────────────────────────────────────────────────────────

# RGBA float32 for vispy
_RGBA = {
    "high":   np.array([0.133, 0.773, 0.435, 1.0], dtype=np.float32),
    "medium": np.array([0.918, 0.702, 0.031, 1.0], dtype=np.float32),
    "low":    np.array([0.937, 0.267, 0.267, 1.0], dtype=np.float32),
}

# Hex for matplotlib
_HEX = {"high": "#22c55e", "medium": "#eab308", "low": "#ef4444"}


# ── shared data builder ───────────────────────────────────────────────────────

def _build_arrays(results: Dict) -> tuple:
    """
    Return (positions, dot_colors, halo_colors, halo_sizes) numpy arrays
    for all triangulated pixels, or (None, None, None, None) if empty.

    Halo size is in screen pixels (independent of zoom) and scales as
    (1 − confidence)^1.2 so tight green dots = high confidence,
    large fuzzy halos = poor geometry — like GPS accuracy rings.
    """
    rows = []
    for r in results.values():
        if r.position is None:
            continue
        rgba = _RGBA.get(r.confidence_label)
        if rgba is None:
            continue
        rows.append((r.position, rgba, float(r.confidence)))

    if not rows:
        return None, None, None, None

    pts   = np.array([row[0] for row in rows], dtype=np.float32)
    dot_c = np.array([row[1] for row in rows], dtype=np.float32)
    confs = np.array([row[2] for row in rows], dtype=np.float32)

    # Halo colour = same RGB, alpha fades for confident pixels
    halo_c = dot_c.copy()
    halo_c[:, 3] = (1.0 - confs) ** 1.2 * 0.35

    # Halo screen size: 0px (confident) → ~70px (very uncertain)
    halo_s = (1.0 - confs) ** 1.2 * 65 + 5

    return pts, dot_c, halo_c, halo_s


# ── try vispy ─────────────────────────────────────────────────────────────────

_VISPY_OK = False
try:
    # Must set the backend before importing scene
    from vispy import use as _vispy_use
    _vispy_use("tk", "gl2")
    from vispy import scene as _scene
    _VISPY_OK = True
    log.debug("vispy available — will use GPU viewer")
except Exception as _e:
    log.debug("vispy not available (%s); will use matplotlib", _e)


class VispyViewer:
    """
    GPU-accelerated 3D viewer.

    Mouse controls (vispy TurntableCamera):
      Left-drag   — orbit / rotate
      Right-drag  — pan
      Scroll      — zoom in / out
      Double-click — reset to default view
    """
    backend = "vispy (GPU)"

    def __init__(self, parent: tk.Widget):
        self._canvas = _scene.SceneCanvas(
            parent=parent,
            keys="interactive",
            bgcolor="#1a1a2e",
            size=(420, 340),
        )
        self._view = self._canvas.central_widget.add_view()
        self._view.camera = _scene.cameras.TurntableCamera(
            fov=40, azimuth=30, elevation=25,
        )
        # Fixed scene elements
        _scene.visuals.XYZAxis(parent=self._view.scene)

        # Reusable marker visuals (updated in-place each refresh)
        self._halos = _scene.visuals.Markers(parent=self._view.scene)
        self._dots  = _scene.visuals.Markers(parent=self._view.scene)

        self.widget = self._canvas.native

    def update(self, results: Dict) -> None:
        pts, dot_c, halo_c, halo_s = _build_arrays(results)
        if pts is None:
            return

        self._halos.set_data(
            pts, face_color=halo_c,
            size=halo_s, edge_width=0, scaling=False,
        )
        self._dots.set_data(
            pts, face_color=dot_c,
            size=10, edge_width=1.0,
            edge_color=(1.0, 1.0, 1.0, 0.5),
            scaling=False,
        )

        # Auto-fit camera on first call
        centre = pts.mean(axis=0)
        span   = float(np.ptp(pts, axis=0).max())
        self._view.camera.center   = tuple(centre)
        self._view.camera.distance = max(span * 2.5, 0.5)
        self._canvas.update()

    def reset_view(self) -> None:
        self._view.camera.reset()
        self._canvas.update()


# ── try matplotlib ────────────────────────────────────────────────────────────

_MPL_OK = False
try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    _MPL_OK = True
    log.debug("matplotlib available — will use as 3D viewer fallback")
except Exception as _e:
    log.debug("matplotlib not available: %s", _e)


class MatplotlibViewer:
    """
    Software-rendered 3D viewer (matplotlib mpl_toolkits.mplot3d).

    Mouse controls (built into matplotlib):
      Left-drag  — orbit / rotate
      Right-drag — zoom
      Scroll     — zoom
    """
    backend = "matplotlib (software)"

    def __init__(self, parent: tk.Widget,
                 figsize: tuple = (4.2, 3.4), dpi: int = 88):
        self._fig = Figure(figsize=figsize, dpi=dpi, facecolor="#1a1a2e")
        self._ax  = self._fig.add_subplot(111, projection="3d")
        self._ax.set_facecolor("#1a1a2e")
        for pane in (self._ax.xaxis.pane, self._ax.yaxis.pane,
                     self._ax.zaxis.pane):
            pane.fill = False
        self._mpl = FigureCanvasTkAgg(self._fig, master=parent)
        self.widget = self._mpl.get_tk_widget()

    def update(self, results: Dict) -> None:
        self._ax.clear()
        self._ax.set_facecolor("#1a1a2e")

        groups: Dict[str, list] = {}
        for r in results.values():
            if r.position is not None:
                groups.setdefault(r.confidence_label, []).append(r)

        for label, hex_color in _HEX.items():
            grp = groups.get(label, [])
            if not grp:
                continue
            pts   = np.array([r.position   for r in grp])
            confs = np.array([r.confidence for r in grp])

            # Halos
            halo_s = (1.0 - confs) ** 1.2 * 500 + 15
            self._ax.scatter(
                pts[:, 0], pts[:, 1], pts[:, 2],
                c=hex_color, s=halo_s, alpha=0.13,
                linewidths=0, depthshade=False,
            )
            # Centre dots
            self._ax.scatter(
                pts[:, 0], pts[:, 1], pts[:, 2],
                c=hex_color, s=14, alpha=0.92,
                linewidths=0.5, edgecolors="white",
                label=label, depthshade=True,
            )

        self._ax.set_xlabel("X", fontsize=7, color="white")
        self._ax.set_ylabel("Y", fontsize=7, color="white")
        self._ax.set_zlabel("Z", fontsize=7, color="white")
        self._ax.tick_params(labelsize=6, colors="white")
        if groups:
            self._ax.legend(fontsize=7, loc="upper left",
                            facecolor="#2a2a3e", labelcolor="white")
        self._mpl.draw()

    def reset_view(self) -> None:
        self._ax.view_init(elev=25, azim=45)
        self._mpl.draw()


# ── no-viewer stub ────────────────────────────────────────────────────────────

class DummyViewer:
    backend = "none"

    def __init__(self, parent: tk.Widget):
        self.widget = ttk.Label(
            parent,
            text=(
                "No 3D viewer available.\n\n"
                "Install vispy for GPU rendering:\n"
                "  pip install vispy\n\n"
                "Or matplotlib for basic rotation:\n"
                "  pip install matplotlib"
            ),
            justify="center", padding=20,
        )

    def update(self, _results: Dict) -> None:
        pass

    def reset_view(self) -> None:
        pass


# ── factory ───────────────────────────────────────────────────────────────────

def make_viewer(parent: tk.Widget) -> "VispyViewer | MatplotlibViewer | DummyViewer":
    """
    Return the best available 3D viewer widget.

    Priority: vispy (GPU) → matplotlib (software) → text stub.
    The viewer's `.widget` attribute is the tkinter widget to pack.
    The viewer's `.backend` string identifies which engine is running.
    """
    if _VISPY_OK:
        try:
            v = VispyViewer(parent)
            log.info("3D viewer: vispy GPU (TurntableCamera)")
            return v
        except Exception as e:
            log.warning("vispy canvas init failed (%s) — falling back", e)
    if _MPL_OK:
        log.info("3D viewer: matplotlib software renderer")
        return MatplotlibViewer(parent)
    return DummyViewer(parent)
