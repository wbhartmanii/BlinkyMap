"""
Export pixel 3D positions to formats xLights can consume.

Outputs:
  - CSV  (Channel, X, Y, Z) — importable via xLights Custom Model Creator
  - .xmodel — standard xLights custom model XML with cylindrical-unwrap grid
  - .ply   — ASCII point cloud for inspection in MeshLab / Blender etc.
"""

import csv
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np


# ── CSV export ────────────────────────────────────────────────────────────────

def export_csv(positions: Dict[int, np.ndarray],
               filepath: str | Path,
               total_pixels: Optional[int] = None) -> Path:
    """
    Write Channel,X,Y,Z CSV.

    Channel numbers are 1-based to match xLights convention.
    Pixels not detected are omitted (or written with NaN if total_pixels given).
    """
    filepath = Path(filepath)
    known = sorted(positions.keys())
    all_indices = range(total_pixels) if total_pixels else known

    with open(filepath, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Channel", "X", "Y", "Z"])
        for idx in all_indices:
            ch = idx + 1  # 1-based
            if idx in positions:
                x, y, z = positions[idx]
                w.writerow([ch, f"{x:.4f}", f"{y:.4f}", f"{z:.4f}"])
            # else: skip — xLights treats absent channels as unmapped
    return filepath


# ── PLY export ────────────────────────────────────────────────────────────────

def export_ply(positions: Dict[int, np.ndarray], filepath: str | Path) -> Path:
    """ASCII PLY point cloud — useful for visual inspection."""
    filepath = Path(filepath)
    pts = [(idx, *positions[idx]) for idx in sorted(positions)]
    with open(filepath, "w") as f:
        f.write("ply\nformat ascii 1.0\n")
        f.write(f"element vertex {len(pts)}\n")
        f.write("property float x\nproperty float y\nproperty float z\n")
        f.write("property int channel\n")
        f.write("end_header\n")
        for idx, x, y, z in pts:
            f.write(f"{x:.4f} {y:.4f} {z:.4f} {idx + 1}\n")
    return filepath


# ── xLights .xmodel export ────────────────────────────────────────────────────

def _cylindrical_unwrap(positions: Dict[int, np.ndarray],
                         grid_w: int, grid_h: int) -> Dict[Tuple[int, int], int]:
    """
    Map 3D pixel positions onto a 2D grid via cylindrical projection.

    Columns  → angle around the tree (0 … 360°)
    Rows     → normalised height   (bottom = grid_h-1, top = 0)

    Returns {(row, col): channel_number (1-based)}.
    If two pixels land in the same cell the later one wins (rare in practice).
    """
    if not positions:
        return {}

    pts = np.array([positions[k] for k in sorted(positions)])
    indices = sorted(positions.keys())

    # Centroid in X-Z for angle reference
    cx = (pts[:, 0].max() + pts[:, 0].min()) / 2
    cz = (pts[:, 2].max() + pts[:, 2].min()) / 2

    # Y extents for height mapping
    y_min, y_max = pts[:, 1].min(), pts[:, 1].max()
    y_range = y_max - y_min if y_max != y_min else 1.0

    grid: Dict[Tuple[int, int], int] = {}
    for pixel_idx in indices:
        x, y, z = positions[pixel_idx]
        angle = math.atan2(x - cx, z - cz)          # -π … π
        norm_angle = (angle + math.pi) / (2 * math.pi)   # 0 … 1
        norm_height = (y - y_min) / y_range               # 0 … 1

        col = min(int(norm_angle * grid_w), grid_w - 1)
        row = min(int((1.0 - norm_height) * grid_h), grid_h - 1)  # top = row 0

        grid[(row, col)] = pixel_idx + 1  # 1-based channel

    return grid


def _grid_to_string(grid: Dict[Tuple[int, int], int],
                    grid_w: int, grid_h: int) -> str:
    """Encode grid as xLights CustomModel string (rows separated by ';')."""
    rows = []
    for r in range(grid_h):
        row_vals = []
        for c in range(grid_w):
            row_vals.append(str(grid.get((r, c), 0)))
        rows.append(",".join(row_vals))
    return ";".join(rows)


def export_xmodel(positions: Dict[int, np.ndarray],
                   filepath: str | Path,
                   model_name: str = "BlinkyMap",
                   grid_w: Optional[int] = None,
                   grid_h: Optional[int] = None,
                   source_version: str = "2024.15") -> Path:
    """
    Write an xLights .xmodel file.

    The model uses a cylindrical projection grid so effects like
    Meteor Shower, Pinwheel, and spirals work correctly.

    xLights import: Layout → right-click → Import Model → select .xmodel
    """
    filepath = Path(filepath)
    n = len(positions)
    if n == 0:
        raise ValueError("No pixel positions to export")

    # Auto-size grid: aim for ~16:9 aspect, min cell density
    if grid_w is None or grid_h is None:
        grid_w = max(16, int(math.ceil(math.sqrt(n * 2))))
        grid_h = max(8,  int(math.ceil(n / grid_w)) + 2)

    grid = _cylindrical_unwrap(positions, grid_w, grid_h)
    custom_model_str = _grid_to_string(grid, grid_w, grid_h)

    attrib = {
        "name": model_name,
        "parm1": str(grid_w),
        "parm2": str(grid_h),
        "StringType": "RGB Nodes",
        "Transparency": "0",
        "PixelSize": "2",
        "ModelBrightness": "",
        "Antialias": "1",
        "StrandNames": "",
        "NodeNames": "",
        "CustomModel": custom_model_str,
        "SourceVersion": source_version,
        "Description": f"Generated by BlinkyMap ({n} pixels)",
    }
    root = ET.Element("custommodel", attrib)
    tree = ET.ElementTree(root)

    # Pretty-print (Python 3.9+)
    try:
        ET.indent(tree, space="  ")
    except AttributeError:
        pass

    tree.write(str(filepath), encoding="unicode", xml_declaration=False)
    return filepath


# ── Bundle export ─────────────────────────────────────────────────────────────

def export_all(positions: Dict[int, np.ndarray],
               output_dir: str | Path,
               model_name: str = "BlinkyMap",
               total_pixels: Optional[int] = None) -> Dict[str, Path]:
    """Export CSV, xmodel, and PLY to output_dir. Returns paths dict."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    paths = {
        "csv":    export_csv(positions, out / f"{model_name}.csv", total_pixels),
        "xmodel": export_xmodel(positions, out / f"{model_name}.xmodel", model_name),
        "ply":    export_ply(positions, out / f"{model_name}.ply"),
    }
    return paths
