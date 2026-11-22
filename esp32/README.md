# WatchGrapher standalone (ESP32-S3)

A pocket version of WatchGrapher: an ESP32-S3 board with a microphone or
contact pickup -- or a USB-C microphone -- that measures a watch on its own and
serves the app to your phone or computer over its own Wi-Fi hotspot. No computer, no internet, no app to install.

It runs the same measurement chain as the desktop app -- a C++ port of
`watchgrapher/dsp.py` and the measurement part of `analysis.py`, checked
against the Python on synthetic watches (see *Checking the port* below) --
and stores watches and results in the desktop's own `collection.json`
format, so they move between the two.

## Install from your browser

Open **https://legal-copy7045.github.io/watchgrapher/** in Chrome or Edge on a
computer, plug the board in (the `USB` port needs no driver; a `UART` port with
a CH340 chip often fails to start an install) and click **Install**. If it says
*Failed to initialize*, hold `BOOT`, tap `RST`, release `BOOT` and click Install
again -- and when it has finished, **press `RST`** to start WatchGrapher (until
then the board sits in its bootloader: the log says *waiting for download*).
The page then offers Wi-Fi setup to put the device on your network.
Later updates: *Settings -> Check for updates* on the device.

(The page is published by `.github/workflows/esp32.yml` from `main`. It needs
GitHub Pages turned on once: repository *Settings -> Pages -> Source: GitHub
Actions*. To publish a release with the binaries attached, run that workflow
from the Actions tab with *release* ticked.)

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
- **Desktop sync** over your Wi-Fi: finds the WatchGrapher desktop app by
  itself, pairs once with a code, then keeps both collections in step
- **Simulated watch** for trying it with no pickup fitted
- WAV download of the last few seconds (analyse it in the desktop app),
  firmware update from the page, a status LED, auto-gain and clipping guard

Not on the device (yet): self-tune, the fault scan, Allan deviation, reports
and certificates, the advice engine. Do those on the desktop after merging.

## Hardware

| Part | Notes |
|---|---|
| **ESP32-S3 with 8 MB PSRAM** | ESP32-S3-DevKitC-1 **N16R8** (or N8R8), or any S3 board with an N16R8 / N8R8 module. The PSRAM is not optional: it holds the analysis window. |
| **Pickup, option A: USB-C microphone** | A USB-C lavalier or desk mic in the board's `USB` (OTG) port -- see *USB-C microphone* below. No soldering. |
| **Pickup, option B: I2S MEMS mic** | INMP441 or ICS-43434 breakout. Simplest. Press the watch's case back onto the mic port, or put the mic in a small foam-lined cradle. |
| **Pickup, option C: piezo + I2S ADC** | A 20-35 mm piezo disc against the case back (what real timegraphers use), through a high-impedance preamp into a PCM1808 I2S ADC module. Best amplitude readings. The PCM1808 needs MCLK. |
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
blue idle (dim white while storage is being prepared on the first start after
installing -- a few minutes; the hotspot and app work meanwhile), green good reading, amber rate only (amplitude not resolved),
purple listening but no watch found, red clipping.

## USB-C microphone

The S3's native USB port (labelled `USB` on a DevKitC-1; the other port is
`UART`) works as a USB host and reads a standard USB microphone: anything that
works on a computer without a driver (USB Audio Class 1 or 2) and offers PCM
at 32-48 kHz. Nearly all USB-C lavalier and desk mics do. Choose *Settings ->
Source -> USB microphone*; the Measure tab then shows the mic's name and
format, or why nothing is streaming. Plugging and unplugging is handled.

**Power.** The mic draws its power from the port, so the port has to supply
5 V. Power the board from its `5V` and `G` pins (your breakout base), and
check that the 5 V actually reaches the USB-C socket: many S3 boards put a
diode between each USB socket and the 5 V rail so a socket can power the board
but not the other way round. Signs it doesn't: the mic's LED (if it has one)
stays off and the status reads *No USB microphone connected*. A meter between
VBUS and GND on the socket settles it. Fixes, easiest first:

- a solder jumper next to the socket for exactly this, where the board has one
  (often marked `USB-OTG` or `IN-OUT`) -- bridge it;
- a USB-C OTG adapter or Y-cable that injects 5 V into the mic side;
- bridging the diode on that socket's VBUS line.

Never feed 5 V into the `5V` pin while a computer is also plugged into a USB
socket that back-feeds the same rail.

**Things to know**

- While the USB mic is the source, the native port is a host: it is no longer a
  serial port, and the browser installer can't reach the board through it.
  Always install through the `UART` port (or hold `BOOT` while plugging in).
- The S3 is a full-speed USB host (12 Mbit/s). High-speed-only interfaces are
  not seen; mics that offer several rates are set to 48 kHz, or 44.1 kHz if
  that is all they do (the analysis follows).
- The mic's own gain is left alone; the auto-gain on the device does the rest.
  A mic with an on-board gain switch should be set to its highest setting that
  doesn't clip -- the clipping guard turns the LED red.

## Build and flash

With [PlatformIO](https://platformio.org) (`pip install platformio`):

```
cd esp32
pio run -e esp32s3 -t upload       # N16R8; use -e esp32s3-n8r8 for 8 MB flash
pio device monitor                  # optional: serial log
```

The web app in `web/` is gzipped into the firmware at build time, so this is
the only upload. The build also writes `firmware.factory.bin` (everything
merged, flashed at offset 0 -- what the browser installer uses). Later updates
can go over Wi-Fi: *Settings -> Update firmware* with
`.pio/build/esp32s3/firmware.bin`; it asks for the hotspot password, so nobody
else on a shared network can reflash the device. The version lives in
`esp32/VERSION`.

## First use

1. Power it up. Join the Wi-Fi network **WatchGrapher-XXXX**, password
   **watchgrapher**.
2. Open **http://192.168.4.1**. (Phones may say the network has no internet;
   stay connected.)
3. *Settings*: change the hotspot password, and enter your home Wi-Fi under
   *Join network* if you'd like it to be a device on your network: after a
   restart it is at http://watchgrapher.local (Settings shows its address
   too) and sets its clock by NTP. With *Hotspot only when the home network
   can't be reached* ticked (the default), its own hotspot switches off about
   20 s after joining and comes back within a minute whenever that network is
   out of reach -- so away from home it works exactly as before.
4. *Watches*: add a watch and pick its caliber, or load your desktop
   collection (`watches/collection.json` in the WatchGrapher folder).
5. *Measure*: choose the watch at the top, put it on the pickup, press
   **Listen**.

No pickup yet? *Settings -> Source -> Simulated watch* and dial in a rate,
amplitude and beat error to see the whole thing work.

The page sends your phone's clock with each request (the board has no
real-time clock), so results are stamped with the right time even on the
hotspot.

## Syncing with the desktop app

With the device on your home Wi-Fi and WatchGrapher running on a computer on
the same network, the device finds the computer by itself.

1. On the computer, open the **Phone Portal** tab. Under *WatchGrapher
   devices* it shows a 6-digit **pairing code** (and *Let devices on this
   network sync with this computer*, on by default).
2. On the device, open **Watches**. The computer is listed under *Sync with
   the desktop app*: type the code and press **Pair**. It syncs straight away.
3. From then on it syncs by itself -- when the computer appears on the
   network, every 15 minutes, and a minute after you change something -- or
   press **Sync now**.

A sync only ever **adds**: new watches, and test, service and power-reserve
records the other side doesn't have (matched by when they were taken).
Nothing is changed or deleted on the computer; the device then gets the
computer's whole collection, so both hold everything. Edits to a watch's
details are taken from the computer. Power-reserve logs reach the computer
once you have saved them to a watch on the device.

How it works: the desktop broadcasts a small UDP beacon (port 8479) and serves
pairing and sync on port 8478, on its LAN address only. Pairing needs the code
on the computer's screen (it changes after each use; five wrong tries lock
pairing for a minute) and gives the device a token; *Forget all devices*
revokes them. The merge runs inside the app, so the app can't later save over
it. Windows may ask once whether to let Python accept connections on private
networks -- allow it.

**Without a network**, by file: download the device's collection (*Watches ->
Without a network: by file*) and, with WatchGrapher closed, run

```
python esp32/tools/merge_device.py --device device_collection.json
```

(or `--device http://192.168.4.1` from a computer joined to the hotspot). It
merges the same way, backing up `collection.json` first.

## How it fits on the chip

- **Core 0** runs Wi-Fi and the audio task: I2S at 48 kHz, a DC blocker and
  makeup gain into a 16-bit ring buffer in PSRAM.
- **Core 1** runs the analysis task (the web server has its own task, above
  it). The analysis is built for the chip rather than ported as-is:
  - **Streaming.** Each new block of audio is band-passed and turned into an
    envelope once, as it arrives -- zero-phase, with a margin of real audio
    each side, so the values match a whole-window filter -- and kept in a
    sliding buffer. An update only runs the measurement stages; nothing is
    re-filtered.
  - **Kept indexes.** The stream also keeps the envelope's maximum per
    256-sample block and a 2 kHz copy for the beat-period search, so an update
    reads only the blocks that hold beats, not the whole window.
  - **SIMD.** Every correlation runs on esp-dsp's vector dot product (checked
    against a plain loop at boot; Diagnostics shows whether it is on).
  - **Exactness.** `host/stream_check.py` checks the streaming path against
    the whole-window analysis, and `host/compare.py` the whole-window analysis
    against the desktop's Python. On a PC the streaming update is about a
    sixth of the work of the original whole-window one; on the board the
    saving is larger, since most of what it removed was slow PSRAM traffic.
    Diagnostics shows the time per stage on the device.
- A USB microphone is serviced by the USB host library's own tasks on core 0.
- The analyzer needs two floats per sample (band-passed signal and envelope),
  allocated once in PSRAM.

Differences from the Python, all to fit memory: the beat-period
autocorrelation runs on the envelope decimated to ~4 kHz over the newest 8 s,
evaluated only over the plausible lag range (the desktop's full-length FFT
would need ~16 MB); robust noise-floor statistics use a strided subsample;
no coarse spectrum. The period is only a starting estimate -- the template
refinement and least-squares fit set the reported figures, and they agree
with the Python to within 0.001 s/day, ms and degree on the test grid.

A timed run no longer than the window is reported from one analysis of the
whole run, as on the desktop. A longer run is the mean of the full-length
window readings taken during it, with the confidence interval scaled by the
number of non-overlapping windows the run spans.

The analysis never reaches back past a break in the audio: stopping and
restarting, switching source or a USB mic changing rate starts a fresh stream,
so a reading is never stitched from two captures.

## Development

```
# DSP core on the host, checked against the Python analyzer; USB parser tests
cmake -S esp32/host -B build/host && cmake --build build/host
python esp32/host/compare.py build/host/wgcli
python esp32/host/stream_check.py build/host/wgcli
build/host/uac_test

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
| `lib/wguac/` | USB Audio Class descriptor parsing (host-tested by `host/uac_test.cpp`) |
| `installer/` | The browser installer page (assembled by `.github/scripts/make_site.py`) |
| `src/` | Firmware: audio capture, engine (runs / power reserve), web API, settings |
| `web/` | The single-page app served by the device |
| `host/` | Host build, Python comparison, dev server |
| `tools/` | Asset embedding, caliber export, desktop merge |
