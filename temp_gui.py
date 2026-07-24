#!/usr/bin/env python3
"""
Live Temperature Logger GUI
---------------------------
A Tkinter front-end for the ESP32 temperature firmware. It works with any
number of sensors and is built around this workflow:

    1. Pick a COM port and Connect.
    2. Plots appear automatically -- one per detected sensor.
    3. Rename the sensors while data is already streaming.
    4. Choose a folder + file name and Start saving to write CSV.

Viewing controls (below the plot): the "Show" box picks how much time is
visible (All = everything since connect) and the "End of view" slider chooses
where that view ends -- far right is LIVE (follows incoming data); anywhere
left of that freezes the view at an earlier moment. So All+LIVE is start-to-
now, All+scrub is start-to-some-point, and a short window+LIVE is just-the-
recent-data. The control panel and the plot are separated by a draggable
sash, and the panel can be hidden entirely with the button above the plot.

Serial protocol (115200 baud), shared with the CLI loggers:
    Header (optional):  T,RTD,TC1,TC2,TC3      (fields are non-numeric names)
    Data:               T,23.45,150.2,148.9,nan  (one float per sensor; nan ok)
    Fault:              FAULT_RTD,0x80           (reported, not plotted)

The sensor count is inferred from the data rows, so the same GUI handles 1
sensor or 20. A name header, when present, is only used to pre-fill the
editable name fields.

Requires:  pip install pyserial matplotlib   (tkinter ships with CPython)
Run:       python temp_gui.py
"""

import os
import csv
import math
import time
import queue
import bisect
import threading
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import serial
import serial.tools.list_ports

from lab_time import lab_timestamp, session_header

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import (
    FigureCanvasTkAgg, NavigationToolbar2Tk)

BAUD_RATE  = 115200
MAX_PLOT_POINTS = 1500    # max points drawn; longer views are decimated
PLOT_MS    = 500          # plot redraw interval (ms)
POLL_MS    = 50           # serial-queue drain interval (ms)

# "Show" choices for the time window (label, seconds); None = everything.
WINDOW_CHOICES = [("All", None), ("30 s", 30), ("1 min", 60), ("2 min", 120),
                  ("5 min", 300), ("15 min", 900), ("30 min", 1800),
                  ("1 h", 3600), ("4 h", 14400), ("12 h", 43200)]
SCRUB_MAX = 1000          # resolution of the end-of-view slider

COLORS = ["steelblue", "tomato", "seagreen", "darkorange", "mediumpurple",
          "gold", "deeppink", "cyan", "olive", "slategray", "crimson",
          "teal", "chocolate", "darkgreen", "navy", "magenta"]


def safe_float(s):
    """Return a float, or None for blanks / non-numeric / NaN cells."""
    try:
        v = float(s.strip())
        return None if math.isnan(v) else v
    except (ValueError, AttributeError):
        return None


def looks_numeric(fields):
    """True if every field parses as a float (nan/inf count as numeric)."""
    if not fields:
        return False
    for f in fields:
        try:
            float(f.strip())
        except (ValueError, AttributeError):
            return False
    return True


class TempLoggerGUI:
    def __init__(self, root):
        self.root = root
        root.title("Temperature Logger")
        root.geometry("1150x760")
        root.minsize(900, 600)

        # --- serial / reader-thread state ---
        self.ser = None
        self.reader_thread = None
        self.stop_flag = threading.Event()
        self.rx = queue.Queue()

        # --- sensor / data model ---
        self.n = 0                       # number of sensors (0 = unknown yet)
        self.esp_names = []              # names from the ESP32 header, if any
        self.names = []                  # active display names
        self.name_vars = []              # tk.StringVar per name entry

        # Full-resolution history: the plot and the CSV back-fill both draw
        # from these (the plot decimates long views down to MAX_PLOT_POINTS).
        self.hist_epoch = []             # wall-clock time.time() per sample
        self.hist_labels = []            # "HH:MM:SS" tick label per sample
        self.hist_elapsed = []           # seconds since connect
        self.hist_data = []              # list[list] one column per sensor
        self.start_epoch = None          # wall clock at connect

        # --- CSV logging state ---
        self.save_dir = None
        self.csv_file = None
        self.csv_writer = None
        self.saved_rows = 0
        self.save_from_index = 0         # history index where live saving resumes

        # --- plot handles ---
        self.fig = None
        self.canvas = None
        self.axes = []
        self.lines = []
        self.overlay_var = tk.BooleanVar(value=False)
        self.plot_dirty = False

        self._build_ui()
        self.refresh_ports()
        self.root.after(POLL_MS, self._poll_serial)
        self.root.after(PLOT_MS, self._refresh_plot)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # =====================================================================
    #  UI construction
    # =====================================================================
    def _build_ui(self):
        # Two columns: control panel on the left, plot on the right. The
        # sash between them is draggable, and the panel can be hidden
        # entirely with the toggle button above the plot.
        self.paned = ttk.PanedWindow(self.root, orient="horizontal")
        self.paned.pack(fill="both", expand=True, padx=8, pady=8)

        self.left = ttk.Frame(self.paned)
        right = ttk.Frame(self.paned)
        self.paned.add(self.left, weight=0)
        self.paned.add(right, weight=1)
        self.panel_visible = True

        self._build_connection(self.left)
        self._build_sensors(self.left)
        self._build_saving(self.left)
        self._build_log(self.left)
        self._build_plot(right)

    def toggle_panel(self):
        if self.panel_visible:
            self.paned.forget(self.left)
            self.panel_btn.configure(text="▶ Show panel")
        else:
            self.paned.insert(0, self.left)
            self.panel_btn.configure(text="◀ Hide panel")
        self.panel_visible = not self.panel_visible

    def _build_connection(self, parent):
        f = ttk.LabelFrame(parent, text="Connection")
        f.pack(fill="x", pady=(0, 8))
        self.port_var = tk.StringVar()
        self.port_menu = ttk.Combobox(f, textvariable=self.port_var,
                                      width=26, state="readonly")
        self.port_menu.grid(row=0, column=0, columnspan=2, padx=6, pady=6)
        ttk.Button(f, text="Refresh", command=self.refresh_ports).grid(
            row=1, column=0, padx=6, pady=(0, 6), sticky="ew")
        self.connect_btn = ttk.Button(f, text="Connect",
                                       command=self.toggle_connect)
        self.connect_btn.grid(row=1, column=1, padx=6, pady=(0, 6), sticky="ew")
        self.conn_status = ttk.Label(f, text="Not connected", foreground="#888")
        self.conn_status.grid(row=2, column=0, columnspan=2, padx=6,
                              pady=(0, 6), sticky="w")

    def _build_sensors(self, parent):
        f = ttk.LabelFrame(parent, text="Sensors")
        f.pack(fill="both", expand=True, pady=8)

        ttk.Label(f, text="Rename channels, then Apply:",
                  foreground="#555").pack(anchor="w", padx=6, pady=(6, 2))

        # Scrollable list -- the number of sensors is arbitrary.
        body = ttk.Frame(f)
        body.pack(fill="both", expand=True, padx=6)
        self.names_canvas = tk.Canvas(body, height=180, width=250,
                                      highlightthickness=0)
        vsb = ttk.Scrollbar(body, orient="vertical",
                            command=self.names_canvas.yview)
        self.names_frame = ttk.Frame(self.names_canvas)
        self.names_frame.bind(
            "<Configure>",
            lambda e: self.names_canvas.configure(
                scrollregion=self.names_canvas.bbox("all")))
        self.names_canvas.create_window((0, 0), window=self.names_frame,
                                        anchor="nw")
        self.names_canvas.configure(yscrollcommand=vsb.set)
        self.names_canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.names_hint = ttk.Label(self.names_frame,
                                    text="(connect to detect sensors)",
                                    foreground="#888")
        self.names_hint.pack(anchor="w", pady=6)

        btns = ttk.Frame(f)
        btns.pack(fill="x", padx=6, pady=6)
        self.apply_btn = ttk.Button(btns, text="Apply names",
                                    command=self.apply_names, state="disabled")
        self.apply_btn.pack(side="left")
        ttk.Checkbutton(btns, text="Overlay on one plot",
                        variable=self.overlay_var,
                        command=self.rebuild_plots).pack(side="left", padx=10)

    def _build_saving(self, parent):
        f = ttk.LabelFrame(parent, text="Saving")
        f.pack(fill="x", pady=8)

        ttk.Button(f, text="Choose folder…",
                   command=self.choose_folder).grid(
            row=0, column=0, padx=6, pady=6, sticky="ew")
        self.folder_lbl = ttk.Label(f, text="(no folder)", foreground="#888",
                                    width=26, anchor="w")
        self.folder_lbl.grid(row=0, column=1, padx=6, pady=6, sticky="w")

        ttk.Label(f, text="File name:").grid(row=1, column=0, padx=6, sticky="e")
        self.fname_var = tk.StringVar(
            value=f"temp_{datetime.now():%Y%m%d_%H%M%S}.csv")
        ttk.Entry(f, textvariable=self.fname_var, width=26).grid(
            row=1, column=1, padx=6, pady=4, sticky="w")

        self.save_btn = ttk.Button(f, text="Start saving",
                                   command=self.toggle_saving, state="disabled")
        self.save_btn.grid(row=2, column=0, padx=6, pady=6, sticky="ew")
        self.save_status = ttk.Label(f, text="not saving", foreground="#888")
        self.save_status.grid(row=2, column=1, padx=6, pady=6, sticky="w")

    def _build_log(self, parent):
        f = ttk.LabelFrame(parent, text="Log")
        f.pack(fill="both", expand=False, pady=(8, 0))
        self.log = tk.Text(f, height=6, width=40, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, padx=4, pady=4)

    def _build_plot(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill="x", padx=4)
        self.panel_btn = ttk.Button(top, text="◀ Hide panel", width=14,
                                    command=self.toggle_panel)
        self.panel_btn.pack(side="left")

        self.fig = Figure(figsize=(7, 6), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        toolbar = NavigationToolbar2Tk(self.canvas, parent, pack_toolbar=False)
        toolbar.update()

        # Pack bottom-up so the canvas takes all remaining space.
        self._build_timenav(parent)
        toolbar.pack(side="bottom", fill="x")
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self._show_placeholder()

    def _build_timenav(self, parent):
        f = ttk.LabelFrame(parent, text="Time window")
        f.pack(side="bottom", fill="x", pady=(4, 0))
        row = ttk.Frame(f)
        row.pack(fill="x", padx=6, pady=4)

        ttk.Label(row, text="Show:").pack(side="left")
        self.window_var = tk.StringVar(value="All")
        wb = ttk.Combobox(row, textvariable=self.window_var, width=7,
                          state="readonly",
                          values=[lbl for lbl, _ in WINDOW_CHOICES])
        wb.pack(side="left", padx=(4, 16))
        wb.bind("<<ComboboxSelected>>", lambda _e: self._mark_dirty())

        ttk.Label(row, text="End of view:").pack(side="left")
        self.scrub_var = tk.DoubleVar(value=SCRUB_MAX)
        self.scrub = ttk.Scale(row, from_=0, to=SCRUB_MAX,
                               variable=self.scrub_var,
                               command=lambda _v: self._mark_dirty())
        self.scrub.pack(side="left", fill="x", expand=True, padx=6)
        self.scrub_lbl = ttk.Label(row, text="LIVE", width=9,
                                   foreground="#2e7d32")
        self.scrub_lbl.pack(side="left")
        ttk.Button(row, text="Go live", width=8,
                   command=self._go_live).pack(side="left", padx=(4, 0))

    def _mark_dirty(self):
        self.plot_dirty = True

    def _go_live(self):
        self.scrub_var.set(SCRUB_MAX)
        self.plot_dirty = True

    def _show_placeholder(self):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.text(0.5, 0.5, "Connect to a COM port to begin",
                ha="center", va="center", fontsize=13, color="#888",
                transform=ax.transAxes)
        ax.set_xticks([])
        ax.set_yticks([])
        self.canvas.draw_idle()

    # =====================================================================
    #  Logging helper
    # =====================================================================
    def logmsg(self, text):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{ts}] {text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # =====================================================================
    #  Serial connection
    # =====================================================================
    def refresh_ports(self):
        ports = serial.tools.list_ports.comports()
        values = [f"{p.device} - {p.description}" for p in ports]
        self._port_devices = [p.device for p in ports]
        self.port_menu["values"] = values
        if values and not self.port_var.get():
            self.port_menu.current(0)
        if not values:
            self.logmsg("No COM ports found.")

    def _selected_port(self):
        idx = self.port_menu.current()
        if idx is None or idx < 0:
            return None
        return self._port_devices[idx]

    def toggle_connect(self):
        if self.ser is None:
            self.connect()
        else:
            self.disconnect()

    def connect(self):
        port = self._selected_port()
        if not port:
            messagebox.showwarning("No port", "Select a COM port first.")
            return
        try:
            self.ser = serial.Serial(port, BAUD_RATE, timeout=1)
        except serial.SerialException as e:
            messagebox.showerror("Connection failed",
                                 f"Could not open {port}.\n\n{e}\n\n"
                                 "Make sure no serial monitor is using it.")
            self.ser = None
            return

        # Reset the data model for a fresh session.
        self._reset_data()
        self.stop_flag.clear()
        self.reader_thread = threading.Thread(target=self._reader_loop,
                                              daemon=True)
        self.reader_thread.start()

        self.connect_btn.configure(text="Disconnect")
        self.conn_status.configure(text=f"Connected: {port}",
                                   foreground="#2e7d32")
        self.logmsg(f"Opened {port} at {BAUD_RATE} baud. Waiting for data…")

    def disconnect(self):
        self.stop_flag.set()
        if self.reader_thread:
            self.reader_thread.join(timeout=1.0)
            self.reader_thread = None
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        if self.csv_file:
            self.stop_saving()
        self.connect_btn.configure(text="Connect")
        self.conn_status.configure(text="Not connected", foreground="#888")
        self.logmsg("Disconnected.")

    def _reader_loop(self):
        """Runs in a background thread: read lines, hand them to the queue."""
        while not self.stop_flag.is_set():
            try:
                raw = self.ser.readline().decode("utf-8", errors="ignore")
            except Exception:
                break
            if raw:
                self.rx.put(raw.strip())

    # =====================================================================
    #  Serial parsing (main thread, via queue)
    # =====================================================================
    def _poll_serial(self):
        try:
            while True:
                line = self.rx.get_nowait()
                self._handle_line(line)
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._poll_serial)

    def _handle_line(self, line):
        if not line:
            return
        if not line.startswith("T,"):
            if "FAULT" in line:
                self.logmsg(f"!! {line}")
            return

        fields = line.split(",")[1:]
        if not fields:
            return

        if not looks_numeric(fields):
            # A name header -- remember it and (re)configure channels.
            self.esp_names = [f.strip() for f in fields]
            if self.n != len(self.esp_names):
                self._configure_sensors(len(self.esp_names),
                                        default_names=self.esp_names)
            else:
                # Same count: pre-fill any names the user hasn't customised.
                self._prefill_names(self.esp_names)
            self.logmsg(f"Header: {', '.join(self.esp_names)}")
            return

        # Data row.
        if self.n == 0:
            self._configure_sensors(len(fields))
        if len(fields) != self.n:
            return  # malformed / mid-reconfiguration row

        vals = [safe_float(f) for f in fields]
        self._append_sample(vals)

    def _append_sample(self, vals):
        # One wall-clock reading feeds both time columns and the tick
        # label, so the CSV and the plot can never name different
        # instants for the same sample.
        epoch = time.time()
        elapsed = epoch - self.start_epoch

        self.hist_epoch.append(epoch)
        self.hist_labels.append(datetime.fromtimestamp(epoch)
                                .strftime("%H:%M:%S"))
        self.hist_elapsed.append(elapsed)
        for i, v in enumerate(vals):
            self.hist_data[i].append(v)

        self.plot_dirty = True

        if self.csv_writer:
            self.csv_writer.writerow(
                [lab_timestamp(epoch), f"{elapsed:.3f}"]
                + [f"{v:.2f}" if v is not None else "" for v in vals])
            self.csv_file.flush()
            self.saved_rows += 1
            self.save_status.configure(text=f"saving ({self.saved_rows} rows)",
                                       foreground="#2e7d32")

    # =====================================================================
    #  Sensor / name configuration
    # =====================================================================
    def _reset_data(self):
        self.n = 0
        self.esp_names = []
        self.names = []
        self.hist_epoch = []
        self.hist_labels = []
        self.hist_elapsed = []
        self.hist_data = []
        self.start_epoch = time.time()
        self.scrub_var.set(SCRUB_MAX)     # new session starts LIVE
        self.plot_dirty = False
        for w in self.names_frame.winfo_children():
            w.destroy()
        self.name_vars = []
        self.apply_btn.configure(state="disabled")
        self.save_btn.configure(state="disabled")
        self._show_placeholder()

    def _configure_sensors(self, n, default_names=None):
        """Establish n channels, build name entries, and build the plots."""
        self.n = n
        self.hist_data = [[] for _ in range(n)]
        if default_names and len(default_names) == n:
            self.names = list(default_names)
        else:
            self.names = [f"Sensor {i + 1}" for i in range(n)]

        self._build_name_entries()
        self.apply_btn.configure(state="normal")
        self.save_btn.configure(state="normal")
        self.rebuild_plots()
        self.logmsg(f"Detected {n} sensor(s). Plotting live.")

    def _build_name_entries(self):
        for w in self.names_frame.winfo_children():
            w.destroy()
        self.name_vars = []
        for i in range(self.n):
            row = ttk.Frame(self.names_frame)
            row.pack(fill="x", pady=2)
            swatch = tk.Label(row, text="  ", background=COLORS[i % len(COLORS)])
            swatch.pack(side="left", padx=(0, 6))
            ttk.Label(row, text=f"{i + 1}.", width=3).pack(side="left")
            var = tk.StringVar(value=self.names[i])
            ttk.Entry(row, textvariable=var, width=18).pack(side="left",
                                                            fill="x", expand=True)
            self.name_vars.append(var)

    def _prefill_names(self, names):
        """Update name entries that still hold their auto-generated default."""
        for i, var in enumerate(self.name_vars):
            if i < len(names) and var.get() == f"Sensor {i + 1}":
                var.set(names[i])

    def apply_names(self):
        if self.csv_writer:
            messagebox.showinfo(
                "Saving in progress",
                "Sensor names are locked while saving (they are the CSV "
                "header). Stop saving to rename, then start a new file.")
            return
        self.names = [v.get().strip() or f"Sensor {i + 1}"
                      for i, v in enumerate(self.name_vars)]
        self.rebuild_plots()
        self.logmsg(f"Names applied: {', '.join(self.names)}")

    # =====================================================================
    #  Plotting
    # =====================================================================
    def rebuild_plots(self):
        if self.n == 0:
            return
        self.fig.clear()
        self.axes = []
        self.lines = []

        if self.overlay_var.get():
            ax = self.fig.add_subplot(111)
            for i in range(self.n):
                line, = ax.plot([], [], color=COLORS[i % len(COLORS)],
                                linewidth=1.5, marker="o", markersize=3,
                                label=self.names[i])
                self.lines.append(line)
                self.axes.append(ax)
            ax.set_ylabel("Temperature (°C)")
            ax.set_xlabel("Time")
            ax.grid(True, linestyle="--", alpha=0.5)
            ax.legend(loc="upper left", fontsize=8)
        else:
            for i in range(self.n):
                ax = self.fig.add_subplot(self.n, 1, i + 1)
                line, = ax.plot([], [], color=COLORS[i % len(COLORS)],
                                linewidth=1.5, marker="o", markersize=3)
                ax.set_ylabel(f"{self.names[i]}\n(°C)", fontsize=9)
                ax.grid(True, linestyle="--", alpha=0.5)
                if i < self.n - 1:
                    ax.tick_params(labelbottom=False)
                self.axes.append(ax)
                self.lines.append(line)
            self.axes[-1].set_xlabel("Time")

        self.fig.suptitle("Temperature — Live", fontsize=12)
        self.fig.tight_layout()
        self.plot_dirty = True
        self.canvas.draw_idle()

    def _window_seconds(self):
        for lbl, secs in WINDOW_CHOICES:
            if lbl == self.window_var.get():
                return secs
        return None

    def _view_range(self):
        """History slice (i0, i1, live) for the current time controls.

        The scrub slider maps [0..SCRUB_MAX] onto [first sample .. newest];
        at the far right the view is LIVE and follows incoming data.
        """
        t0, t1 = self.hist_elapsed[0], self.hist_elapsed[-1]
        frac = self.scrub_var.get() / SCRUB_MAX
        live = frac >= 0.999
        t_end = t1 if live else t0 + (t1 - t0) * frac
        w = self._window_seconds()
        t_start = t0 if w is None else max(t0, t_end - w)
        i0 = bisect.bisect_left(self.hist_elapsed, t_start)
        i1 = bisect.bisect_right(self.hist_elapsed, t_end)
        if i1 <= i0:                      # always show at least one sample
            i1 = min(i0 + 1, len(self.hist_elapsed))
            i0 = i1 - 1
        return i0, i1, live

    def _refresh_plot(self):
        if self.plot_dirty and self.n and self.hist_elapsed:
            i0, i1, live = self._view_range()
            step = max(1, (i1 - i0) // MAX_PLOT_POINTS)
            t = self.hist_elapsed[i0:i1:step]
            lbls = self.hist_labels[i0:i1:step]
            marker = "o" if len(t) <= 300 else ""
            for i, line in enumerate(self.lines):
                col = self.hist_data[i][i0:i1:step]
                line.set_data(
                    t, [float("nan") if v is None else v for v in col])
                line.set_marker(marker)

            # x limits come from the view; y autoscales to the visible data.
            x_lo, x_hi = t[0], t[-1]
            if x_hi - x_lo < 1e-9:
                x_lo, x_hi = x_lo - 0.5, x_hi + 0.5
            for ax in dict.fromkeys(self.axes):
                ax.relim()
                ax.autoscale_view(scalex=False, scaley=True)
                ax.set_xlim(x_lo, x_hi)

            # Time tick labels on the bottom axis only.
            bottom = self.axes[-1]
            k = max(1, len(t) // 6)
            idx = list(range(0, len(t), k))
            bottom.set_xticks([t[j] for j in idx])
            bottom.set_xticklabels([lbls[j] for j in idx],
                                   rotation=30, ha="right", fontsize=8)

            if live:
                self.scrub_lbl.configure(text="LIVE", foreground="#2e7d32")
            else:
                self.scrub_lbl.configure(text=lbls[-1], foreground="#c62828")

            self.canvas.draw_idle()
            self.plot_dirty = False
        self.root.after(PLOT_MS, self._refresh_plot)

    # =====================================================================
    #  CSV saving
    # =====================================================================
    def choose_folder(self):
        d = filedialog.askdirectory(title="Choose a folder to save CSV")
        if d:
            self.save_dir = d
            self.folder_lbl.configure(text=d, foreground="#333")

    def toggle_saving(self):
        if self.csv_writer is None:
            self.start_saving()
        else:
            self.stop_saving()

    def start_saving(self):
        if self.n == 0:
            messagebox.showwarning("No sensors", "No sensors detected yet.")
            return
        if not self.save_dir:
            messagebox.showwarning("No folder", "Choose a folder first.")
            return
        fname = self.fname_var.get().strip() or "temp_log.csv"
        if not fname.lower().endswith(".csv"):
            fname += ".csv"
        path = os.path.join(self.save_dir, fname)
        if os.path.exists(path) and not messagebox.askyesno(
                "Overwrite?", f"{fname} already exists. Overwrite it?"):
            return
        try:
            self.csv_file = open(path, "w", newline="")
        except OSError as e:
            messagebox.showerror("Cannot save", f"Could not open file:\n{e}")
            self.csv_file = None
            return

        # Lock in names from the entries at save time.
        self.names = [v.get().strip() or f"Sensor {i + 1}"
                      for i, v in enumerate(self.name_vars)]
        self.rebuild_plots()

        # '#' provenance block, the same one every lab log opens with.
        # pandas/numpy skip it with comment='#'. session_start is the
        # CONNECT time, not when saving began, because that is the origin
        # elapsed_s counts from and the back-fill below reaches back to it.
        self.csv_file.write(session_header("temperature log",
                                           self.start_epoch))
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow(
            ["timestamp", "elapsed_s"] + [f"{n}_C" for n in self.names])

        # Back-fill everything captured since connect so nothing is lost.
        self.saved_rows = 0
        for j in range(len(self.hist_epoch)):
            row = [lab_timestamp(self.hist_epoch[j]),
                   f"{self.hist_elapsed[j]:.3f}"] + [
                f"{self.hist_data[i][j]:.2f}"
                if self.hist_data[i][j] is not None else ""
                for i in range(self.n)]
            self.csv_writer.writerow(row)
            self.saved_rows += 1
        self.csv_file.flush()

        self.save_btn.configure(text="Stop saving")
        self.save_status.configure(text=f"saving ({self.saved_rows} rows)",
                                   foreground="#2e7d32")
        self._set_name_entries_state("disabled")
        self.logmsg(f"Saving to {path} (back-filled {self.saved_rows} rows).")

    def stop_saving(self):
        if self.csv_file:
            try:
                self.csv_file.close()
            except Exception:
                pass
        self.csv_file = None
        self.csv_writer = None
        self.save_btn.configure(text="Start saving")
        self.save_status.configure(
            text=f"stopped ({self.saved_rows} rows)", foreground="#888")
        self._set_name_entries_state("normal")
        self.logmsg("Stopped saving.")

    def _set_name_entries_state(self, state):
        for row in self.names_frame.winfo_children():
            for w in row.winfo_children():
                if isinstance(w, ttk.Entry):
                    w.configure(state=state)

    # =====================================================================
    #  Shutdown
    # =====================================================================
    def on_close(self):
        try:
            self.disconnect()
        finally:
            self.root.destroy()


def main():
    root = tk.Tk()
    TempLoggerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
