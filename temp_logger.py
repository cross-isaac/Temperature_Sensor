"""
RTD Temperature Logger
----------------------
Reads RTD temperature data from Board 2 over USB serial,
plots it live, and saves to a CSV file.

USAGE:
    python rtd_logger.py

REQUIREMENTS:
    pip install pyserial matplotlib
"""

import os
import csv
import time
import serial
from datetime import datetime
import serial.tools.list_ports
from lab_time import lab_timestamp, session_header
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from collections import deque

BAUD_RATE  = 115200  # must match Serial.begin() in Board 2 code
MAX_POINTS = 200     # how many points to show in the live plot window


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
    port = input("Enter COM port (e.g. COM6): ").strip()
    return port


def prompt_csv_path():
    print()
    folder = input("Enter folder path to save CSV (e.g. C:\\Users\\isaac\\Documents): ").strip().strip('"')
    filename = input("Enter file name (e.g. run1.csv): ").strip()
    if not filename.endswith(".csv"):
        filename += ".csv"
    full_path = os.path.join(folder, filename)
    return full_path


def main():
    print("=== RTD Temperature Logger ===")

    # Prompt for COM port
    port = prompt_port()
    try:
        ser = serial.Serial(port, BAUD_RATE, timeout=1)
        print(f"\nOpened {port} at {BAUD_RATE} baud.")
    except serial.SerialException as e:
        print(f"\nERROR: Could not open {port}.\n  {e}")
        print("Check the port name and make sure the serial monitor is closed.")
        return

    # Prompt for CSV save path
    csv_path = prompt_csv_path()
    try:
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        csv_file = open(csv_path, "w", newline="")
    except Exception as e:
        print(f"\nERROR: Could not create file at {csv_path}.\n  {e}")
        ser.close()
        return

    # Session origin for elapsed_s, fixed before the header is written so
    # the two name the same instant. See lab_time.py for the convention.
    start_epoch = time.time()
    csv_file.write(session_header("RTD temperature log", start_epoch))
    writer = csv.writer(csv_file)
    writer.writerow(["timestamp", "elapsed_s", "tempC"])
    csv_file.flush()
    print(f"\nLogging to: {csv_path}")
    print("Close the plot window to stop.\n")

    # Rolling buffers for the live plot
    times  = deque(maxlen=MAX_POINTS)
    temps  = deque(maxlen=MAX_POINTS)
    labels = deque(maxlen=MAX_POINTS)

    # ——— Set up the plot — clean default matplotlib style ———
    fig, ax = plt.subplots(figsize=(10, 5))
    line, = ax.plot([], [], color="steelblue", linewidth=1.5, marker="o", markersize=3)

    ax.set_title("RTD Temperature — Live")
    ax.set_xlabel("Time")
    ax.set_ylabel("Temperature (°C)")
    ax.grid(True, linestyle="--", alpha=0.5)
    fig.tight_layout(pad=2)

    def update(_frame):
        # Drain everything currently in the serial buffer
        while ser.in_waiting:
            try:
                raw = ser.readline().decode("utf-8", errors="ignore").strip()
            except Exception:
                continue

            if raw.startswith("T,"):
                try:
                    temp_str = raw.split(",")[1].strip()
                    tempC = float(temp_str)
                    # One wall-clock reading feeds both CSV time columns
                    # and the plot's tick label.
                    epoch = time.time()
                    ts = datetime.fromtimestamp(epoch).strftime("%H:%M:%S")
                    elapsed = epoch - start_epoch

                    times.append(elapsed)
                    temps.append(tempC)
                    labels.append(ts)

                    # Write to CSV
                    writer.writerow([lab_timestamp(epoch), f"{elapsed:.3f}",
                                     f"{tempC:.2f}"])
                    csv_file.flush()

                    print(f"[{ts}]  {tempC:.2f} °C")

                except (ValueError, IndexError):
                    pass

            elif "FAULT" in raw:
                print(f"  !! {raw}")

        # Update plot data
        if times:
            line.set_data(list(times), list(temps))
            ax.relim()
            ax.autoscale_view()

            # Show last few time labels on x-axis
            tick_indices = range(0, len(times), max(1, len(times) // 6))
            ax.set_xticks([list(times)[i] for i in tick_indices])
            ax.set_xticklabels([list(labels)[i] for i in tick_indices], rotation=30, ha="right", fontsize=8)

        return line,

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
