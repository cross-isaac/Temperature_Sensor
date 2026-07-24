# Temperature_Sensor

ESP32 temperature acquisition for RTDs (MAX31865 / PT100) and K-type
thermocouples (MAX31856), with Python tools on the PC side for live plotting and
CSV logging over USB serial. See the root `../CLAUDE.md` for the git and physics
rules.

## Two halves

**Firmware** (PlatformIO, ESP32 + Arduino framework). PlatformIO compiles every
`.cpp` in `src/`, so **only `main.cpp` is actually built.** The other files in
`src/` and everything in `test/` are reference sketches deliberately kept
*without* a `.cpp` extension so they don't all compile into one binary. To flash
one, copy its contents into `main.cpp`. Note `test/` holds archived firmware
variants, **not** unit tests.

`main.cpp` is currently a 2.4 GHz WiFi channel scanner, not a temperature
sketch. Check before assuming.

**PC side.** `temp_gui.py` is the recommended tool — a Tkinter GUI that handles
any number of sensors. The CLI loggers (`temp_logger_arb.py`, `temp_logger_4.py`,
`temp_logger.py`) remain for scripted use. All four write CSV through the same
helpers in `lab_time.py` — keep it that way rather than reintroducing a
per-script time format.

```
py -m pip install -r requirements.txt
py temp_gui.py
```

Close the PlatformIO serial monitor first — two programs cannot share a COM port.

## Serial protocol (115200 baud)

Shared by the GUI and all CLI loggers:

```
Header (optional):  T,RTD,TC1,TC2,TC3        fields are non-numeric names
Data:               T,23.45,150.2,148.9,nan  one float per sensor; nan ok
Fault:              FAULT_RTD,0x80           reported, not plotted
```

**Sensor count is inferred from the data rows, not hardcoded** — the same GUI
handles 1 sensor or 20. A name header only pre-fills the editable name fields.
Keep that inference; don't reintroduce a fixed channel count.

Sensors share the VSPI bus, each with its own chip-select line (SCK 18, MOSI 23,
MISO 19 in `FullOven4Sensors`).

## Timestamps

`lab_time.py` owns the format, and it is the lab-wide one: every row carries
`timestamp` (local wall clock `YYYY-MM-DD HH:MM:SS.mmm`) and `elapsed_s`, both
naming the instant the sample arrived, both off the same clock, so
`timestamp[i] - timestamp[0] == elapsed_s[i]` holds. That invariant is what
makes a temperature trace alignable against the ion-gauge pressure log or a
scope frame index on `timestamp` alone — don't break it by taking two separate
clock readings for one sample.

The old bare `HH:MM:SS` column is gone. It could not survive midnight, had no
date for a multi-day bake, and no sub-second resolution to match a 1 Hz scope
frame. Do not reintroduce it.

## Never tracked

`*.csv` from any logger, `data/`, `__pycache__/`, PlatformIO's `.pio/`.
`temp_data.csv` was tracked historically and has been untracked — it stays on
disk but must not come back.
