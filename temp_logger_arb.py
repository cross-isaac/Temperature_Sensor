"""
Universal Temperature Logger
------------------------------
Works with any number of sensors as long as the ESP32 sends:
  - A header line first:   T,RTD1,RTD2        (or T,RTD,TC1,TC2,TC3 etc.)
  - Then data lines:       T,23.45,24.01

USAGE:
    python temp_logger.py

REQUIREMENTS:
    pip install pyserial matplotlib
"""

import os
import csv
import math
import serial
from datetime import datetime
import serial.tools.list_ports
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from collections import deque

BAUD_RATE  = 115200
MAX_POINTS = 200

COLORS = ["steelblue", "tomato", "seagreen", "darkorange",
          "mediumpurple", "gold", "deeppink", "cyan"]


def list_ports():
    ports = serial.tools.list_ports.comports()
    if ports:
        print("Available COM ports:")
        for p in ports:
            print(f"  {p.device} — {p.description}")
    else:
        print("No COM ports found.")


def prompt_port():
    print()
    list_ports()
    print()
    return input("Enter COM port (e.g. COM6): ").strip()


def prompt_csv_path():
    print()
    folder = input("Enter folder path to save CSV (e.g. C:\\Users\\isaac\\Documents): ").strip().strip('"')
    filename = input("Enter file name (e.g. run1.csv): ").strip()
    if not filename.endswith(".csv"):
        filename += ".csv"
    return os.path.join(folder, filename)


def prompt_sensor_names(detected_names):
    """Ask user for a friendly name for each detected sensor.
    Pressing Enter keeps the default name from the ESP32 header."""
    print()
    print("Name each sensor (press Enter to keep the default):")
    custom_names = []
    for default in detected_names:
        raw = input(f"  {default} → ").strip()
        custom_names.append(raw if raw else default)
    return custom_names


def safe_float(s):
    try:
        v = float(s.strip())
        return None if math.isnan(v) else v
    except ValueError:
        return None


def wait_for_header(ser):
    """Read lines until we get the T,...  header, return column names."""
    print("Waiting for sensor header from ESP32...")
    while True:
        try:
            raw = ser.readline().decode("utf-8", errors="ignore").strip()
        except Exception:
            continue
        if raw.startswith("T,"):
            cols = raw.split(",")[1:]
            if cols:
                print(f"Detected {len(cols)} sensor(s): {', '.join(cols)}\n")
                return cols


def main():
    print("=== Universal Temperature Logger ===")

    port = prompt_port()
    try:
        ser = serial.Serial(port, BAUD_RATE, timeout=1)
        print(f"\nOpened {port} at {BAUD_RATE} baud.")
    except serial.SerialException as e:
        print(f"\nERROR: Could not open {port}.\n  {e}")
        print("Check the port name and make sure the serial monitor is closed.")
        return

    detected_names = wait_for_header(ser)
    n = len(detected_names)

    sensor_names = prompt_sensor_names(detected_names)
    print(f"\nUsing names: {', '.join(sensor_names)}\n")

    csv_path = prompt_csv_path()
    try:
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        csv_file = open(csv_path, "w", newline="")
    except Exception as e:
        print(f"\nERROR: Could not create file at {csv_path}.\n  {e}")
        ser.close()
        return

    writer = csv.writer(csv_file)
    writer.writerow(["time"] + [f"{s}_C" for s in sensor_names])
    csv_file.flush()
    print(f"Logging to: {csv_path}")
    print("Close the plot window to stop.\n")

    # Console header
    print(f"{'Time':>10}  " + "  ".join(f"{s:>10}" for s in sensor_names))
    print("-" * (12 + 12 * n))

    # Rolling buffers
    times  = deque(maxlen=MAX_POINTS)
    labels = deque(maxlen=MAX_POINTS)
    data   = [deque(maxlen=MAX_POINTS) for _ in range(n)]

    start_time = datetime.now()

    # ——— Subplots: one per sensor, shared x-axis ———
    fig, axes = plt.subplots(
        nrows=n, ncols=1,
        figsize=(12, 3 * n),
        sharex=True          # <— this is what lines up the time axis
    )
    # If only 1 sensor, axes is not a list — normalise it
    if n == 1:
        axes = [axes]

    lines = []
    for i, (ax, name, color) in enumerate(zip(axes, sensor_names, COLORS)):
        line, = ax.plot([], [], color=color, linewidth=1.5, marker="o", markersize=3)
        ax.set_ylabel(f"{name}\n(°C)", fontsize=9)
        ax.grid(True, linestyle="--", alpha=0.5)
        ax.set_title(name, fontsize=10)
        lines.append(line)

    axes[-1].set_xlabel("Time")
    fig.suptitle("Temperature — Live", fontsize=12)
    fig.tight_layout(pad=2)

    def update(_frame):
        while ser.in_waiting:
            try:
                raw = ser.readline().decode("utf-8", errors="ignore").strip()
            except Exception:
                continue

            if not raw.startswith("T,"):
                if "FAULT" in raw:
                    print(f"  !! {raw}")
                continue

            parts = raw.split(",")
            if len(parts) != n + 1:
                continue

            vals = [safe_float(p) for p in parts[1:]]

            now     = datetime.now()
            ts      = now.strftime("%H:%M:%S")
            elapsed = (now - start_time).total_seconds()

            times.append(elapsed)
            labels.append(ts)
            for i, v in enumerate(vals):
                data[i].append(float("nan") if v is None else v)

            writer.writerow([ts] + [f"{v:.2f}" if v is not None else "" for v in vals])
            csv_file.flush()

            def fmt(v):
                return f"{v:>10.2f}" if v is not None else f"{'---':>10}"
            print(f"{ts:>10}  " + "  ".join(fmt(v) for v in vals))

        if times:
            t_list = list(times)
            for i, line in enumerate(lines):
                line.set_data(t_list, list(data[i]))
                axes[i].relim()
                axes[i].autoscale_view()

            # x-axis tick labels only on the bottom subplot (sharex handles the rest)
            tick_indices = range(0, len(times), max(1, len(times) // 6))
            axes[-1].set_xticks([t_list[i] for i in tick_indices])
            axes[-1].set_xticklabels(
                [list(labels)[i] for i in tick_indices],
                rotation=30, ha="right", fontsize=8
            )

        return lines

    ani = animation.FuncAnimation(fig, update, interval=500,
                                  blit=False, cache_frame_data=False)
    try:
        plt.show()
    finally:
        print("\nClosing — saving CSV...")
        csv_file.close()
        ser.close()
        print(f"Saved: {csv_path}")


if __name__ == "__main__":
    main()
