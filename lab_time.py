"""
The lab timestamp convention, shared by the GUI and the CLI loggers.

Every logged row in this lab -- temperature, pressure, flow, scope frames,
stage positions -- carries the same two time columns, and both describe the
instant the sample was *acquired*, never the instant the row happened to be
written:

    timestamp   local wall clock, "YYYY-MM-DD HH:MM:SS.mmm"
    elapsed_s   seconds since this file's session start, %.3f

Both come off the same wall clock, so ``timestamp[i] - timestamp[0]`` equals
``elapsed_s[i]`` exactly. That invariant is the point: it is what makes it
valid to line a temperature trace up against an ion-gauge pressure log or a
scope frame index on the ``timestamp`` column alone, with no epoch column and
no guesswork.

``time.monotonic()`` is the right clock for pacing a loop or timing a dwell --
it cannot be stepped by NTP -- but it means nothing in another program's file,
so it is never written to one.

This replaced a bare "HH:MM:SS" column, which could not survive midnight,
carried no date to align a multi-day bake against, and had no sub-second
resolution to match a 1 Hz scope frame with.

The same three functions appear (as a deliberate copy, not an import) in
Rigol_Scope_Control, Rb_experiment_runner, Ion_Gauge_Pressure_Monitor and
Alicat_Flow_Monitor, so each of those repos stays standalone. If the format
changes, it changes in all five.
"""

from datetime import datetime


def lab_timestamp(epoch=None):
    """Local wall clock as 'YYYY-MM-DD HH:MM:SS.mmm'.

    Pass the sample's own ``time.time()`` as `epoch` so the string names the
    acquisition instant instead of whenever this was called.
    """
    dt = datetime.now() if epoch is None else datetime.fromtimestamp(epoch)
    return dt.isoformat(sep=" ", timespec="milliseconds")


def timezone_note():
    """'Central Daylight Time (UTC-05:00)' -- what the timestamps mean.

    Recorded once in the header rather than on every row: it is what keeps a
    local wall-clock string interpretable months later, including across a
    daylight-saving change.
    """
    local = datetime.now().astimezone()
    off = local.strftime("%z")                  # '-0500'
    return f"{local.tzname()} (UTC{off[:3]}:{off[3:]})"


def session_header(title, t0_epoch):
    """The '#' provenance block every lab CSV opens with, as one string.

    ``pd.read_csv(path, comment="#")`` skips it and parses the data cleanly;
    a plain ``csv.reader`` needs the '#' lines filtered out first.
    """
    return (
        f"# {title}\n"
        f"# session_start: {lab_timestamp(t0_epoch)}\n"
        f"# timezone: {timezone_note()}\n"
        f"# timestamp = local wall clock when the sample arrived, "
        f"elapsed_s = seconds since session_start\n"
    )
