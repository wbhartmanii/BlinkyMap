"""
BlinkyMap GUI — tkinter wizard with 5 tabs:
  1. Controller  — FPP/E1.31 setup
  2. Cameras     — add USB / IP cameras, live preview
  3. Calibrate   — simple (baseline + FOV) or checkerboard
  4. Scan        — run the pixel-by-pixel scan
  5. Results     — 3D viewer + export
"""

import logging
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Dict, List, Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)

# ── lazy imports so the GUI opens even if opencv isn't installed ───────────────
try:
    from PIL import Image, ImageTk
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    _MPL_OK = True
except ImportError:
    _MPL_OK = False

from .capture import (Camera, CameraManager, LEDDetector, PixelCapture,
                       capture_backgrounds, scan_pixel)
from .controller import ControllerConfig, PixelController
from .export import export_all
from .triangulate import (CameraCalib, SimpleStereoConfig, build_simple_stereo,
                           normalize_positions, remove_outliers,
                           triangulate_all)


# ── helpers ───────────────────────────────────────────────────────────────────

def _frame_to_tk(frame: np.ndarray, max_w: int = 320, max_h: int = 240):
    """Convert BGR numpy frame to a PhotoImage scaled to max dimensions."""
    if not _PIL_OK:
        return None
    h, w = frame.shape[:2]
    scale = min(max_w / w, max_h / h, 1.0)
    nw, nh = int(w * scale), int(h * scale)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(rgb).resize((nw, nh), Image.LANCZOS)
    return ImageTk.PhotoImage(img)


def _section(parent, text: str) -> ttk.LabelFrame:
    lf = ttk.LabelFrame(parent, text=text, padding=6)
    lf.pack(fill="x", padx=8, pady=4)
    return lf


def _row(parent, label: str, widget_factory, **kw):
    """Helper: label on left, widget on right in a frame row."""
    f = ttk.Frame(parent)
    f.pack(fill="x", pady=2)
    ttk.Label(f, text=label, width=22, anchor="w").pack(side="left")
    w = widget_factory(f, **kw)
    w.pack(side="left", fill="x", expand=True)
    return w


# ── per-tab frames ─────────────────────────────────────────────────────────────

class ControllerTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app

        sec = _section(self, "Controller / FPP")
        self.host_var = tk.StringVar(value="192.168.1.100")
        self.universe_var = tk.IntVar(value=1)
        self.start_ch_var = tk.IntVar(value=1)
        self.pixel_count_var = tk.IntVar(value=100)
        self.delay_var = tk.DoubleVar(value=0.15)

        _row(sec, "FPP / Controller IP:", ttk.Entry, textvariable=self.host_var)
        _row(sec, "Universe (sACN):", ttk.Spinbox,
             from_=1, to=63999, textvariable=self.universe_var, width=8)
        _row(sec, "Start channel (1-based):", ttk.Spinbox,
             from_=1, to=512, textvariable=self.start_ch_var, width=8)
        _row(sec, "Pixel count:", ttk.Spinbox,
             from_=1, to=10000, textvariable=self.pixel_count_var, width=8)
        _row(sec, "Capture delay (s):", ttk.Spinbox,
             from_=0.05, to=2.0, increment=0.05,
             textvariable=self.delay_var, width=8)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=8, pady=6)
        ttk.Button(btn_frame, text="Test Connection",
                   command=self._test).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Quick Flash (5 pixels)",
                   command=self._flash).pack(side="left", padx=4)

        self.status_var = tk.StringVar(value="Not connected")
        ttk.Label(self, textvariable=self.status_var,
                  foreground="gray").pack(padx=8, pady=4, anchor="w")

    def _make_controller(self) -> PixelController:
        cfg = ControllerConfig(
            host=self.host_var.get(),
            universe=self.universe_var.get(),
            start_channel=self.start_ch_var.get(),
            pixel_count=self.pixel_count_var.get(),
            inter_pixel_delay=self.delay_var.get(),
        )
        ctrl = PixelController(cfg)
        ctrl.connect()
        return ctrl

    def _test(self):
        self.status_var.set("Connecting…")
        self.update()
        try:
            ctrl = self._make_controller()
            info = ctrl.connect()
            fpp_str = f"FPP {info['fpp_version']}" if info["fpp_detected"] else "FPP not found (direct E1.31)"
            self.status_var.set(f"OK — {fpp_str}")
            self.app.controller = ctrl
        except Exception as e:
            self.status_var.set(f"Error: {e}")
            log.exception("Controller test failed")

    def _flash(self):
        try:
            ctrl = self._make_controller()
            self.app.controller = ctrl
            threading.Thread(target=ctrl.test_pattern, daemon=True).start()
            self.status_var.set("Flashing 5 pixels…")
        except Exception as e:
            self.status_var.set(f"Error: {e}")

    def get_config(self) -> ControllerConfig:
        return ControllerConfig(
            host=self.host_var.get(),
            universe=self.universe_var.get(),
            start_channel=self.start_ch_var.get(),
            pixel_count=self.pixel_count_var.get(),
            inter_pixel_delay=self.delay_var.get(),
        )


class CamerasTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.mgr = CameraManager()
        self._preview_running = False
        self._preview_job = None

        # ── add camera controls ───────────────────────────────────────────────
        add_sec = _section(self, "Add Camera")

        usb_f = ttk.Frame(add_sec)
        usb_f.pack(fill="x", pady=2)
        ttk.Label(usb_f, text="USB index:", width=12).pack(side="left")
        self.usb_idx = tk.IntVar(value=0)
        ttk.Spinbox(usb_f, from_=0, to=10, textvariable=self.usb_idx,
                    width=5).pack(side="left", padx=4)
        ttk.Button(usb_f, text="Add USB Camera",
                   command=self._add_usb).pack(side="left", padx=4)
        ttk.Button(usb_f, text="Scan USB Cameras",
                   command=self._scan_usb).pack(side="left", padx=4)

        ip_f = ttk.Frame(add_sec)
        ip_f.pack(fill="x", pady=2)
        ttk.Label(ip_f, text="IP/RTSP URL:", width=12).pack(side="left")
        self.ip_url = tk.StringVar(value="rtsp://")
        ttk.Entry(ip_f, textvariable=self.ip_url, width=40).pack(side="left", padx=4)
        ttk.Button(ip_f, text="Add IP Camera",
                   command=self._add_ip).pack(side="left", padx=4)

        # ── camera list ───────────────────────────────────────────────────────
        list_sec = _section(self, "Connected Cameras")
        cols = ("id", "label", "resolution", "status")
        self.tree = ttk.Treeview(list_sec, columns=cols, show="headings", height=4)
        for c, w in zip(cols, (80, 160, 110, 100)):
            self.tree.heading(c, text=c.title())
            self.tree.column(c, width=w)
        self.tree.pack(fill="x")
        ttk.Button(list_sec, text="Remove Selected",
                   command=self._remove).pack(anchor="e", pady=2)

        # ── live preview ──────────────────────────────────────────────────────
        prev_sec = _section(self, "Live Preview")
        self.preview_label = ttk.Label(prev_sec,
                                        text="Select a camera and click preview")
        self.preview_label.pack()
        btn_f = ttk.Frame(prev_sec)
        btn_f.pack()
        ttk.Button(btn_f, text="Start Preview",
                   command=self._start_preview).pack(side="left", padx=4)
        ttk.Button(btn_f, text="Stop Preview",
                   command=self._stop_preview).pack(side="left", padx=4)
        self._tk_img = None  # hold reference to prevent GC

    def _add_usb(self):
        idx = self.usb_idx.get()
        cam = self.mgr.add_usb(idx)
        ok = cam.open()
        self._refresh_list()
        if not ok:
            messagebox.showwarning("Camera", f"Could not open USB camera {idx}")

    def _scan_usb(self):
        found = CameraManager.list_usb_cameras()
        messagebox.showinfo("USB Scan", f"Found USB cameras at indices: {found or 'none'}")

    def _add_ip(self):
        url = self.ip_url.get().strip()
        if not url or url == "rtsp://":
            messagebox.showwarning("Camera", "Enter a valid URL")
            return
        cam = self.mgr.add_ip(url)
        ok = cam.open()
        self._refresh_list()
        if not ok:
            messagebox.showwarning("Camera", f"Could not open: {url}")

    def _remove(self):
        sel = self.tree.selection()
        if not sel:
            return
        cam_id = self.tree.item(sel[0])["values"][0]
        self.mgr.remove(str(cam_id))
        self._refresh_list()

    def _refresh_list(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for cam in self.mgr.cameras():
            res = f"{cam.info.width}×{cam.info.height}" if cam.info.width else "?"
            status = "open" if cam.is_open else "error"
            self.tree.insert("", "end",
                             values=(cam.info.cam_id, cam.info.label, res, status))
        self.app.camera_mgr = self.mgr

    def _start_preview(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Preview", "Select a camera row first")
            return
        cam_id = str(self.tree.item(sel[0])["values"][0])
        cam = self.mgr.get(cam_id)
        if cam is None or not cam.is_open:
            return
        self._preview_running = True
        self._preview_cam = cam
        self._tick_preview()

    def _stop_preview(self):
        self._preview_running = False
        if self._preview_job:
            self.after_cancel(self._preview_job)
        self.preview_label.configure(image="",
                                      text="Preview stopped")

    def _tick_preview(self):
        if not self._preview_running:
            return
        frame = self._preview_cam.read()
        if frame is not None and _PIL_OK:
            tk_img = _frame_to_tk(frame, 400, 300)
            if tk_img:
                self._tk_img = tk_img
                self.preview_label.configure(image=tk_img, text="")
        self._preview_job = self.after(66, self._tick_preview)  # ~15 fps


class CalibrateTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.calibrations: Dict[str, CameraCalib] = {}

        mode_sec = _section(self, "Calibration Mode")
        self.mode_var = tk.StringVar(value="simple")
        ttk.Radiobutton(mode_sec, text="Simple (baseline + FOV)",
                         variable=self.mode_var, value="simple",
                         command=self._on_mode).pack(anchor="w")
        ttk.Radiobutton(mode_sec, text="Checkerboard (accurate)",
                         variable=self.mode_var, value="checker",
                         command=self._on_mode).pack(anchor="w")

        # ── simple panel ──────────────────────────────────────────────────────
        self.simple_frame = ttk.Frame(self)
        sf = _section(self.simple_frame, "Simple Stereo Setup")
        note = ("Place Camera 1 at the left, Camera 2 at the right.\n"
                "Measure the horizontal distance between them.\n"
                "Both should face the tree at the same height.")
        ttk.Label(sf, text=note, justify="left",
                   foreground="gray").pack(anchor="w", pady=4)

        self.baseline_var = tk.DoubleVar(value=1.5)
        self.fov_var = tk.DoubleVar(value=70.0)
        self.height_offset_var = tk.DoubleVar(value=0.0)

        _row(sf, "Baseline (metres):", ttk.Spinbox,
             from_=0.1, to=20.0, increment=0.1,
             textvariable=self.baseline_var, width=10)
        _row(sf, "Horiz. FOV (degrees):", ttk.Spinbox,
             from_=20, to=170, increment=1,
             textvariable=self.fov_var, width=10)
        _row(sf, "Cam2 height offset (m):", ttk.Spinbox,
             from_=-5.0, to=5.0, increment=0.1,
             textvariable=self.height_offset_var, width=10)

        ttk.Label(sf, text="Camera 1 ID:", width=22).pack(anchor="w")
        self.cam1_combo = ttk.Combobox(sf, width=20, state="readonly")
        self.cam1_combo.pack(anchor="w", pady=2)
        ttk.Label(sf, text="Camera 2 ID:", width=22).pack(anchor="w")
        self.cam2_combo = ttk.Combobox(sf, width=20, state="readonly")
        self.cam2_combo.pack(anchor="w", pady=2)

        self.simple_frame.pack(fill="x")

        # ── checker panel ─────────────────────────────────────────────────────
        self.checker_frame = ttk.Frame(self)
        cf = _section(self.checker_frame, "Checkerboard Calibration")
        ttk.Label(cf, text="Print an asymmetric checkerboard (default 9×6).\n"
                            "Move it to 10+ positions for each camera.\n"
                            "Click 'Capture' while checkerboard is visible.",
                   justify="left", foreground="gray").pack(anchor="w", pady=4)

        self.checker_rows_var = tk.IntVar(value=9)
        self.checker_cols_var = tk.IntVar(value=6)
        self.checker_sq_var = tk.DoubleVar(value=0.025)
        _row(cf, "Inner corners (rows):", ttk.Spinbox,
             from_=3, to=20, textvariable=self.checker_rows_var, width=6)
        _row(cf, "Inner corners (cols):", ttk.Spinbox,
             from_=3, to=20, textvariable=self.checker_cols_var, width=6)
        _row(cf, "Square size (metres):", ttk.Entry,
             textvariable=self.checker_sq_var)

        self._checker_imgs_1: list = []
        self._checker_imgs_2: list = []
        self.checker_count_var = tk.StringVar(value="0 pairs captured")
        btn_cf = ttk.Frame(cf)
        btn_cf.pack(fill="x", pady=4)
        ttk.Button(btn_cf, text="Capture Pair",
                   command=self._capture_checker_pair).pack(side="left", padx=4)
        ttk.Button(btn_cf, text="Clear",
                   command=self._clear_checker).pack(side="left", padx=4)
        ttk.Label(cf, textvariable=self.checker_count_var).pack(anchor="w")

        # ── apply button ──────────────────────────────────────────────────────
        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", padx=8, pady=8)
        ttk.Button(btn_row, text="Apply Calibration",
                   command=self._apply).pack(side="left", padx=4)
        self.calib_status = tk.StringVar(value="Not calibrated")
        ttk.Label(btn_row, textvariable=self.calib_status,
                   foreground="gray").pack(side="left", padx=8)

        self._on_mode()

    def _on_mode(self):
        if self.mode_var.get() == "simple":
            self.checker_frame.pack_forget()
            self.simple_frame.pack(fill="x")
        else:
            self.simple_frame.pack_forget()
            self.checker_frame.pack(fill="x")
        self._refresh_combos()

    def _refresh_combos(self):
        mgr = self.app.camera_mgr
        ids = [c.info.cam_id for c in mgr.cameras()] if mgr else []
        self.cam1_combo["values"] = ids
        self.cam2_combo["values"] = ids
        if len(ids) >= 1:
            self.cam1_combo.current(0)
        if len(ids) >= 2:
            self.cam2_combo.current(1)

    def _apply(self):
        self._refresh_combos()
        mgr = self.app.camera_mgr
        if not mgr or len(mgr) < 2:
            messagebox.showwarning("Calibrate",
                                   "Add at least 2 cameras first")
            return
        try:
            if self.mode_var.get() == "simple":
                self._apply_simple(mgr)
            else:
                self._apply_checker(mgr)
        except Exception as e:
            messagebox.showerror("Calibrate", str(e))
            log.exception("Calibration error")

    def _apply_simple(self, mgr):
        c1_id = self.cam1_combo.get()
        c2_id = self.cam2_combo.get()
        if not c1_id or not c2_id or c1_id == c2_id:
            raise ValueError("Select two different cameras")
        c1 = mgr.get(c1_id)
        c2 = mgr.get(c2_id)
        cfg = SimpleStereoConfig(
            cam1_id=c1_id, cam2_id=c2_id,
            cam1_width=c1.info.width, cam1_height=c1.info.height,
            cam2_width=c2.info.width, cam2_height=c2.info.height,
            baseline_m=self.baseline_var.get(),
            hfov_deg=self.fov_var.get(),
            cam2_height_offset_m=self.height_offset_var.get(),
        )
        self.calibrations = build_simple_stereo(cfg)
        self.app.calibrations = self.calibrations
        self.calib_status.set(
            f"Simple stereo: {c1_id} ↔ {c2_id}  baseline={cfg.baseline_m}m  FOV={cfg.hfov_deg}°"
        )

    def _capture_checker_pair(self):
        mgr = self.app.camera_mgr
        if not mgr or len(mgr) < 2:
            messagebox.showwarning("Calibrate", "Need 2 cameras")
            return
        cams = mgr.cameras()
        f1 = cams[0].read()
        f2 = cams[1].read()
        if f1 is None or f2 is None:
            messagebox.showwarning("Calibrate", "Could not read frames")
            return
        self._checker_imgs_1.append(f1)
        self._checker_imgs_2.append(f2)
        n = len(self._checker_imgs_1)
        self.checker_count_var.set(f"{n} pair(s) captured")

    def _clear_checker(self):
        self._checker_imgs_1.clear()
        self._checker_imgs_2.clear()
        self.checker_count_var.set("0 pairs captured")

    def _apply_checker(self, mgr):
        from .triangulate import calibrate_stereo_pair, CheckerboardConfig
        if len(self._checker_imgs_1) < 4:
            raise ValueError("Capture at least 4 checkerboard pairs first")
        cams = mgr.cameras()
        cfg = CheckerboardConfig(
            rows=self.checker_rows_var.get(),
            cols=self.checker_cols_var.get(),
            square_size_m=self.checker_sq_var.get(),
        )
        self.calibrations = calibrate_stereo_pair(
            self._checker_imgs_1, self._checker_imgs_2, cfg,
            cam1_id=cams[0].info.cam_id,
            cam2_id=cams[1].info.cam_id,
        )
        self.app.calibrations = self.calibrations
        self.calib_status.set(
            f"Checkerboard: {len(self._checker_imgs_1)} pairs — calibrated OK"
        )


class ScanTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._q: queue.Queue = queue.Queue()

        ctrl_sec = _section(self, "Scan Controls")
        btn_row = ttk.Frame(ctrl_sec)
        btn_row.pack(fill="x", pady=4)
        self.start_btn = ttk.Button(btn_row, text="▶  Start Scan",
                                     command=self._start)
        self.start_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(btn_row, text="⏹  Stop",
                                    command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        prog_sec = _section(self, "Progress")
        self.progress_var = tk.DoubleVar(value=0)
        self.progress = ttk.Progressbar(prog_sec,
                                         variable=self.progress_var,
                                         maximum=100)
        self.progress.pack(fill="x")
        self.progress_label = tk.StringVar(value="Ready")
        ttk.Label(prog_sec, textvariable=self.progress_label).pack(anchor="w")

        log_sec = _section(self, "Log")
        self.log_box = scrolledtext.ScrolledText(log_sec, height=10, state="disabled",
                                                  font=("Courier", 9))
        self.log_box.pack(fill="both", expand=True)

    def _log(self, msg: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _start(self):
        mgr = self.app.camera_mgr
        calibs = self.app.calibrations
        if not mgr or len(mgr) < 2:
            messagebox.showwarning("Scan", "Add at least 2 cameras first")
            return
        if not calibs:
            messagebox.showwarning("Scan", "Calibrate cameras first")
            return

        ctrl_cfg = self.app.ctrl_tab.get_config()
        self.app.controller = PixelController(ctrl_cfg)
        try:
            info = self.app.controller.connect()
            self._log(f"Controller: {info['host']}, FPP={info['fpp_detected']}")
        except Exception as e:
            messagebox.showerror("Scan", f"Cannot connect to controller:\n{e}")
            return

        self._running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.app.scan_results = []

        self._thread = threading.Thread(target=self._scan_worker,
                                         args=(ctrl_cfg, mgr, calibs),
                                         daemon=True)
        self._thread.start()
        self._poll()

    def _stop(self):
        self._running = False

    def _scan_worker(self, ctrl_cfg: ControllerConfig,
                     mgr: CameraManager,
                     calibs: Dict[str, CameraCalib]):
        ctrl = PixelController(ctrl_cfg)
        ctrl.connect()
        try:
            cameras = [c for c in mgr.cameras() if c.is_open]
            detector = LEDDetector()
            n = ctrl_cfg.pixel_count
            delay = ctrl_cfg.inter_pixel_delay

            self._q.put(("log", "Capturing background frames…"))
            ctrl.blackout()
            time.sleep(0.3)
            backgrounds = capture_backgrounds(cameras)
            self._q.put(("log", f"Background captured for {len(backgrounds)} camera(s)"))

            captures: List[PixelCapture] = []
            for i in range(n):
                if not self._running:
                    self._q.put(("log", "Scan stopped by user"))
                    break
                ctrl.light_pixel(i)
                time.sleep(delay)
                cap = scan_pixel(i, cameras, backgrounds, detector)
                ctrl.blackout()
                captures.append(cap)

                pct = (i + 1) / n * 100
                det_count = cap.camera_count
                self._q.put(("progress", pct, i, n, det_count))

            self._q.put(("log", f"Scan complete: {len(captures)} pixels"))
            self._q.put(("done", captures))
        except Exception as e:
            self._q.put(("error", str(e)))
            log.exception("Scan worker error")
        finally:
            ctrl.disconnect()

    def _poll(self):
        try:
            while True:
                msg = self._q.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, pct, i, n, det = msg
                    self.progress_var.set(pct)
                    self.progress_label.set(
                        f"Pixel {i+1}/{n} — detected in {det} camera(s)"
                    )
                elif kind == "log":
                    self._log(msg[1])
                elif kind == "done":
                    self._scan_done(msg[1])
                    return
                elif kind == "error":
                    messagebox.showerror("Scan Error", msg[1])
                    self._scan_done([])
                    return
        except queue.Empty:
            pass
        if self._running:
            self.after(100, self._poll)

    def _scan_done(self, captures):
        self._running = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        if captures:
            self.app.scan_results = captures
            self._log("Triangulating…")
            positions = triangulate_all(captures, self.app.calibrations)
            positions = remove_outliers(positions)
            positions = normalize_positions(positions)
            self.app.positions = positions
            self._log(f"Triangulated {len(positions)} pixel positions")
            self.app.notebook.select(4)   # jump to Results tab
        else:
            self._log("No captures — nothing to triangulate")


class ResultsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app

        # ── 3D viewer ─────────────────────────────────────────────────────────
        view_sec = _section(self, "3D Point Cloud")
        if _MPL_OK:
            self.fig = Figure(figsize=(5, 4), dpi=90)
            self.ax = self.fig.add_subplot(111, projection="3d")
            self.canvas = FigureCanvasTkAgg(self.fig, master=view_sec)
            self.canvas.get_tk_widget().pack(fill="both", expand=True)
        else:
            ttk.Label(view_sec,
                       text="Install matplotlib for 3D preview").pack()

        ttk.Button(view_sec, text="Refresh View",
                   command=self._refresh_view).pack(pady=4)

        # ── stats ─────────────────────────────────────────────────────────────
        stat_sec = _section(self, "Statistics")
        self.stats_var = tk.StringVar(value="No results yet")
        ttk.Label(stat_sec, textvariable=self.stats_var,
                   justify="left").pack(anchor="w")

        # ── export ────────────────────────────────────────────────────────────
        exp_sec = _section(self, "Export")
        self.model_name_var = tk.StringVar(value="BlinkyTree")
        _row(exp_sec, "Model name:", ttk.Entry, textvariable=self.model_name_var)

        btn_row = ttk.Frame(exp_sec)
        btn_row.pack(fill="x", pady=4)
        ttk.Button(btn_row, text="Export xModel + CSV",
                   command=self._export).pack(side="left", padx=4)
        self.export_status = tk.StringVar(value="")
        ttk.Label(exp_sec, textvariable=self.export_status,
                   foreground="green").pack(anchor="w")

    def _refresh_view(self):
        positions = self.app.positions
        if not positions:
            messagebox.showinfo("Results", "No positions yet — run a scan first")
            return

        pts = np.array([positions[k] for k in sorted(positions)])
        n = len(pts)

        if _MPL_OK:
            self.ax.clear()
            sc = self.ax.scatter(pts[:, 0], pts[:, 1], pts[:, 2],
                                  c=np.arange(n), cmap="rainbow",
                                  s=10, alpha=0.8)
            self.ax.set_xlabel("X")
            self.ax.set_ylabel("Y (height)")
            self.ax.set_zlabel("Z")
            self.ax.set_title(f"BlinkyMap — {n} pixels")
            self.canvas.draw()

        # Stats
        y = pts[:, 1]
        tree_h = y.max() - y.min()
        r = np.sqrt(pts[:, 0] ** 2 + pts[:, 2] ** 2)
        self.stats_var.set(
            f"Pixels: {n}   |   "
            f"Tree height: {tree_h:.2f} m   |   "
            f"Max radius: {r.max():.2f} m"
        )

    def _export(self):
        positions = self.app.positions
        if not positions:
            messagebox.showwarning("Export", "No data to export")
            return

        out_dir = filedialog.askdirectory(title="Select export folder")
        if not out_dir:
            return

        name = self.model_name_var.get().strip() or "BlinkyTree"
        try:
            cfg = self.app.ctrl_tab.get_config()
            paths = export_all(positions, out_dir, name,
                               total_pixels=cfg.pixel_count)
            msg = "Exported:\n" + "\n".join(f"  {k}: {v.name}" for k, v in paths.items())
            self.export_status.set(f"Saved to {out_dir}")
            messagebox.showinfo("Export Complete", msg)
        except Exception as e:
            messagebox.showerror("Export Error", str(e))
            log.exception("Export error")


# ── Main application ──────────────────────────────────────────────────────────

class BlinkyMapApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("BlinkyMap — Pixel Tree 3D Mapper")
        self.root.minsize(600, 580)

        # ── shared state ──────────────────────────────────────────────────────
        self.controller: Optional[PixelController] = None
        self.camera_mgr: CameraManager = CameraManager()
        self.calibrations: Dict[str, CameraCalib] = {}
        self.scan_results: List[PixelCapture] = []
        self.positions: Dict[int, np.ndarray] = {}

        # ── header ────────────────────────────────────────────────────────────
        hdr = ttk.Frame(self.root, relief="groove", padding=6)
        hdr.pack(fill="x", padx=4, pady=(4, 0))
        ttk.Label(hdr, text="BlinkyMap",
                   font=("", 14, "bold")).pack(side="left")
        ttk.Label(hdr, text="  Pixel Tree → xLights 3D Model",
                   foreground="gray").pack(side="left")

        # ── notebook ──────────────────────────────────────────────────────────
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=4, pady=4)

        self.ctrl_tab = ControllerTab(self.notebook, self)
        self.cam_tab = CamerasTab(self.notebook, self)
        self.calib_tab = CalibrateTab(self.notebook, self)
        self.scan_tab = ScanTab(self.notebook, self)
        self.results_tab = ResultsTab(self.notebook, self)

        for tab, name in [(self.ctrl_tab,    "1. Controller"),
                          (self.cam_tab,     "2. Cameras"),
                          (self.calib_tab,   "3. Calibrate"),
                          (self.scan_tab,    "4. Scan"),
                          (self.results_tab, "5. Results")]:
            self.notebook.add(tab, text=name)

        # ── status bar ────────────────────────────────────────────────────────
        status_bar = ttk.Frame(self.root, relief="sunken", padding=2)
        status_bar.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(status_bar, textvariable=self.status_var,
                   anchor="w").pack(fill="x")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        if self.controller:
            try:
                self.controller.disconnect()
            except Exception:
                pass
        if self.camera_mgr:
            self.camera_mgr.close_all()
        self.root.destroy()

    def run(self):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        self.root.mainloop()
