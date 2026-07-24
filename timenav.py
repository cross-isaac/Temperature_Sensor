"""
The time-window / time-travel control, as one reusable Tk widget.

A live plot needs two independent knobs: *how much* time is on screen, and
*where that view ends*. `TimeNav` owns both, plus the parsing and the dialogs
for typing exact values, so a host GUI only has to say what its data span is
and ask for a slice back.

    nav = TimeNav(parent, on_change=self._mark_dirty)
    ...
    nav.set_span(elapsed[0], elapsed[-1], epoch0=self.start_epoch)
    i0, i1 = nav.slice(elapsed)          # the samples to draw

Everything is in **elapsed seconds** from a session origin. Pass `epoch0` (the
wall clock of elapsed 0) and the widget can also show and accept real clock
times; without it, it falls back to "+123 s" labels and still works.

Why a menubutton and not a combobox
-----------------------------------
This control sits at the bottom of a plot window. A `ttk.Combobox` drops its
list *downward*, so on a maximised window -- where the widget is a few pixels
above the taskbar -- the list opened past the bottom of the screen and the
lower entries were unreachable. A `ttk.Menubutton` with `direction="above"`
pops upward by construction, which is always correct for a bottom-anchored
control regardless of screen size, DPI or where the window sits. That is the
fix, not a workaround: nothing here depends on Tk guessing that it should flip.

The view model
--------------
* `window_s`  -- seconds visible, or None for "everything".
* `view_end`  -- elapsed seconds of the right edge, or None for LIVE
                 (follows incoming data).

The scrub slider writes `view_end`; new data never moves a pinned view. A
custom range sets both at once. Storing an absolute `view_end` rather than a
slider fraction is what makes a typed range stay put: a fraction of a span
that keeps growing would drift left every time a sample arrived.

This file is deliberately duplicated across the lab's GUIs rather than
imported, so each repo stays standalone. If it changes, it changes in all of
them.
"""

import bisect
import re
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk

#: Resolution of the end-of-view slider.
SCRUB_MAX = 1000

#: (label, seconds) offered in the Show menu. "All" and "Custom" are added.
DEFAULT_PRESETS = [
    ("30 s", 30), ("1 min", 60), ("2 min", 120), ("5 min", 300),
    ("15 min", 900), ("30 min", 1800), ("1 h", 3600), ("2 h", 7200),
    ("6 h", 21600), ("12 h", 43200), ("24 h", 86400),
]

LIVE_FG = "#2e7d32"      # green: view is following live data
PAST_FG = "#c62828"      # red: view is frozen in the past
HINT_FG = "#666666"

_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
}
_TOKEN = re.compile(r"(\d*\.?\d+)([a-z]*)")


def parse_duration(text):
    """Seconds from a typed duration, or None if it makes no sense.

    Accepts a bare number as seconds ("90"), a number with a unit
    ("90 s", "2.5 min", "4 hours"), several of those run together
    ("1h30m", "2 min 30 s"), and clock-style "M:SS" / "H:MM:SS".
    Whitespace is irrelevant. Zero and negative durations are rejected --
    a window of zero seconds has nothing to show.
    """
    s = re.sub(r"\s+", "", str(text).strip().lower())
    if not s:
        return None

    if ":" in s:
        parts = s.split(":")
        if len(parts) > 3:
            return None
        try:
            values = [float(p) for p in parts]
        except ValueError:
            return None
        total = 0.0
        for v in values:                    # M:SS or H:MM:SS
            total = total * 60 + v
        return total if total > 0 else None

    total = 0.0
    pos = 0
    for match in _TOKEN.finditer(s):
        if match.start() != pos:            # junk between the tokens
            return None
        pos = match.end()
        unit = match.group(2)
        if unit == "":
            multiplier = 1                  # a bare number means seconds
        elif unit in _UNITS:
            multiplier = _UNITS[unit]
        else:
            return None
        total += float(match.group(1)) * multiplier
    if pos != len(s):                       # trailing junk
        return None
    return total if total > 0 else None


def format_duration(seconds):
    """'All' / '45 s' / '2.5 min' / '4 h' -- the Show button's label."""
    if seconds is None:
        return "All"
    if seconds < 60:
        return f"{seconds:g} s"
    if seconds < 3600:
        return f"{seconds / 60:g} min"
    if seconds < 86400:
        return f"{seconds / 3600:g} h"
    return f"{seconds / 86400:g} d"


class _Prompt(tk.Toplevel):
    """A small modal dialog: some labelled entries, OK / Cancel.

    `result` is a list of the entry strings, or None if cancelled.
    """

    def __init__(self, parent, title, fields, hint=None, extra=None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result = None
        self._entries = []

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)

        if hint:
            ttk.Label(body, text=hint, foreground=HINT_FG,
                      justify="left").grid(row=0, column=0, columnspan=2,
                                           sticky="w", pady=(0, 8))
        for i, (label, initial) in enumerate(fields, start=1):
            ttk.Label(body, text=label).grid(row=i, column=0, sticky="e",
                                             padx=(0, 6), pady=2)
            var = tk.StringVar(value=initial)
            entry = ttk.Entry(body, textvariable=var, width=24)
            entry.grid(row=i, column=1, sticky="ew", pady=2)
            self._entries.append(var)

        row = len(fields) + 1
        if extra:
            ttk.Label(body, text=extra, foreground=HINT_FG,
                      justify="left").grid(row=row, column=0, columnspan=2,
                                           sticky="w", pady=(8, 0))
            row += 1

        self._error = ttk.Label(body, text="", foreground=PAST_FG)
        self._error.grid(row=row, column=0, columnspan=2, sticky="w",
                         pady=(6, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=row + 1, column=0, columnspan=2, sticky="e",
                     pady=(10, 0))
        ttk.Button(buttons, text="Cancel", width=9,
                   command=self._cancel).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="OK", width=9,
                   command=self._ok).pack(side="right")

        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.transient(parent)
        self.grab_set()
        self.update_idletasks()
        self._centre_on(parent)

    def _centre_on(self, parent):
        try:
            x = parent.winfo_rootx() + (parent.winfo_width()
                                        - self.winfo_width()) // 2
            y = parent.winfo_rooty() + (parent.winfo_height()
                                        - self.winfo_height()) // 2
            # Never place the dialog off-screen: a parent that is maximised
            # or on a secondary monitor can push a naive centre out of view.
            x = max(0, min(x, self.winfo_screenwidth() - self.winfo_width()))
            y = max(0, min(y, self.winfo_screenheight() - self.winfo_height()))
            self.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass

    def show_error(self, message):
        self._error.configure(text=message)

    def _ok(self):
        self.result = [v.get() for v in self._entries]
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class TimeNav(ttk.LabelFrame):
    """Show-this-much / end-the-view-here controls for a live plot.

    Args:
        parent: Tk parent.
        on_change: Called with no arguments whenever the view changes.
            Typically the host's "mark the plot dirty" method.
        presets: [(label, seconds)] for the Show menu; defaults to
            `DEFAULT_PRESETS`.
        text: LabelFrame caption.
    """

    def __init__(self, parent, on_change=None, presets=None,
                 text="Time window"):
        super().__init__(parent, text=text)
        self._on_change = on_change
        self._presets = list(DEFAULT_PRESETS if presets is None else presets)

        self._window_s = None       # None = show everything
        self._view_end = None       # None = LIVE
        self._t0 = 0.0              # elapsed seconds of the first sample
        self._t1 = 0.0              # ... and of the newest
        self._epoch0 = None         # wall clock at elapsed 0, if known
        self._syncing = False       # suppress the slider's own callback

        self._build()

    # ── construction ─────────────────────────────────────────────────────

    def _build(self):
        row = ttk.Frame(self)
        row.pack(fill="x", padx=6, pady=(4, 2))

        ttk.Label(row, text="Show:").pack(side="left")
        # direction="above": this control lives at the bottom of the window,
        # so the menu must open upward or it runs off the bottom of the
        # screen when the window is maximised.
        self._show_btn = ttk.Menubutton(row, width=8, direction="above",
                                        text="All")
        self._menu = tk.Menu(self._show_btn, tearoff=False)
        self._show_btn.configure(menu=self._menu)
        self._show_btn.pack(side="left", padx=(4, 6))
        self._rebuild_menu()

        ttk.Button(row, text="Range...", width=9,
                   command=self.ask_range).pack(side="left", padx=(0, 14))

        ttk.Label(row, text="End of view:").pack(side="left")
        self._scrub_var = tk.DoubleVar(value=SCRUB_MAX)
        self._scrub = ttk.Scale(row, from_=0, to=SCRUB_MAX,
                                variable=self._scrub_var,
                                command=self._on_scrub)
        self._scrub.pack(side="left", fill="x", expand=True, padx=6)
        self._end_lbl = ttk.Label(row, text="LIVE", width=9,
                                  foreground=LIVE_FG)
        self._end_lbl.pack(side="left")
        ttk.Button(row, text="Go live", width=8,
                   command=self.go_live).pack(side="left", padx=(4, 0))

        self._range_lbl = ttk.Label(self, text="", foreground=HINT_FG)
        self._range_lbl.pack(anchor="w", padx=8, pady=(0, 4))

    def _rebuild_menu(self):
        """(Re)build the Show menu, marking the active choice."""
        self._menu.delete(0, "end")
        self._menu.add_command(label="All",
                               command=lambda: self.set_window(None))
        self._menu.add_separator()
        for label, seconds in self._presets:
            self._menu.add_command(
                label=label,
                command=lambda s=seconds: self.set_window(s))
        self._menu.add_separator()
        self._menu.add_command(label="Custom...", command=self.ask_window)

    # ── the view model ───────────────────────────────────────────────────

    @property
    def window_s(self):
        """Seconds visible, or None for everything held."""
        return self._window_s

    @property
    def is_live(self):
        """True when the view follows incoming data."""
        return self._view_end is None

    def set_window(self, seconds):
        """Set how much time is visible. None means everything."""
        self._window_s = None if seconds is None else float(seconds)
        self._show_btn.configure(text=format_duration(self._window_s))
        self._changed()

    def go_live(self):
        """Unpin the view so it follows incoming data again."""
        self._view_end = None
        self._changed()

    def set_range(self, start, end):
        """Pin the view to [start, end] in elapsed seconds.

        Stored as a window length plus an absolute end, so the range stays
        exactly where it was put as new samples arrive.
        """
        if end <= start:
            return False
        self._window_s = float(end - start)
        self._view_end = float(end)
        self._show_btn.configure(text=format_duration(self._window_s))
        self._changed()
        return True

    def set_span(self, t0, t1, epoch0=None):
        """Tell the widget the data's extent, in elapsed seconds.

        Call this on every redraw, before `bounds()` or `slice()`.
        """
        self._t0, self._t1 = float(t0), float(t1)
        if epoch0 is not None:
            self._epoch0 = float(epoch0)
        if self._view_end is not None:
            # A pinned end stays where it was put, but cannot sit outside
            # the data that actually exists.
            self._view_end = min(max(self._view_end, self._t0), self._t1)
        self._sync_widgets()

    def bounds(self):
        """(start, end) of the visible window, in elapsed seconds."""
        end = self._t1 if self._view_end is None else self._view_end
        if self._window_s is None:
            return self._t0, end
        return max(self._t0, end - self._window_s), end

    def slice(self, elapsed):
        """(i0, i1) into a sorted elapsed-seconds list for the current view.

        At least one sample is always returned, so a host never has to
        guard against an empty slice mid-drag.
        """
        low, high = self.bounds()
        i0 = bisect.bisect_left(elapsed, low)
        i1 = bisect.bisect_right(elapsed, high)
        if i1 <= i0:
            i1 = min(i0 + 1, len(elapsed))
            i0 = max(i1 - 1, 0)
        return i0, i1

    # ── labels ───────────────────────────────────────────────────────────

    def clock(self, elapsed, with_date=False):
        """An elapsed value as a wall-clock string, if the origin is known."""
        if self._epoch0 is None:
            return f"+{elapsed:.0f} s"
        moment = datetime.fromtimestamp(self._epoch0 + elapsed)
        return moment.strftime("%Y-%m-%d %H:%M:%S" if with_date
                               else "%H:%M:%S")

    def _sync_widgets(self):
        """Put the slider and the labels where the model says they are."""
        span = self._t1 - self._t0
        if self._view_end is None or span <= 0:
            fraction = 1.0
        else:
            fraction = (self._view_end - self._t0) / span
        target = fraction * SCRUB_MAX
        # Only write the slider when it is actually somewhere else, so a
        # pinned view does not repaint on every incoming sample.
        if abs(self._scrub_var.get() - target) > 0.5:
            self._syncing = True
            try:
                self._scrub_var.set(target)
            finally:
                self._syncing = False

        if self._view_end is None:
            self._end_lbl.configure(text="LIVE", foreground=LIVE_FG)
        else:
            self._end_lbl.configure(text=self.clock(self._view_end),
                                    foreground=PAST_FG)

        low, high = self.bounds()
        self._range_lbl.configure(
            text=f"showing  {self.clock(low)}  →  {self.clock(high)}"
                 f"   ({format_duration(max(high - low, 1e-9))})")

    # ── slider ───────────────────────────────────────────────────────────

    def _on_scrub(self, _value=None):
        if self._syncing:
            return
        fraction = self._scrub_var.get() / SCRUB_MAX
        if fraction >= 0.999:
            self._view_end = None                       # snap back to LIVE
        else:
            self._view_end = self._t0 + (self._t1 - self._t0) * fraction
        self._changed()

    def _changed(self):
        self._sync_widgets()
        if self._on_change is not None:
            self._on_change()

    # ── typed input ──────────────────────────────────────────────────────

    def ask_window(self):
        """Prompt for an arbitrary window length."""
        dialog = _Prompt(
            self.winfo_toplevel(), "Custom time window",
            [("Show the last:", format_duration(self._window_s)
              if self._window_s else "5 min")],
            hint="How much time to keep on screen.",
            extra="Examples:  45 s    2.5 min    1h30m    4 hours    1:30:00")
        self.wait_window(dialog)
        if dialog.result is None:
            return
        seconds = parse_duration(dialog.result[0])
        if seconds is None:
            self._retry(self.ask_window, "not a duration")
            return
        self.set_window(seconds)

    def ask_range(self):
        """Prompt for an explicit start and end."""
        low, high = self.bounds()
        dialog = _Prompt(
            self.winfo_toplevel(), "Custom range",
            [("Start:", self.clock(low)), ("End:", self.clock(high))],
            hint=(f"Data runs {self.clock(self._t0, with_date=True)}\n"
                  f"       to {self.clock(self._t1, with_date=True)}"),
            extra=("A clock time (14:05, 14:05:30, or with a date),\n"
                   "+2 min from the start, -30 s back from the newest,\n"
                   "or the words  start  /  now."))
        self.wait_window(dialog)
        if dialog.result is None:
            return
        start = self._parse_moment(dialog.result[0], self._t0)
        end = self._parse_moment(dialog.result[1], self._t1)
        if start is None or end is None:
            self._retry(self.ask_range, "could not read that time")
            return
        if end <= start:
            self._retry(self.ask_range, "end is not after start")
            return
        self.set_range(start, end)

    def _retry(self, again, message):
        """Say what was wrong and reopen, rather than silently doing nothing."""
        note = _Prompt(self.winfo_toplevel(), "Try again", [],
                       hint=f"{message}.")
        self.wait_window(note)
        again()

    def _parse_moment(self, text, fallback):
        """Typed text -> elapsed seconds, or None if unreadable."""
        text = text.strip()
        if not text:
            return fallback
        lowered = text.lower()
        if lowered in ("start", "begin", "first"):
            return self._t0
        if lowered in ("now", "live", "end", "last", "latest"):
            return self._t1
        if lowered.startswith("+"):
            offset = parse_duration(lowered[1:])
            return None if offset is None else self._t0 + offset
        if lowered.startswith("-"):
            offset = parse_duration(lowered[1:])
            return None if offset is None else self._t1 - offset
        if self._epoch0 is None:
            # No wall-clock origin, so a bare number can only mean elapsed.
            offset = parse_duration(lowered)
            return None if offset is None else self._t0 + offset
        return self._elapsed_from_clock(text)

    def _elapsed_from_clock(self, text):
        """A clock string -> elapsed seconds, or None."""
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(text, fmt).timestamp() - self._epoch0
            except ValueError:
                continue

        for fmt in ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M"):
            try:
                wanted = datetime.strptime(text, fmt).time()
            except ValueError:
                continue
            # A bare clock time carries no date. Try every date the session
            # touches and take the one inside it -- a run started before
            # midnight would otherwise resolve to the wrong day. If none
            # lands inside, take the nearest, so typing a time just outside
            # the data clamps to the edge instead of failing.
            first = datetime.fromtimestamp(self._epoch0 + self._t0)
            last = datetime.fromtimestamp(self._epoch0 + self._t1)
            best = None
            day = first.date()
            while day <= last.date():
                candidate = (datetime.combine(day, wanted).timestamp()
                             - self._epoch0)
                if self._t0 <= candidate <= self._t1:
                    return candidate
                distance = max(self._t0 - candidate, candidate - self._t1)
                if best is None or distance < best[0]:
                    best = (distance, candidate)
                day += timedelta(days=1)
            return None if best is None else best[1]
        return None
