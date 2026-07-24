"""
4-Sensor Temperature Logger
----------------------------
Reads all 4 sensor temperatures from the ESP32 over USB serial,
plots them live on one chart, and saves to a CSV file.

Serial format expected from ESP32:
    T,<RTD>,<TC1>,<TC2>,<TC3>
    e.g.  T,23.45,150.22,148.91,22.10
    NaN sensors are printed as "nan"

USAGE:
    python temp_logger.py

REQUIREMENTS:
    pip install pyserial matplotlib
"""

import os
import csv
import math
import time
import serial
from datetime import datetime
import serial.tools.list_ports
from lab_time import lab_timestamp, session_header
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from collections import deque

BAUD_RATE  = 115200
MAX_POINTS = 200   # rolling window of points shown in the live plot

SENSOR_NAMES  = ["RTD", "TC1", "TC2", "TC3"]
SENSOR_COLORS = ["steelblue", "tomato", "seagreen", "darkorange"]


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


def safe_float(s):
    """Return float or None for 'nan' / bad values."""
    try:
        v = float(s.strip())
        return None if math.isnan(v) else v
    except ValueError:
        return None


def main():
    print("=== 4-Sensor Temperature Logger ===")

    # ——— Open serial port ———
    port = prompt_port()
    try:
        ser = serial.Serial(port, BAUD_RATE, timeout=1)
        print(f"\nOpened {port} at {BAUD_RATE} baud.")
    except serial.SerialException as e:
        print(f"\nERROR: Could not open {port}.\n  {e}")
        print("Check the port name and make sure the serial monitor is closed.")
        return

    # ——— Open CSV ———
    csv_path = prompt_csv_path()
    try:
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        csv_file = open(csv_path, "w", newline="")
    except Exception as e:
        print(f"\nERROR: Could not create file at {csv_path}.\n  {e}")
        ser.close()
        return

    # Session origin for elapsed_s, fixed before the header is written
    # so the two name the same instant. See lab_time.py.
    start_epoch = time.time()
    csv_file.write(session_header('4-sensor temperature log', start_epoch))
    writer = csv.writer(csv_file)
    writer.writerow(["timestamp", "elapsed_s", "RTD_C", "TC1_C", "TC2_C", "TC3_C"])
    csv_file.flush()
    print(f"\nLogging to: {csv_path}")
    print("Close the plot window to stop.\n")
    print(f"{'Time':>10}  {'RTD':>8}  {'TC1':>8}  {'TC2':>8}  {'TC3':>8}")
    print("-" * 52)

    # ——— Rolling buffers — one deque per sensor ———
    times  = deque(maxlen=MAX_POINTS)
    labels = deque(maxlen=MAX_POINTS)
    data   = [deque(maxlen=MAX_POINTS) for _ in SENSOR_NAMES]

    # ——— Plot setup ———
    fig, ax = plt.subplots(figsize=(12, 5))
    lines = [
        ax.plot([], [], color=c, linewidth=1.5, marker="o", markersize=3, label=n)[0]
        for n, c in zip(SENSOR_NAMES, SENSOR_COLORS)
    ]
    ax.set_title("Temperature — Live (all 4 sensors)")
    ax.set_xlabel("Time")
    ax.set_ylabel("Temperature (°C)")
    ax.legend(loc="upper left")
    ax.grid(True, linestyle="--", alpha=0.5)
    fig.tight_layout(pad=2)

    def update(_frame):
        while ser.in_waiting:
            try:
                raw = ser.readline().decode("utf-8", errors="ignore").strip()
            except Exception:
                continue

            # Skip header line or empty lines
            if not raw.startswith("T,"):
                if "FAULT" in raw:
                    print(f"  !! {raw}")
                continue

            parts = raw.split(",")
            # Expect: T, RTD, TC1, TC2, TC3  (5 fields)
            if len(parts) != 5:
                continue

            vals = [safe_float(p) for p in parts[1:]]

            # One wall-clock reading feeds both CSV time columns and
            # the plot's tick label.
            epoch   = time.time()
            ts      = datetime.fromtimestamp(epoch).strftime("%H:%M:%S")
            elapsed = epoch - start_epoch

            times.append(elapsed)
            labels.append(ts)
            for i, v in enumerate(vals):
                # Store NaN-as-None as float nan so the deque stays aligned
                data[i].append(float("nan") if v is None else v)

            # CSV row — empty string for missing values
            row = ([lab_timestamp(epoch), f"{elapsed:.3f}"]
                   + [f"{v:.2f}" if v is not None else "" for v in vals])
            writer.writerow(row)
            csv_file.flush()

            # Console printout
            def fmt(v):
                return f"{v:>8.2f}" if v is not None else f"{'---':>8}"
            print(f"{ts:>10}  {fmt(vals[0])}  {fmt(vals[1])}  {fmt(vals[2])}  {fmt(vals[3])}")

        # Update plot lines
        if times:
            t_list = list(times)
            for i, line in enumerate(lines):
                line.set_data(t_list, list(data[i]))
            ax.relim()
            ax.autoscale_view()

            tick_indices = range(0, len(times), max(1, len(times) // 6))
            ax.set_xticks([t_list[i] for i in tick_indices])
            ax.set_xticklabels(
                [list(labels)[i] for i in tick_indices],
                rotation=30, ha="right", fontsize=8
            )

        return lines

    ani = animation.FuncAnimation(fig, update, interval=500, blit=False, cache_frame_data=False)

    try:
        plt.show()
    finally:
        print("\nClosing — saving CSV...")
        csv_file.close()
        ser.close()
        print(f"Saved: {csv_path}")


if __name__ == "__main__":
    main()
