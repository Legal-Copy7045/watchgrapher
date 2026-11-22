# WatchGrapher standalone (ESP32-S3)

A pocket version of WatchGrapher: an ESP32-S3 board with a microphone or
contact pickup that measures a watch on its own and serves the app to your
phone over its own Wi-Fi hotspot. No computer, no internet, no app to install.

It runs the same measurement chain as the desktop app -- a C++ port of
`watchgrapher/dsp.py` and the measurement part of `analysis.py`, checked
against the Python on synthetic watches (see *Checking the port* below) --
and stores watches and results in the desktop's own `collection.json`
format, so they move between the two.

## What it does

- **Live measurement**: rate (with 95% confidence), amplitude, beat error and
  beat rate, the timegrapher trace, the averaged beat waveform with the
  unlocking / drop markers, and a rate / amplitude history
- **Timed runs** with a settling period, and a **six-position check** with
  mean, delta and flat-to-vertical amplitude drop
- **Power-reserve logging** that runs on the device by itself -- close the
  page, come back in a day. Stops when the watch runs down, shows the
  *power reserve* (135 deg) and *good time to* (200 deg) figures, and survives
  a power cut (the log is closed off and kept)
- **Watches**: add / edit / archive, caliber picker over the full desktop
  caliber database (lift angle and beat rate set automatically), history
- **Desktop sync**: load your desktop `collection.json` onto the device;
  merge device results back with `tools/merge_device.py`
- **Simulated watch** for trying it with no pickup fitted
- WAV download of the last few seconds (analyse it in the desktop app),
  firmware update from the page, a status LED, auto-gain and clipping guard

Not on the device (yet): self-tune, the fault scan, Allan deviation, reports
and certificates, the advice engine. Do those on the desktop after merging.

## Hardware

| Part | Notes |
|---|---|
| **ESP32-S3 with 8 MB PSRAM** | ESP32-S3-DevKitC-1 **N16R8** (or N8R8), or any S3 board with an N16R8 / N8R8 module. The PSRAM is not optional: it holds the analysis window. |
| **Pickup, option A: I2S MEMS mic** | INMP441 or ICS-43434 breakout. Simplest. Press the watch's case back onto the mic port, or put the mic in a small foam-lined cradle. |
| **Pickup, option B: piezo + I2S ADC** | A 20-35 mm piezo disc against the case back (what real timegraphers use), through a high-impedance preamp into a PCM1808 I2S ADC module. Best amplitude readings. The PCM1808 needs MCLK. |
| Power | USB, or a LiPo on a board with a charger. |

### Wiring (defaults -- all changeable in Settings)

| Signal | ESP32-S3 GPIO | INMP441 | PCM1808 |
|---|---|---|---|
| Bit clock | 5 | SCK | BCK |
| Word select | 6 | WS | LRC |
| Data in | 4 | SD | OUT |
| Master clock | -1 (off) -- set e.g. 7 | -- | SCK (system clock) |
| L/R select | -- | GND (left) | FMT to GND (I2S) |
| Power | 3V3 / GND | VDD / GND | 3.3 V / 5 V per module |

The on-board RGB LED (GPIO 48 on DevKitC-1 v1.0, 38 on v1.1) shows status:
blue idle, green good reading, amber rate only (amplitude not resolved),
purple listening but no watch found, red clipping.

## Build and flash

With [PlatformIO](https://platformio.org) (`pip install platformio`):

```
cd esp32
pio run -e esp32s3 -t upload       # N16R8; use -e esp32s3-n8r8 for 8 MB flash
pio device monitor                  # optional: serial log
```

The web app in `web/` is gzipped into the firmware at build time, so this is
the only upload. Later updates can go over Wi-Fi: *Settings -> Update
firmware*, choosing `.pio/build/esp32s3/firmware.bin`.

## First use

1. Power it up. Join the Wi-Fi network **WatchGrapher-XXXX**, password
   **watchgrapher**.
2. Open **http://192.168.4.1**. (Phones may say the network has no internet;
   stay connected.)
3. *Settings*: change the hotspot password. Optionally enter your home Wi-Fi
   under *Join network* -- the device then also appears at
   http://watchgrapher.local and sets its clock by NTP.
4. *Watches*: add a watch and pick its caliber, or load your desktop
   collection (`watches/collection.json` in the WatchGrapher folder).
5. *Measure*: choose the watch at the top, put it on the pickup, press
   **Listen**.

No pickup yet? *Settings -> Source -> Simulated watch* and dial in a rate,
amplitude and beat error to see the whole thing work.

The page sends your phone's clock with each request (the board has no
real-time clock), so results are stamped with the right time even on the
hotspot.

## Bringing results back to the desktop

Close WatchGrapher on the computer, join the device's hotspot (or have the
device on your home network) and run:

```
python esp32/tools/merge_device.py --device http://192.168.4.1
```

or download the collection from the *Watches* tab and pass the file instead
of the URL. It adds new watches and any test, service and power-reserve
records the desktop does not already have, backs up `collection.json` first,
and never changes or removes desktop data. The desktop recomputes
power-reserve figures from the raw samples with its fuller model.

## How it fits on the chip

- **Core 0** runs Wi-Fi and the audio task: I2S at 48 kHz, a DC blocker and
  makeup gain into a 16-bit ring buffer in PSRAM.
- **Core 1** runs the web server and the analysis task. The analysis copies
  the newest window (up to ~13 s on 8 MB PSRAM, 12 s by default) and runs the
  full chain about twice a second.
- The analyzer needs two floats per sample (band-passed signal and envelope),
  allocated once in PSRAM.

Differences from the Python, all to fit memory: the beat-period
autocorrelation runs on the envelope decimated to ~4 kHz over the newest 8 s,
evaluated only over the plausible lag range (the desktop's full-length FFT
would need ~16 MB); robust noise-floor statistics use a strided subsample;
no coarse spectrum. The period is only a starting estimate -- the template
refinement and least-squares fit set the reported figures, and they agree
with the Python to within 0.001 s/day, ms and degree on the test grid.

A timed run longer than the window is reported as the mean of the window
readings taken during it, with the confidence interval scaled by the number
of non-overlapping windows the run spans.

## Development

```
# DSP core on the host, checked against the Python analyzer
cmake -S esp32/host -B build/host && cmake --build build/host
python esp32/host/compare.py build/host/wgcli

# The web app against a stand-in for the device (Python analyzer + simulated watch)
python esp32/host/devserver.py --port 8080 --speed 600
```

`devserver.py` imitates the firmware API so the page can be developed in a
desktop browser; `--speed` fast-forwards power-reserve logs.
`tools/export_calibers.py` regenerates `web/calibers.json` from the desktop
caliber database.

| Path | What |
|---|---|
| `lib/wgdsp/` | Portable DSP core (`wgdsp`) and simulated watch (`wgsim`) |
| `src/` | Firmware: audio capture, engine (runs / power reserve), web API, settings |
| `web/` | The single-page app served by the device |
| `host/` | Host build, Python comparison, dev server |
| `tools/` | Asset embedding, caliber export, desktop merge |
