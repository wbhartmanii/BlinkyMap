"""
BlinkyMap GUI — single-camera, multi-session workflow.

Tabs:
  1. Controller  — FPP / E1.31 setup
  2. Camera      — one camera (USB or IP), intrinsic calibration
  3. Sessions    — run scans, accumulate sessions, live 3D confidence view
  4. Export      — save xModel / CSV when you're happy
"""

import logging
import math
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
from typing import Dict, List, Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)

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
from .triangulate import (
    CameraCalib, CheckerboardConfig, ModelConfidence, PixelResult,
    ScanSession, build_session_calib, calibrate_camera_intrinsics,
    compute_model_confidence, extract_positions, locate_session_pnp,
    _make_K, normalize_positions, remove_outliers, triangulate_sessions,
)


# ── UI helpers ────────────────────────────────────────────────────────────────

CONF_COLORS = {          # confidence label → hex colour for matplotlib scatter
    "high":   "#22c55e",  # green
    "medium": "#eab308",  # yellow
    "low":    "#ef4444",  # red
    "unseen": "#6b7280",  # grey
}


def _section(parent, text: str) -> ttk.LabelFrame:
    lf = ttk.LabelFrame(parent, text=text, padding=6)
    lf.pack(fill="x", padx=8, pady=4)
    return lf


def _row(parent, label: str, widget_factory, **kw):
    f = ttk.Frame(parent)
    f.pack(fill="x", pady=2)
    ttk.Label(f, text=label, width=24, anchor="w").pack(side="left")
    w = widget_factory(f, **kw)
    w.pack(side="left", fill="x", expand=True)
    return w


def _frame_to_tk(frame: np.ndarray, max_w=380, max_h=280):
    if not _PIL_OK:
        return None
    h, w = frame.shape[:2]
    scale = min(max_w / w, max_h / h, 1.0)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(rgb).resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return ImageTk.PhotoImage(img)


# ── Tab 1: Controller ─────────────────────────────────────────────────────────

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

    def get_config(self) -> ControllerConfig:
        return ControllerConfig(
            host=self.host_var.get(),
            universe=self.universe_var.get(),
            start_channel=self.start_ch_var.get(),
            pixel_count=self.pixel_count_var.get(),
            inter_pixel_delay=self.delay_var.get(),
        )

    def _make_ctrl(self) -> PixelController:
        ctrl = PixelController(self.get_config())
        ctrl.connect()
        return ctrl

    def _test(self):
        self.status_var.set("Connecting…")
        self.update()
        try:
            ctrl = self._make_ctrl()
            info = ctrl.connect()
            fpp = f"FPP {info['fpp_version']}" if info["fpp_detected"] else "direct E1.31"
            self.status_var.set(f"OK — {fpp}  |  host={info['host']}")
        except Exception as e:
            self.status_var.set(f"Error: {e}")

    def _flash(self):
        try:
            ctrl = self._make_ctrl()
            threading.Thread(target=ctrl.test_pattern, daemon=True).start()
            self.status_var.set("Flashing…")
        except Exception as e:
            self.status_var.set(f"Error: {e}")


# ── Tab 2: Camera ─────────────────────────────────────────────────────────────

class CameraTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._cam: Optional[Camera] = None
        self._preview_job = None
        self._tk_img = None
        self._checker_imgs: list = []

        # ── source ────────────────────────────────────────────────────────────
        src_sec = _section(self, "Camera Source")
        self.src_type = tk.StringVar(value="usb")
        ttk.Radiobutton(src_sec, text="USB", variable=self.src_type,
                         value="usb", command=self._on_src).pack(side="left")
        ttk.Radiobutton(src_sec, text="IP / RTSP", variable=self.src_type,
                         value="ip",  command=self._on_src).pack(side="left", padx=8)

        self.usb_idx_var = tk.IntVar(value=0)
        self.ip_url_var = tk.StringVar(value="rtsp://")

        usb_row = ttk.Frame(src_sec)
        usb_row.pack(fill="x", pady=2)
        ttk.Label(usb_row, text="USB index:", width=12).pack(side="left")
        ttk.Spinbox(usb_row, from_=0, to=10, textvariable=self.usb_idx_var,
                    width=5).pack(side="left", padx=4)
        ttk.Button(usb_row, text="Scan",
                   command=self._scan_usb).pack(side="left", padx=4)
        self._usb_row = usb_row

        ip_row = ttk.Frame(src_sec)
        ttk.Label(ip_row, text="URL:", width=12).pack(side="left")
        ttk.Entry(ip_row, textvariable=self.ip_url_var, width=38).pack(side="left", padx=4)
        self._ip_row = ip_row

        ttk.Button(src_sec, text="Open Camera",
                   command=self._open_cam).pack(anchor="w", pady=4)
        self.cam_status_var = tk.StringVar(value="No camera opened")
        ttk.Label(src_sec, textvariable=self.cam_status_var,
                   foreground="gray").pack(anchor="w")

        # ── intrinsics ────────────────────────────────────────────────────────
        cal_sec = _section(self, "Intrinsic Calibration")
        self.cal_mode_var = tk.StringVar(value="fov")
        ttk.Radiobutton(cal_sec, text="Use FOV estimate",
                         variable=self.cal_mode_var, value="fov",
                         command=self._on_cal_mode).pack(anchor="w")
        ttk.Radiobutton(cal_sec, text="Checkerboard (more accurate)",
                         variable=self.cal_mode_var, value="checker",
                         command=self._on_cal_mode).pack(anchor="w")

        # FOV entry
        self._fov_frame = ttk.Frame(cal_sec)
        self.fov_var = tk.DoubleVar(value=70.0)
        _row(self._fov_frame, "Horiz. FOV (°):", ttk.Spinbox,
             from_=20, to=170, increment=1, textvariable=self.fov_var, width=8)
        self._fov_frame.pack(fill="x")

        # Checker capture
        self._chk_frame = ttk.Frame(cal_sec)
        self.chk_rows_var = tk.IntVar(value=9)
        self.chk_cols_var = tk.IntVar(value=6)
        self.chk_sq_var  = tk.DoubleVar(value=0.025)
        _row(self._chk_frame, "Inner corners (rows):", ttk.Spinbox,
             from_=3, to=20, textvariable=self.chk_rows_var, width=6)
        _row(self._chk_frame, "Inner corners (cols):", ttk.Spinbox,
             from_=3, to=20, textvariable=self.chk_cols_var, width=6)
        _row(self._chk_frame, "Square size (m):", ttk.Entry,
             textvariable=self.chk_sq_var)
        chk_btn = ttk.Frame(self._chk_frame)
        chk_btn.pack(fill="x", pady=4)
        ttk.Button(chk_btn, text="Capture Frame",
                   command=self._capture_checker).pack(side="left", padx=4)
        ttk.Button(chk_btn, text="Clear",
                   command=self._clear_checker).pack(side="left", padx=4)
        self.chk_count_var = tk.StringVar(value="0 frames")
        ttk.Label(self._chk_frame, textvariable=self.chk_count_var).pack(anchor="w")

        ttk.Button(cal_sec, text="Apply Calibration",
                   command=self._apply_cal).pack(anchor="w", pady=4)
        self.cal_status_var = tk.StringVar(value="Not calibrated")
        ttk.Label(cal_sec, textvariable=self.cal_status_var,
                   foreground="gray").pack(anchor="w")

        # ── live preview ──────────────────────────────────────────────────────
        prev_sec = _section(self, "Live Preview")
        self.preview_lbl = ttk.Label(prev_sec,
                                      text="Open a camera to see preview")
        self.preview_lbl.pack()
        btn_row = ttk.Frame(prev_sec)
        btn_row.pack()
        ttk.Button(btn_row, text="Start",
                   command=self._start_preview).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Stop",
                   command=self._stop_preview).pack(side="left", padx=4)

        self._on_src()

    # ── source switching ──────────────────────────────────────────────────────

    def _on_src(self):
        if self.src_type.get() == "usb":
            self._ip_row.pack_forget()
            self._usb_row.pack(fill="x", pady=2)
        else:
            self._usb_row.pack_forget()
            self._ip_row.pack(fill="x", pady=2)

    def _on_cal_mode(self):
        if self.cal_mode_var.get() == "fov":
            self._chk_frame.pack_forget()
            self._fov_frame.pack(fill="x")
        else:
            self._fov_frame.pack_forget()
            self._chk_frame.pack(fill="x")

    def _scan_usb(self):
        found = CameraManager.list_usb_cameras()
        messagebox.showinfo("USB Scan", f"Available indices: {found or 'none'}")

    # ── camera open ───────────────────────────────────────────────────────────

    def _open_cam(self):
        if self._cam:
            self._cam.close()
        mgr = CameraManager()
        if self.src_type.get() == "usb":
            cam = mgr.add_usb(self.usb_idx_var.get())
        else:
            url = self.ip_url_var.get().strip()
            if not url or url == "rtsp://":
                messagebox.showwarning("Camera", "Enter a valid URL")
                return
            cam = mgr.add_ip(url)
        ok = cam.open()
        if not ok:
            messagebox.showwarning("Camera", "Could not open camera")
            return
        self._cam = cam
        self.app.camera = cam
        self.cam_status_var.set(
            f"Opened: {cam.info.label}  ({cam.info.width}×{cam.info.height})"
        )

    # ── calibration ───────────────────────────────────────────────────────────

    def _capture_checker(self):
        if not self._cam or not self._cam.is_open:
            messagebox.showwarning("Camera", "Open a camera first")
            return
        frame = self._cam.read()
        if frame is None:
            messagebox.showwarning("Camera", "Could not read frame")
            return
        self._checker_imgs.append(frame)
        self.chk_count_var.set(f"{len(self._checker_imgs)} frames")

    def _clear_checker(self):
        self._checker_imgs.clear()
        self.chk_count_var.set("0 frames")

    def _apply_cal(self):
        cam = self._cam
        if not cam:
            messagebox.showwarning("Calibrate", "Open a camera first")
            return
        try:
            if self.cal_mode_var.get() == "fov":
                K = _make_K(cam.info.width, cam.info.height, self.fov_var.get())
                dist = np.zeros(5, dtype=np.float64)
                self.app.camera_K = K
                self.app.camera_dist = dist
                self.cal_status_var.set(
                    f"FOV estimate: {self.fov_var.get()}°  "
                    f"fx={K[0,0]:.0f}px"
                )
            else:
                if len(self._checker_imgs) < 4:
                    messagebox.showwarning("Calibrate", "Capture ≥4 frames first")
                    return
                cfg = CheckerboardConfig(
                    rows=self.chk_rows_var.get(),
                    cols=self.chk_cols_var.get(),
                    square_size_m=self.chk_sq_var.get(),
                )
                K, dist = calibrate_camera_intrinsics(self._checker_imgs, cfg)
                self.app.camera_K = K
                self.app.camera_dist = dist
                self.cal_status_var.set(
                    f"Checkerboard: {len(self._checker_imgs)} frames  "
                    f"fx={K[0,0]:.0f}px"
                )
        except Exception as e:
            messagebox.showerror("Calibrate", str(e))

    # ── preview ───────────────────────────────────────────────────────────────

    def _start_preview(self):
        if not self._cam or not self._cam.is_open:
            messagebox.showinfo("Preview", "Open a camera first")
            return
        self._tick_preview()

    def _stop_preview(self):
        if self._preview_job:
            self.after_cancel(self._preview_job)
            self._preview_job = None
        self.preview_lbl.configure(image="", text="Preview stopped")

    def _tick_preview(self):
        if not self._cam or not self._cam.is_open:
            return
        frame = self._cam.read()
        if frame is not None and _PIL_OK:
            tk_img = _frame_to_tk(frame)
            if tk_img:
                self._tk_img = tk_img
                self.preview_lbl.configure(image=tk_img, text="")
        self._preview_job = self.after(66, self._tick_preview)


# ── Tab 3: Sessions ───────────────────────────────────────────────────────────

class _SessionDialog(tk.Toplevel):
    """Small dialog to enter camera position for a new session."""

    def __init__(self, parent, session_id: int, can_autopnp: bool):
        super().__init__(parent)
        self.title(f"Session {session_id} — Camera Position")
        self.resizable(False, False)
        self.grab_set()
        self.result = None  # (angle, dist, height, use_pnp)

        note = ("Where is the camera?\n"
                "Stand around the tree and estimate your position.")
        ttk.Label(self, text=note, justify="left",
                   padding=8).grid(row=0, column=0, columnspan=2, sticky="w")

        self.angle_var = tk.DoubleVar(value=0.0)
        self.dist_var  = tk.DoubleVar(value=2.0)
        self.height_var = tk.DoubleVar(value=1.2)
        self.pnp_var   = tk.BooleanVar(value=can_autopnp)

        fields = [
            ("Angle (0=front, clockwise °):", self.angle_var,  0,   360, 15),
            ("Distance from tree (m):",        self.dist_var,  0.3,  20, 0.1),
            ("Camera height (m):",             self.height_var, 0.1, 5,  0.1),
        ]
        for r, (lbl, var, lo, hi, inc) in enumerate(fields, start=1):
            ttk.Label(self, text=lbl, anchor="w").grid(
                row=r, column=0, sticky="w", padx=8, pady=3)
            ttk.Spinbox(self, from_=lo, to=hi, increment=inc,
                         textvariable=var, width=10).grid(
                row=r, column=1, padx=8, pady=3)

        if can_autopnp:
            r += 1
            ttk.Checkbutton(
                self,
                text="Auto-locate camera from known pixels (PnP)",
                variable=self.pnp_var,
            ).grid(row=r, column=0, columnspan=2, sticky="w", padx=8, pady=4)
            ttk.Label(self, text="  (No manual position needed if ≥6 pixels already mapped)",
                       foreground="gray").grid(
                row=r+1, column=0, columnspan=2, sticky="w", padx=16)

        ttk.Button(self, text="Run Scan", command=self._ok).grid(
            row=10, column=0, pady=8)
        ttk.Button(self, text="Cancel", command=self.destroy).grid(
            row=10, column=1, pady=8)

        self.wait_window()

    def _ok(self):
        self.result = (
            self.angle_var.get(),
            self.dist_var.get(),
            self.height_var.get(),
            self.pnp_var.get(),
        )
        self.destroy()


class SessionsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._running = False
        self._q: queue.Queue = queue.Queue()

        # ── session list ──────────────────────────────────────────────────────
        list_sec = _section(self, "Scan Sessions")

        cols = ("id", "position", "detected", "coverage", "auto")
        self.tree = ttk.Treeview(list_sec, columns=cols, show="headings", height=5)
        for col, (hdr, w) in zip(cols, [
            ("ID", 40), ("Position", 180), ("Detected", 75),
            ("Coverage", 80), ("Located", 60)
        ]):
            self.tree.heading(col, text=hdr)
            self.tree.column(col, width=w)
        self.tree.pack(fill="x")

        btn_sec = ttk.Frame(self)
        btn_sec.pack(fill="x", padx=8, pady=4)
        self.add_btn = ttk.Button(btn_sec, text="▶  New Session",
                                   command=self._new_session)
        self.add_btn.pack(side="left", padx=4)
        ttk.Button(btn_sec, text="Remove Last",
                   command=self._remove_last).pack(side="left", padx=4)

        # ── confidence gauge ──────────────────────────────────────────────────
        conf_sec = _section(self, "Model Confidence")

        self.conf_pct_var = tk.StringVar(value="—")
        self.conf_grade_var = tk.StringVar(value="Add sessions to begin")
        ttk.Label(conf_sec, textvariable=self.conf_pct_var,
                   font=("", 28, "bold")).pack(anchor="w", padx=4)
        ttk.Label(conf_sec, textvariable=self.conf_grade_var,
                   font=("", 11), foreground="gray").pack(anchor="w", padx=4)

        detail_frame = ttk.Frame(conf_sec)
        detail_frame.pack(fill="x", pady=4)
        self.conf_detail_var = tk.StringVar(value="")
        ttk.Label(detail_frame, textvariable=self.conf_detail_var,
                   justify="left").pack(anchor="w")

        # Color key
        key_frame = ttk.Frame(conf_sec)
        key_frame.pack(anchor="w", pady=2)
        for label, color in [("High", "#22c55e"), ("Medium", "#eab308"),
                              ("Low", "#ef4444"), ("Unseen", "#9ca3af")]:
            dot = tk.Label(key_frame, text="●", foreground=color)
            dot.pack(side="left")
            tk.Label(key_frame, text=f" {label}   ").pack(side="left")

        # ── mini 3D view ──────────────────────────────────────────────────────
        view_sec = _section(self, "Live 3D View  (updates after each session)")
        if _MPL_OK:
            self.fig = Figure(figsize=(5, 3.5), dpi=88)
            self.ax = self.fig.add_subplot(111, projection="3d")
            self.canvas = FigureCanvasTkAgg(self.fig, master=view_sec)
            self.canvas.get_tk_widget().pack(fill="both", expand=True)
        else:
            ttk.Label(view_sec, text="Install matplotlib for 3D preview").pack()

        # ── progress log ──────────────────────────────────────────────────────
        log_sec = _section(self, "Log")
        self.log_box = scrolledtext.ScrolledText(log_sec, height=6, state="disabled",
                                                  font=("Courier", 9))
        self.log_box.pack(fill="both", expand=True)

        # scan progress bar (hidden until running)
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_lbl = tk.StringVar(value="")
        self._prog_bar = ttk.Progressbar(self, variable=self.progress_var, maximum=100)
        self._prog_lbl_widget = ttk.Label(self, textvariable=self.progress_lbl)

    # ── session list helpers ──────────────────────────────────────────────────

    def _log(self, msg: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _refresh_list(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for sess in self.app.sessions:
            n_det = len(sess.detected_pixels)
            n_tot = self.app.ctrl_tab.get_config().pixel_count
            cov = f"{100*n_det/max(n_tot,1):.0f}%"
            loc = "PnP" if sess.auto_located else f"{sess.angle_deg:.0f}°/{sess.distance_m:.1f}m"
            self.tree.insert("", "end", values=(
                sess.session_id, loc, n_det, cov,
                "auto" if sess.auto_located else "manual"
            ))

    def _remove_last(self):
        if not self.app.sessions:
            return
        self.app.sessions.pop()
        self._refresh_list()
        self._rebuild()

    # ── new session ───────────────────────────────────────────────────────────

    def _new_session(self):
        if not self.app.camera or not self.app.camera.is_open:
            messagebox.showwarning("Session", "Open a camera on the Camera tab first")
            return
        if self.app.camera_K is None:
            messagebox.showwarning("Session", "Apply camera calibration first")
            return
        if self._running:
            return

        can_pnp = len(self.app.sessions) >= 2 and bool(self.app.pixel_results)
        session_id = len(self.app.sessions) + 1

        dlg = _SessionDialog(self, session_id, can_pnp)
        if dlg.result is None:
            return

        angle, dist, height, use_pnp = dlg.result
        sess = ScanSession(
            session_id=session_id,
            angle_deg=angle,
            distance_m=dist,
            height_m=height,
        )
        self.app.sessions.append(sess)

        self._run_session(sess, use_pnp)

    def _run_session(self, sess: ScanSession, use_pnp: bool):
        ctrl_cfg = self.app.ctrl_tab.get_config()
        cam = self.app.camera
        K = self.app.camera_K
        dist_coef = self.app.camera_dist

        self._running = True
        self.add_btn.configure(state="disabled")
        self._prog_bar.pack(fill="x", padx=8)
        self._prog_lbl_widget.pack(padx=8, anchor="w")

        self._q = queue.Queue()
        t = threading.Thread(
            target=self._scan_worker,
            args=(sess, use_pnp, ctrl_cfg, cam, K, dist_coef),
            daemon=True,
        )
        t.start()
        self._poll()

    def _scan_worker(self, sess: ScanSession, use_pnp: bool,
                     ctrl_cfg: ControllerConfig, cam: Camera,
                     K: np.ndarray, dist_coef: np.ndarray):
        ctrl = PixelController(ctrl_cfg)
        ctrl.connect()
        try:
            detector = LEDDetector()
            n = ctrl_cfg.pixel_count

            self._q.put(("log", f"Session {sess.session_id}: capturing background…"))
            ctrl.blackout()
            time.sleep(0.3)
            # Single camera — wrap in list for capture helpers
            backgrounds = {}
            for _ in range(3):  # burn a few frames for exposure settle
                cam.read()
            bg = cam.read_gray()
            if bg is None:
                self._q.put(("error", "Could not read camera"))
                return
            backgrounds[cam.info.cam_id] = bg

            captures = []
            for i in range(n):
                if not self._running:
                    self._q.put(("log", "Stopped."))
                    break
                ctrl.light_pixel(i)
                time.sleep(ctrl_cfg.inter_pixel_delay)
                cap = scan_pixel(i, [cam], backgrounds, detector)
                ctrl.blackout()
                captures.append(cap)
                pct = (i + 1) / n * 100
                self._q.put(("progress", pct, i + 1, n, cap.camera_count))

            sess.captures = captures
            n_det = len(sess.detected_pixels)
            self._q.put(("log", f"Session {sess.session_id}: {n_det}/{n} pixels detected"))

            # ── calibrate this session ────────────────────────────────────────
            if use_pnp and self.app.pixel_results:
                known = {idx: r.position
                         for idx, r in self.app.pixel_results.items()
                         if r.position is not None}
                calib = locate_session_pnp(sess, known, K, dist_coef)
                if calib:
                    sess.calib = calib
                    sess.auto_located = True
                    self._q.put(("log", f"  PnP auto-located camera ✓"))
                else:
                    self._q.put(("log", "  PnP failed — using manual position"))

            if sess.calib is None:
                sess.calib = build_session_calib(sess, K, dist_coef)
                self._q.put(("log",
                    f"  Manual position: {sess.angle_deg:.0f}°, "
                    f"{sess.distance_m:.1f}m, h={sess.height_m:.1f}m"))

            self._q.put(("done",))

        except Exception as e:
            self._q.put(("error", str(e)))
            log.exception("Session worker error")
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
                    self.progress_lbl.set(
                        f"Pixel {i}/{n}  —  {'detected' if det else 'not seen'}"
                    )
                elif kind == "log":
                    self._log(msg[1])
                elif kind == "done":
                    self._session_done()
                    return
                elif kind == "error":
                    messagebox.showerror("Scan Error", msg[1])
                    self._session_done()
                    return
        except queue.Empty:
            pass
        if self._running:
            self.after(80, self._poll)

    def _session_done(self):
        self._running = False
        self.add_btn.configure(state="normal")
        self._prog_bar.pack_forget()
        self._prog_lbl_widget.pack_forget()
        self._refresh_list()
        self._rebuild()

    # ── rebuild model ─────────────────────────────────────────────────────────

    def _rebuild(self):
        """Triangulate from all sessions and refresh confidence + 3D view."""
        n_pixels = self.app.ctrl_tab.get_config().pixel_count
        calibrated = [s for s in self.app.sessions if s.calib is not None]

        if len(calibrated) < 2:
            self._log("Need ≥2 sessions to triangulate — add another session.")
            self._update_confidence(None, n_pixels)
            return

        results = triangulate_sessions(self.app.sessions, n_pixels)
        self.app.pixel_results = results

        mc = compute_model_confidence(results, n_pixels)
        self.app.model_confidence = mc

        self._update_confidence(mc, n_pixels)
        self._update_3d(results, mc)
        self._log(
            f"→ Model: {mc.score_pct}% confidence  |  "
            f"high={mc.high_count} med={mc.medium_count} "
            f"low={mc.low_count} unseen={mc.unseen_count}"
        )

    def _update_confidence(self, mc: Optional[ModelConfidence], total: int):
        if mc is None:
            self.conf_pct_var.set("—")
            self.conf_grade_var.set("Add sessions to begin")
            self.conf_detail_var.set("")
            return

        self.conf_pct_var.set(f"{mc.score_pct}%")
        self.conf_grade_var.set(mc.grade)
        self.conf_detail_var.set(
            f"Coverage: {mc.coverage*100:.0f}%   "
            f"({mc.high_count} high / {mc.medium_count} medium / "
            f"{mc.low_count} low / {mc.unseen_count} unseen)\n"
            f"Avg reprojection error: {mc.mean_reprojection_px:.1f} px"
        )

    def _update_3d(self, results: Dict[int, PixelResult], mc: ModelConfidence):
        if not _MPL_OK:
            return

        self.ax.clear()

        # Sort pixels by confidence label for legend grouping
        groups: Dict[str, list] = {"high": [], "medium": [], "low": [], "unseen": []}
        for r in results.values():
            if r.position is not None:
                groups[r.confidence_label].append(r)
            else:
                groups["unseen"].append(r)

        for label, color in CONF_COLORS.items():
            grp = groups[label]
            if not grp:
                continue
            has_pos = [r for r in grp if r.position is not None]
            if not has_pos:
                continue
            pts = np.array([r.position for r in has_pos])
            self.ax.scatter(pts[:, 0], pts[:, 1], pts[:, 2],
                            c=color, s=8, alpha=0.85, label=label)

        self.ax.set_xlabel("X", fontsize=7)
        self.ax.set_ylabel("Y", fontsize=7)
        self.ax.set_zlabel("Z", fontsize=7)
        self.ax.tick_params(labelsize=6)
        n_sess = len([s for s in self.app.sessions if s.calib])
        self.ax.set_title(f"{mc.score_pct}% confidence  |  {n_sess} session(s)",
                          fontsize=9)
        self.ax.legend(fontsize=7, loc="upper left")
        self.canvas.draw()


# ── Tab 4: Export ─────────────────────────────────────────────────────────────

class ExportTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app

        # ── final 3D view ─────────────────────────────────────────────────────
        view_sec = _section(self, "Final 3D Model")
        if _MPL_OK:
            self.fig = Figure(figsize=(5, 4), dpi=90)
            self.ax = self.fig.add_subplot(111, projection="3d")
            self.canvas = FigureCanvasTkAgg(self.fig, master=view_sec)
            self.canvas.get_tk_widget().pack(fill="both", expand=True)
        else:
            ttk.Label(view_sec, text="Install matplotlib for preview").pack()

        ttk.Button(view_sec, text="Refresh View",
                   command=self._refresh).pack(pady=4)

        # ── confidence summary ────────────────────────────────────────────────
        sum_sec = _section(self, "Summary")
        self.summary_var = tk.StringVar(value="Run sessions first")
        ttk.Label(sum_sec, textvariable=self.summary_var,
                   justify="left").pack(anchor="w")

        # ── export options ────────────────────────────────────────────────────
        exp_sec = _section(self, "Export")
        self.model_name_var = tk.StringVar(value="BlinkyTree")
        self.min_conf_var = tk.DoubleVar(value=0.0)
        _row(exp_sec, "Model name:", ttk.Entry, textvariable=self.model_name_var)
        _row(exp_sec, "Min confidence filter:", ttk.Spinbox,
             from_=0.0, to=1.0, increment=0.05,
             textvariable=self.min_conf_var, width=8)
        ttk.Label(exp_sec,
                   text="(0.0 = include all triangulated pixels)",
                   foreground="gray").pack(anchor="w")

        btn_row = ttk.Frame(exp_sec)
        btn_row.pack(fill="x", pady=6)
        ttk.Button(btn_row, text="Export xModel + CSV + PLY",
                   command=self._export).pack(side="left", padx=4)

        self.export_status_var = tk.StringVar(value="")
        ttk.Label(exp_sec, textvariable=self.export_status_var,
                   foreground="green").pack(anchor="w")

    def _refresh(self):
        results = self.app.pixel_results
        mc = self.app.model_confidence
        if not results or mc is None:
            messagebox.showinfo("Export", "Run sessions first")
            return

        self._draw(results, mc)

        n_pixels = self.app.ctrl_tab.get_config().pixel_count
        self.summary_var.set(
            f"Overall confidence: {mc.score_pct}%  ({mc.grade})\n"
            f"Coverage: {mc.coverage*100:.0f}% of {n_pixels} pixels triangulated\n"
            f"  High: {mc.high_count}    Medium: {mc.medium_count}    "
            f"Low: {mc.low_count}    Unseen: {mc.unseen_count}\n"
            f"Avg reprojection error: {mc.mean_reprojection_px:.1f} px\n"
            f"Sessions used: {len([s for s in self.app.sessions if s.calib is not None])}"
        )

    def _draw(self, results: Dict[int, PixelResult], mc: ModelConfidence):
        if not _MPL_OK:
            return
        self.ax.clear()

        for label, color in CONF_COLORS.items():
            pts = [r.position for r in results.values()
                   if r.position is not None and r.confidence_label == label]
            if pts:
                arr = np.array(pts)
                self.ax.scatter(arr[:, 0], arr[:, 1], arr[:, 2],
                                c=color, s=12, alpha=0.9, label=label)

        self.ax.set_xlabel("X")
        self.ax.set_ylabel("Y (height)")
        self.ax.set_zlabel("Z")
        self.ax.set_title(f"BlinkyMap — {mc.score_pct}% confident")
        self.ax.legend(fontsize=8)
        self.canvas.draw()

    def _export(self):
        results = self.app.pixel_results
        if not results:
            messagebox.showwarning("Export", "No data — run sessions first")
            return

        out_dir = filedialog.askdirectory(title="Select export folder")
        if not out_dir:
            return

        min_c = self.min_conf_var.get()
        filtered = {
            idx: r.position for idx, r in results.items()
            if r.position is not None and r.confidence >= min_c
        }

        if not filtered:
            messagebox.showwarning("Export", "No pixels pass the confidence filter")
            return

        # Normalise (bottom of tree = Y=0)
        cleaned = remove_outliers(filtered)
        normalised = normalize_positions(cleaned)

        name = self.model_name_var.get().strip() or "BlinkyTree"
        cfg = self.app.ctrl_tab.get_config()
        try:
            paths = export_all(normalised, out_dir, name, cfg.pixel_count)
            self.export_status_var.set(f"Saved {len(normalised)} pixels → {out_dir}")
            msg = f"Exported {len(normalised)} pixels:\n"
            msg += "\n".join(f"  {k}: {v.name}" for k, v in paths.items())
            messagebox.showinfo("Export Complete", msg)
        except Exception as e:
            messagebox.showerror("Export Error", str(e))


# ── Main application ──────────────────────────────────────────────────────────

class BlinkyMapApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("BlinkyMap — Pixel Tree 3D Mapper")
        self.root.minsize(640, 620)

        # ── shared state ──────────────────────────────────────────────────────
        self.camera: Optional[Camera] = None
        self.camera_K: Optional[np.ndarray] = None
        self.camera_dist: np.ndarray = np.zeros(5)
        self.sessions: List[ScanSession] = []
        self.pixel_results: Dict[int, PixelResult] = {}
        self.model_confidence: Optional[ModelConfidence] = None

        # ── header ────────────────────────────────────────────────────────────
        hdr = ttk.Frame(self.root, relief="groove", padding=6)
        hdr.pack(fill="x", padx=4, pady=(4, 0))
        ttk.Label(hdr, text="BlinkyMap",
                   font=("", 14, "bold")).pack(side="left")
        ttk.Label(hdr, text="  Pixel Tree → xLights 3D Model  "
                  "  (one camera, multiple passes)",
                   foreground="gray").pack(side="left")

        # ── notebook ──────────────────────────────────────────────────────────
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=4, pady=4)

        self.ctrl_tab    = ControllerTab(self.notebook, self)
        self.cam_tab     = CameraTab(self.notebook, self)
        self.sess_tab    = SessionsTab(self.notebook, self)
        self.export_tab  = ExportTab(self.notebook, self)

        for tab, name in [
            (self.ctrl_tab,   "1. Controller"),
            (self.cam_tab,    "2. Camera"),
            (self.sess_tab,   "3. Sessions"),
            (self.export_tab, "4. Export"),
        ]:
            self.notebook.add(tab, text=name)

        # status bar
        sb = ttk.Frame(self.root, relief="sunken", padding=2)
        sb.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(sb, textvariable=self.status_var, anchor="w").pack(fill="x")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        if self.camera:
            try:
                self.camera.close()
            except Exception:
                pass
        self.root.destroy()

    def run(self):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        self.root.mainloop()
