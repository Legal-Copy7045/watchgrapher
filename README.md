# WatchGrapher

An acoustic timegrapher for Windows. Listens to a mechanical watch through a
USB microphone and reports rate, amplitude, beat error and beat rate, then
tells you what to adjust for the caliber you're working on.

---

## Features

<!-- Keep this list current as features are added or changed. -->

### Measurement
- Acoustic timegraphy from a microphone or piezo contact pickup: rate (s/day),
  amplitude (degrees), beat error (ms), beat rate (bph)
- Exact-harmonic amplitude, not the small-angle approximation, so the reading
  stays honest near the knocking region
- Tick/tock anchor correction: removes phantom beat error caused by the two
  half-swings sounding different
- Live timegrapher trace, averaged-beat waveform, raw-mic view
- Instantaneous-rate waterfall with ghost traces
- Rate history against elapsed time with age-tiered decimation (keeps scrolling
  on multi-day runs)
- 95% confidence interval on rate; per-beat amplitude scatter
- Coarse spectrum / diagnostics view
- Timed runs with a settling period, or open-ended
- Simulated-watch mode: full end-to-end operation with no hardware
- Analyze a recorded WAV offline; record WAV during a session
- Auto-gain + clipping guard (toggleable); one-press self-tune pickup

### Analysis
- Rate-stability (Allan deviation) for a single run
- Long-term stability across a watch's whole history
- Escapement efficiency: impulse fraction lift / (2 x amplitude)
- Power-reserve logging: amplitude/rate decay, isochronism (linear or quadratic,
  toggleable), post-wind "kick" analysis
- Two headline reserve figures, live and in the history: **power reserve** (full
  wind to run-down, ~135 deg) and **good time to** (amplitude 200 deg) -- taken
  from where the run actually crossed each level, or projected from the decay
  and marked estimated, sharpening as the run goes
- Gear-train / periodic-fault scan with wheel identification
- Fault-signature library (magnetism, poise error, hairspring flat, rebanking, ...)
- Rebanking / knocking detection

### Positions & regulation
- Guided six-position workflow with auto-capture
- Positional view: table + bars, or a polar compass plot
- Caliber-specific advice engine, ordered amplitude -> beat error -> rate ->
  delta / isochronism, keyed to the regulating hardware
- Guided regulation wizard
- Regulation session log with learned per-watch index sensitivity
- Grading against COSC-style / METAS-style / manufacture / vintage standards

### Caliber & watch database
- Curated caliber database: beat rate, lift angle (with provenance), regulator
  type, expected amplitude, teeth, jewels, power reserve, service interval,
  known weak points
- Bulk WatchGuy lift-angle list merged in (~2,000 movements)
- "Movement info": plain-language "what's normal for this caliber"
- Caliber cross-reference (clone / equivalent families)
- Watch catalogue: model -> movement across generations, per-reference detail,
  Rolex reference decoder
- Caliber service templates: phases, lubrication map, specs, weak points
- Schematic illustrations for every catalogued reference (generated line
  drawings, not copyrighted photos)

### My Watches (collection)
- Full profile: identity, movement, case, provenance, purchase, service
  interval, target rate, photo, tags, notes
- Test history with trend charts and per-year slope against a noise floor
- Service history including water-resistance test results
- Power-reserve run history
- Wrist-rate log: real on-the-wrist performance vs bench figures
- Document vault: warranty cards, receipts, box & papers, manuals, valuations
- Tags and smart collections (Needs service, Divers, Chronographs, ...)
- Reminders: service overdue, warranty ending, watch gone quiet
- CSV import with a worked template file
- Undo for collection changes (Ctrl+Z, 25 deep)
- Back up / restore the whole collection as a zip

### Reports & output
- Timing report with inline trace, positional results, grade, reserve run,
  assessment, fault scan
- Printable timing certificate (COSC / METAS-style single page)
- Before/after service report
- Watch report: profile + full history + trends + long-term stability +
  regulation log + services + documents
- Portfolio report and Year-in-review
- Side-by-side comparison of 2-4 watches
- Direct PDF output for any report (Qt's own renderer, no extra dependency);
  optional PDF sidecar for every HTML report
- Report library: browse, open, convert to PDF, delete
- CSV export of positional data, reserve runs and history

### Phone as pickup / remote
- Phone browser streams its mic over Wi-Fi (PCM over WebSocket, or WebRTC),
  no app to install
- HTTPS with a runtime self-signed certificate
- Fixed port, persistent server, auto-reconnect, screen-wake-lock while running
- Server-side makeup AGC + a boost slider on the page
- Remote control: from the phone, choose watch / position / wind / duration,
  start or stop a run (asking which mic each time), start a power-reserve log,
  and get the save/discard prompt -- all on the phone
- Live monitor: any desktop run shows on the phone automatically -- the watch,
  the four numbers, and for a power-reserve run the power-reserve and
  good-time-to figures, elapsed/target hours, amplitude and rate decay,
  deg/hour, isochronism spread, next-sample countdown and a decay sparkline
- Watch management from the phone: add / edit (with catalogue reference lookup)
  / archive; bench-level fields stay on the desktop
- Dedicated Phone Portal tab with QR code; optional autostart
- Bound to the LAN address only; watch list and commands token-gated

### Chronograph
- Dedicated Chrono tab: measure chronograph stopped vs running, quantify the
  amplitude / rate load, A/B compare

### Tools & calibration
- Sound-card sample-clock calibration against NTP, with optional background
  auto-calibration when you start a run on an un-calibrated device
- Microphone-response calibration (swept-sine)
- Cross-check against a hardware timegrapher (logged)
- Lift-angle solver (180-degree method)
- Demagnetiser A/B
- Watch-synchronisation clock: NTP-corrected reference time with per-second
  flash / beep, plus mark-and-return rate measurement

### Guidance & UX
- Animated escapement diagram running at your measured amplitude and beat error
- First-run setup wizard, re-runnable any time
- Glossary and a bench-order guide in Help
- Guided interpretation of readings
- Session presets (Quick check, Timed 30 s, Full 6-position, Power reserve,
  Vintage / low-beat)
- Light / dark / system theme
- `WATCHGRAPHER_HOME` to relocate all data
- One-button `run.bat` launcher: installs Python and dependencies on first run

---

## Setup

Unzip somewhere your account owns -- `C:\Tools\Timegrapher` is fine,
`C:\Program Files` is not, because the app writes recordings and exports next
to itself. Then double-click **`run.bat`**.

That is the whole installation. If Python 3.10+ is not already present,
`run.bat` installs it (via winget, falling back to downloading the official
installer from python.org), then builds a virtual environment and installs
dependencies. First run takes a few minutes; later runs start in seconds.

If Windows shows a *"publisher could not be verified"* warning on `run.bat`,
that is the Mark of the Web on files extracted from a downloaded zip -- right
click the folder, or run
`Get-ChildItem -Recurse . | Unblock-File` in PowerShell, once. Files from
`git clone` are not flagged.

Manual install if you prefer:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m watchgrapher
```

---

## The pickup matters more than the software

This is the part that decides whether you get a clean trace or noise. A watch
escapement puts out a very quiet, very short click. A microphone sitting in
open air a few inches away will not reliably resolve the three sub-noises
inside each beat, which is what amplitude depends on.

In rough order of how well they work:

- **A piezo disc against the case back.** A 20-35 mm piezo element wired into
  a cheap USB audio interface's mic input is the closest thing to what a real
  timegrapher uses. Total cost is a few dollars. Press the case back flat
  against the disc; contact pressure matters a lot.
- **A contact / throat microphone.** Same idea, already packaged.
- **A stethoscope head taped over a small electret capsule.** Works well.
- **A good USB condenser mic in a quiet room, watch resting on the grille.**
  Workable for rate and beat error. Amplitude will be marginal.
- **A phone held against the case.** No cable, no interface -- the phone's own
  mic streams to the app over Wi-Fi (see *Using a phone as the pickup* below).
  About as good as the condenser mic: fine for rate and beat error, marginal
  for amplitude, and better if you press the phone's mic port to the case back.

Whatever you use, kill the room noise: no fans, no HVAC, no talking. If your
interface has gain, set it so the level meter sits in the upper half without
touching red. Clipping destroys the sub-noise structure that amplitude reads.

**View -> Auto-gain and clipping guard** (on by default) helps here. Auto-gain
applies a digital makeup gain into the analysis buffer so the DSP keeps
headroom on a quiet pickup -- it does not touch the Windows mixer, and the
recorded WAV stays raw. The clipping guard puts a red warning under the trace
the moment the input hits full scale, because a clipped capture reads amplitude
wrong. Turn it off to use the signal exactly as it arrives.

Sample rate: 48 kHz is the sensible default. At 4 Hz, one sample at 48 kHz is
worth roughly 0.7 degrees of amplitude resolution. 96 kHz halves that if your
interface supports it; it will not fix a bad pickup.

---

## Using a phone as the pickup

There is no app to install on the phone. WatchGrapher runs a small web server
on your local network and the phone's browser opens it and streams its
microphone back -- and the same page doubles as a remote control: pick a watch
from your collection, start the test, watch the four numbers, and save the run,
all without walking back to the computer.

Phone browsers only allow microphone access on a **secure** page (HTTPS), so
the app serves HTTPS using a throwaway self-signed certificate. That means the
phone shows a one-time "connection is not private" warning that you tap
through. The `cryptography` and `aiortc` packages in `requirements.txt` cover
this -- `cryptography` for HTTPS, `aiortc` for the WebRTC transport. Without
`cryptography` the app falls back to plain HTTP and the phone refuses the mic;
without `aiortc` the WebRTC option on the page is greyed out.

The server runs on a **fixed port** (8477 by default, saved in `settings.json`),
so the URL stays the same every time -- bookmark it on the phone. It only moves
to the next free port if something else is already using that one.

**The Phone Portal tab** (top nav, or Ctrl+5) is where you run and connect the
server. It shows a big **Start phone server** button, the URL, a **QR code**,
and a **"Start automatically when WatchGrapher opens"** checkbox. A server
started here stays up until you stop it, and survives switching input devices.

You can also just choose **"Phone / browser pickup (over Wi-Fi)"** as the input
device and press **Start** on the computer -- that jumps to the Phone Portal so
you can scan the code, and that server stops when you pick a different input.

Either way, the computer and the phone must be on the **same Wi-Fi network** (a
guest network that isolates clients will not work). The URL is like
`https://192.168.1.42:8477` and stays the same every time -- bookmark it.

Then, on the phone:

1. **Scan the QR code** on the Phone Portal tab, or type the URL into any
   browser (Safari, Chrome, whatever) and bookmark it. Mind the `https`.
2. The phone warns the certificate is not trusted. This is expected for a
   local self-signed cert -- tap **Advanced** / **Show details** and
   **proceed** / **visit this website**.
The page has two tabs: **Measure** and **Watches**. It follows the desktop's
state automatically -- if a run is already going it opens as a monitor, if the
desktop is idle it shows the new-run form.

**Starting a run from the phone.**

1. On **Measure**, pick the **watch**, **position**, **wind state** and
   **duration** (open-ended, or timed 20 s to 5 min). Tick **Also log power
   reserve** to run a reserve log alongside, with its own sample interval and
   target hours.
2. Tap **Start run**. It asks which microphone:
   - **The desktop's own pickup** -- the phone stays a pure remote; the desktop
     records on whatever input it is set to (USB mic, contact pickup).
   - **This phone's microphone** -- the phone streams its mic; hold the mic port
     against the case back. **Audio settings** (PCM/WebRTC, Boost, auto-level)
     apply to this mode only.
3. The tiles fill in. A timed run shows a `1:12 / 2:00` line and ends itself;
   for an open-ended run tap **Stop run**. Because the phone started it, the
   *Run finished* panel with **Save to watch** / **Discard** appears on the
   phone.
4. Mid-run you can also tap **Start power reserve log** from the monitor.

**Monitoring and taking over.** Whenever the desktop has a run going -- started
anywhere, on any pickup -- the Measure tab shows it: the watch, position and
wind, the four tiles, the progress line, and for a **power reserve run** a card
with elapsed and target hours, amplitude and rate start-to-now, degrees per
hour, the isochronism spread so far, the next-sample countdown, an
amplitude-decay sparkline and the projected full-reserve time. So you can check
a 48-hour run from another room. Nothing to switch on.

**Stop run** works on a desktop-started run too (with a confirm). Whoever stops
it from the phone gets the *Run finished* panel on the phone -- the desktop does
not pop a blocking dialog, so a run that finishes on its own while you are away
never freezes the monitor. After it stops the desktop is idle and the New run
form is right there to start the next one.

**Managing watches.** The **Watches** tab lists your collection. Tap one to edit
brand, model, reference (with a **look up** that resolves the movement from the
catalogue), nickname, tags, target rate, serial and notes; **+ Add watch**
creates one. Deleting is desktop-only -- the phone offers **Archive** instead,
which hides a watch without losing its history (**show archived** brings them
back). The bench-level fields (case detail, provenance, service interval) stay
on the desktop.

The page holds the screen awake while a run is on -- a screen Wake Lock where
supported, plus a hidden looping video for iOS Safari. It reconnects on its own
if the Wi-Fi blips or WatchGrapher restarts.

**Security.** The server binds only to this computer's LAN address (never all
interfaces, so it is not reachable over a VPN or a public-Wi-Fi link). Your
watch list, the live readings and every remote command (start/stop a run, log
power reserve, add/edit/archive a watch, save a result) require a random
per-session token that is embedded in the page the server hands out -- a device
that merely hits the port gets nothing, though anyone who can load the page on
your LAN can drive the session. Treat it like any other device on your home
network.

**What to expect.** A phone mic through this path is roughly as good as a decent
USB condenser mic: solid for rate and beat error, marginal for amplitude unless
coupling is good. Run **Self-tune** once the stream is live -- the band a phone
mic wants is different from a piezo. Sample-clock calibration and per-pickup
profiles are disabled for the phone input (the phone's clock is not something
this end can calibrate). Changing the sample rate restarts the server (and the
URL may move) so set it before you start.

**Privacy and safety.** The server only ever *receives* audio. It serves one
static page, runs no code from the phone, and is reachable only on the local
network -- nothing is sent to the internet. It shuts down when you change the
input device or close the app.

**If it will not connect:**

- **Page will not load on the phone.** Wrong network, or client isolation on
  the Wi-Fi. Try a normal (non-guest) network, or a phone hotspot with the
  laptop joined to it.
- **Windows Firewall prompt on first Start.** Allow WatchGrapher on private
  networks. If you dismissed it, the phone will not reach the page until you
  allow it in Windows Defender Firewall settings.
- **"mic denied" or "getUserMedia is undefined" on the phone.** The page was
  loaded over `http`, not `https` -- reload it with `https://`. If the app only
  offered an `http` URL, `cryptography` is not installed: `pip install
  cryptography` (or re-run `run.bat`) and restart.
- **Certificate warning has no "proceed" option.** Some corporate-managed
  phones block self-signed certs entirely. Use a personal device, or a wired
  pickup.
- **PCM keeps stalling / "reconnecting..." on the phone.** Switch the page to
  WebRTC, move closer to the router, or fall back to a wired pickup.
- **Signal too weak / amplitude will not read.** Turn Boost up. If it is
  already high and still weak, the phone's mic port is not making good contact
  with the case -- press harder or reposition. A phone case can get in the way.
- **Level bar moves on the phone but the app shows nothing.** The app was not
  actually started, or you switched input devices after starting -- press Stop
  and Start again.

---

## Self-tuning the pickup

Press **Self-tune**. From idle it starts listening on its own, waits for a
few seconds of audio, sweeps the filter band, the envelope window and the
sub-noise threshold, applies the best result and stops the run it started --
one press, start to finish, no need to press Start yourself first.

Each combination is scored on signal-quality evidence only: how well beats
match their own averaged template, what fraction yield a usable impulse
interval, how many noises per beat are being resolved, and how much the
per-beat amplitude estimate scatters. It deliberately does not reward a
particular amplitude or rate -- tuning toward a number you were hoping to see
is how you talk yourself into a wrong reading. It also scores your current
settings first and keeps them as a candidate, so a sweep can never leave you
worse off than you started.

Takes about ten seconds; it gives up after fifteen. Click the button again to
cancel. If a filter setting makes the analysis fail -- some bands leave too
little signal for the peak finder, which is common on a noisy pickup -- that
trial scores zero and the sweep carries on rather than aborting. If every
trial fails it says so and changes nothing.

**Room noise** works the same way: lift the watch off the pickup, press it,
and it starts listening if nothing is running, measures the noise floor for a
couple of seconds, tells you whether the room is quiet enough to trust an
amplitude reading, and stops. Below about -48 dBFS is fine; above -38 the
unlocking noise will be buried.

The report afterwards tells you what it chose and,
crucially, the **noises per beat**. A lever escapement makes exactly three:
unlocking, impulse, drop. If tuning still leaves you well above three, no
filter setting will save it -- the pickup is hearing the room, not the
escapement. Press the case back harder against the sensor, kill background
noise, and back the gain off if the level meter is anywhere near red.

That single number is the best health check on a reading. Amplitude is the
measurement it destroys first.

---

## Testing without a microphone

Two ways, both exercising the real analysis chain.

**Simulated watch (best).** Pick the simulated watch from the
Device dropdown and press Start. The **Simulated watch** panel on the
left lets you dial in beat rate, amplitude, rate and beat error, and the
readouts should come back with exactly those numbers. It generates audio into
the same ring buffer a real microphone feeds, so the live trace, level meter,
position capture and advice all behave identically. Change values while it's
running and watch the readouts follow.

Useful things to try:

- Set amplitude to **338** and watch the app flag knocking.
- Set beat error to **1.6 ms** and see the two trace lines pull apart.
- Set rate to **+40** and watch the trace slope.
- Drop **Noise** to 4 dB to see what a bad pickup looks like.
- Select an ETA 2824-2 but set the simulator to 21600 bph -- the beat rate
  readout turns red and the app tells you the rate figure is meaningless.

**Device selection.** The app picks a real input on startup and lists the
simulator last, so it never opens pretending to measure a watch that is not
there. Windows exposes the same physical microphone once per host API, and
PortAudio's "default" is usually the MME copy -- which resamples to 44100
behind your back and costs about a third of your amplitude resolution. The app
therefore honours your chosen *device* but takes the best route to it,
preferring WASAPI. Override from the Device dropdown if you want a different
one; **View -> Rescan audio devices** (Ctrl+R) re-enumerates after you plug
something in. If a stream stalls mid-run -- USB power management, a WASAPI
timeout, a burst of overflows -- the app notices the capture buffer has
stopped advancing and rebuilds the stream in place, so a long run survives a
glitch instead of quietly freezing.

**Test WAV files.** Generate a set with known values baked into the filenames:

```
.venv\Scripts\python tools\make_test_wav.py test_wavs
```

Ten files covering healthy, needs-regulating, bad beat error, low amplitude,
knocking, Seiko, vintage, co-axial, hi-beat and a deliberately noisy pickup.
Load them with **Analyze a WAV file** and check the readouts against the
filename. Set the lift angle to match the filename first, or amplitude will be
scaled wrong.

**Scrubbing a recording.** A WAV longer than the rolling window opens in a
scrubber instead of just analysing the first few seconds: an overview of the
whole file, a draggable/resizable window, prev/next, and *Analyse window* to
run the full chain on any slice. Lets you find the clean stretch of a noisy or
intermittent recording. *Send window to main view* pushes that reading to the
readouts and trace.

---

## Using it

1. **Wind the watch fully**, then let it settle 10-15 minutes. Amplitude right
   after winding reads high and falling.
2. Pick the caliber. Search accepts loose text: `2824`, `nh35`, `3135`,
   `co-axial`. Selecting a caliber fills in the lift angle and beat rate.
3. **Measure.** One **Start** button at the bottom of the control column, with
   a duration beside it.

   - **A duration** (default 20 s) runs a timed test. When it ends, the run
     *ends*: the stream stops, the whole capture is analysed in one pass, and
     you are asked what to do with the result. That single pass beats any live
     reading -- rate precision scales with capture length, so 60 s resolves
     about 0.02 s/day where a 20 s window manages roughly 0.2.
   - **Duration 0** ("open-ended") runs until you press Stop, which is what you
     want while turning a regulator and watching a number move. Stopping asks
     the same question.
   - **Settle before timing** (tick box beside the duration) holds the timed
     run off until five consecutive readings agree on rate and amplitude, so
     the run records the watch rather than the transient from setting it down.
     Falls back to starting anyway after 90 s.

   The RATE readout carries a 95% confidence figure (`+2.3 +/-0.4 s/day`).
   It shrinks as the capture lengthens -- when it is wider than a couple of
   s/day, a 20 s window is not enough and you want a timed run.

   Do not confuse the duration with the **rolling window** in Pickup tuning:
   that is how far back each *live* reading looks while a measurement is in
   progress, not how long the measurement lasts.

     The dialog offers four things: save to one of your watches (the one you
     are testing is pre-selected), create a new watch and save to it, print a
     report for the run, or discard it. Pressing **Stop** on a continuous
     session offers the same four choices -- stopping by hand is just as much
     the end of a measurement as a timer running out, and the numbers should
     not be lost because you pressed the other button. It stays quiet when
     there is nothing to file, and when the stream is only being restarted
     internally. A separate tick box records the result
     as the current position in the running six-position session, so filing the
     run and continuing round the positions are independent choices.

     Start timed run starts listening by itself, so a six-position sequence is
     one button per position rather than two.

   The rolling window is not a test duration -- that confusion is why the run
   length control exists. For multi-hour mainspring work use the Power reserve
   tab, which is a third thing again, measured in hours.
4. Check the **beat rate** readout matches the caliber before you believe
   anything else. If it doesn't, the pickup is mistracking or the caliber is
   wrong.
5. Capture each of the six positions with **Capture position**. Move the
   watch and set the position dropdown yourself between captures; it defaults
   to Dial up and stays where you leave it. The Positions tab plots the
   captured rates as a bar chart against the mean, so a poise problem is
   obvious at a glance.
6. Hit **Analyze and advise**. The Advice tab also carries a live
   **regulation assistant** -- current rate versus target, the direction and
   amount to move, and the regulator-specific instruction for the caliber --
   and grades the run against a standard (COSC-style, METAS-style,
   manufacture-typical or serviceable/vintage). **Timing certificate...**
   prints that grading as a single-page certificate -- identity block,
   verdict, the criteria table and the six-position measurements, with a
   signature line -- clearly marked as an indicative acoustic assessment, not
   a certified laboratory test.
7. For a hands-on adjustment, **Guided regulation...** walks it step by step:
   capture a baseline, fix beat error first with the instruction for that
   caliber's hardware, then close on the rate one measured move at a time --
   it learns how far your first regulator move shifted the rate and sizes the
   next one from that, rather than making you guess. Each step ends by asking
   for a fresh reading, so the loop is measured, not estimated.

Hover any of the four readouts, the trace or the beat panel for a plain-language
explanation of what the number means, how it is measured and the thresholds it
is judged against.

### Reading the trace

Two dot lines, one for the tick and one for the tock.

- **Slope** is the rate. Sloping down-right means gaining.
- **Vertical gap between the two lines** is the beat error.
- **Thick or fuzzy lines** mean the escapement isn't repeating cleanly, or the
  pickup is noisy.
- **Wandering, non-straight lines** point at a real fault -- a bent pivot, a
  hairspring catching, dirt in the train.

### Cross-checking against a hardware timegrapher

**Tools -> Cross-check against a hardware timegrapher...** takes what a Witschi
or Weishi machine reads for the same watch and compares it to the acoustic
reading. Rate off by more than 2 s/day that repeats across watches is a
systematic bias, almost always the sound-card sample clock -- it converts the
gap to ppm and points you at the Sync tab's calibration. Amplitude off by more
than ~12 degrees is a lift-angle disagreement. Each comparison can be logged to
`crosschecks.json` to build a picture over time.

### Comparing two runs

**View -> Pin current reading as trace reference** (Ctrl+P) freezes the current
tick/tock trace as a faint set of dots. The live trace then draws over it, and
the rate / amplitude / beat-error readouts gain a `vs ref` delta. Use it for
before and after a regulation, a demagnetise or a service -- pin the first
reading, do the work, and the second run overlays it directly. Ctrl+P again
with no live reading clears the reference.

### Reading the beat panel

Three views, switched from the selector on the panel:

- **Average** -- every beat in the rolling window stacked and averaged, with
  the two markers the amplitude calculation uses: the **unlock** noise and the
  **drop** noise. This is the one that drives amplitude. If those markers
  aren't sitting on obvious peaks, your amplitude number is wrong and you
  should fix that before believing it.
- **Live beat** -- the band-passed waveform of the most recent single beat,
  refreshed each cycle. Shows attack transients, ringing, a fourth noise or
  clipping the average smooths away.
- **Mic** -- the raw signal the pickup is delivering right now, last 0.6 s. A
  real-time scope: tick spikes marching across the noise floor. Use it to
  judge coupling before you trust anything.

The **sub-noise threshold** control is the knob:

- Amplitude reads implausibly **high** or jumps to nonsense: threshold is too
  high, the quiet unlocking noise is being missed. Lower it.
- Amplitude reads **low** and scatters: threshold is too low, room noise is
  being counted as the unlocking noise. Raise it.

### Reading the rate history

The plot under the beat panel tracks the run over time. It carries two lines on
two scales: **rate in s/day on the blue left axis**, **amplitude in degrees on
the amber right axis**, each axis coloured to match its line. The x axis is run
time in minutes. It clears when you press Start and thins older points on a long
run so a multi-day trace keeps scrolling. Watch for amplitude sliding while rate
holds (mainspring or train drag) or rate wandering while amplitude is steady (a
hairspring or escapement fault).

---

## What good looks like

| | Target | Acceptable | Investigate |
|---|---|---|---|
| Rate | 0 to ±5 s/d | ±10 s/d | beyond ±20 |
| Amplitude, dial up, full wind | 270-310° | 250-270° | under 250, or over 330 |
| Amplitude, vertical | within 20-40° of horizontal | 50° | more than 60° drop |
| Beat error | under 0.3 ms | under 0.5 ms | over 0.8 ms |
| Positional delta | under 10 s/d | under 20 s/d | over 25 s/d |

Those are modern-Swiss numbers. Vintage and budget calibers run lower on
amplitude by design and the app adjusts its expectations per caliber. A
Sellita SW300-1 is specified as low as 200° at full wind; a Vostok 2409 in the
220s is fine. Don't chase a number the movement was never built to hit.

**Amplitude over 330° is a problem, not an achievement.** That's the region
where the impulse pin starts striking the back of the fork horn (knocking /
rebanking). The watch runs wildly fast and the escapement takes damage.

---

## Order of operations

Work in this order or you'll do the work twice:

1. **Confirm the beat rate and lift angle.** A wrong lift angle makes a
   healthy watch look sick. One degree of lift angle error is worth about
   five degrees of amplitude.
2. **Amplitude.** It's the energy budget for everything else. A movement with
   weak amplitude will not hold whatever rate you regulate it to.
3. **Beat error.** Cheap on most calibers, and it destabilises rate across
   positions when it's large.
4. **Rate.** Last, because steps 2 and 3 both move it.
5. **Positional delta and isochronism.** These are poise and hairspring
   problems. No amount of regulating fixes them.

The **Tools** tab has a regulator-sensitivity helper: make one small
adjustment, enter the rate before and after, and it tells you what fraction of
that step you still need. Index sensitivity varies enormously between calibers,
so measuring it beats guessing.

---

## Lift angle

Lift angle only scales amplitude. Rate and beat error are unaffected, so a
wrong value won't make a good watch look broken for the right reason -- it'll
make it look broken for the wrong one.

The database holds around 2,250 calibers in groups -- Swiss/European,
Japanese, Chinese clones, Chinese in-house, Russian, generic fallbacks, and the
bulk WatchGuy reference list. About 140 of those are curated entries with a
beat rate, regulator type and amplitude band; the rest are the WatchGuy
lift-angle list. The dropdown shows the curated groups; the search box reaches
everything, including the reference list, and matches notes as well as names,
so `dandong`, `2824`, `8215` and `as1686` all work.

The watch catalogue that feeds **Find by watch model** carries roughly 470
model/reference entries across 80+ brands -- the mainstream Swiss and Japanese
lines by generation, the Swatch-group value tier (all Powermatic 80 / C07), the
common Sellita/Soprod/STP/La Joux-Perret microbrand movements, and the vintage
Valjoux and Seiko diver/chronograph families.

Every entry records where its lift angle came from:

| Source | Meaning |
|---|---|
| documented | Manufacturer or technical sheet |
| measured | Published bench measurement on real samples |
| community | Corroborated enthusiast consensus, unconfirmed |
| inherited | Taken from the caliber this one clones -- never measured on this movement |
| watchguy | The WatchGuy reference list. Lift angle only; no beat rate published, so beat rate is auto-detected |

**Searching by watch rather than caliber.** The movement search box takes
either. Type a caliber number and it searches movements; type a watch --
`Rolex Submariner`, `Speedmaster`, `SKX007`, `126610LN` -- and it narrows to
the movements that watch actually uses, listing them first.

Read the years before picking. A Submariner spans five calibers from the 1570
to the 3235, and they do not share a lift angle. **Find by watch model...**
shows every generation side by side with references and dates, which is the
faster way to tell them apart.

That distinction matters most for Chinese movements, where manufacturer
documentation frequently does not exist at all. Sea-Gull, Peacock (Dandong),
Hangzhou, HKPT and Dixmont calibers are covered, but only the Peacock SL3034,
SL4801 and the HKPT PT5000 have a lift angle anyone has actually published.

Everything else in that group is *inherited* -- a 2824-2 clone is assigned 50,
a Miyota 8215 clone 49, on the reasoning that clones copy the escapement
geometry. That is usually true and occasionally not. Rate and beat error are
unaffected either way; only amplitude is at risk. If you care about the
amplitude figure on one of these, solve for the real lift angle with the
180-degree method in the Tools tab and add your result via CSV.

Search matches the notes as well as the name, so `dandong`, `2824`, `8215` or
`ty2130` all find the right entries.

To add your own, copy `calibers_template.csv`, fill it in, and either load it
from the **Load caliber CSV** button or save it as
`%USERPROFILE%\.watchgrapher_calibers.csv` to have it loaded at every start.

The largest public list is WatchGuy's, at
<https://watchguy.co.uk/cgi-bin/lift_angles>. It's straightforward to reshape
into the CSV format above.

If your caliber isn't listed anywhere, the **Tools** tab can solve for it: mark
one balance arm, let the watch run down until the mark appears to stall exactly
opposite its rest position (that's 180° of amplitude), and back-solve from the
measured impulse interval.

---

## Command line

```
python -m watchgrapher --version                      print the version
python -m watchgrapher --devices                      list input devices
python -m watchgrapher --wav run.wav --caliber eta_2824_2
python -m watchgrapher --listen 30 --caliber rolex_3135
python -m watchgrapher --selftest                     validate the DSP chain
```

The running version is also shown in the title bar and at the foot of the
Help page, so a local build can be checked against the repo.

`--selftest` generates synthetic escapement audio with known rate, beat error
and amplitude and checks the analyzer recovers them. Useful after any change,
and a quick way to confirm the install is sane.

---

## How it works

1. Band-pass 1.5-12 kHz. Escapement noise lives there; rumble and handling
   don't.
2. RMS envelope with a ~0.35 ms window -- short enough to keep the three
   sub-noises inside each beat resolvable.
3. Beat period from envelope autocorrelation, cross-checked against actual
   peak spacing. The cross-check matters: tick and tock never sound quite
   alike, so autocorrelation alone will happily report half the true bph.
4. Sub-sample beat timing by cross-correlating each beat against an averaged
   template, with parabolic interpolation. Tick and tock get **separate**
   templates -- entry and exit pallet stones sound different, and sharing one
   template injects a fixed bias straight into beat error.
5. Rate from a least-squares fit of beat time against beat index, where
   indices come from successive intervals rather than elapsed time so a
   sloppy period estimate can't accumulate into misnumbered beats.
6. Beat error from the gap between the mean residual of the even beats and
   the odd beats, which is exactly |T1 - T2| / 2.
7. Amplitude from the interval between the first and third noise, using the
   exact harmonic relation

   ```
   A = (lift / 2) / sin(pi * dt * bph / 7200)
   ```

   The formula usually quoted, `A = 3600 * lift / (pi * dt * bph)`, is the
   small-angle approximation of the same thing. They agree to about 0.4% at
   270° but the approximation drifts high as amplitude climbs -- precisely
   where you care, near the knocking threshold.

Steps 4 and 6 need one more correction that is easy to miss. The coarse
detector locks onto whichever sub-noise is loudest, and that need not be the
same one for a tick as for a tock -- if the drop is loudest on one half swing
and the impulse on the other, the anchor jumps by several milliseconds every
other beat. That is a constant offset applied to alternate beats, which is
exactly the signature of beat error, so an uncorrected analyzer will report
several ms of beat error on a watch that is perfectly in beat. The unlocking
noise is the same physical event on both half swings, so the difference
between the mean unlocking offsets of the even and odd beats isolates the
anchor error and nothing else. Removing it leaves real beat error untouched.

Similarly, a lever escapement makes exactly three noises per beat. Anything
arriving after the drop -- case resonance, the rotor, a reflection off the
bench -- is not part of that sequence, and letting it extend the measured
interval drags amplitude far below the truth. The span therefore ends on the
loudest peak in the group, since the drop is the loudest of the three and an
echo is by definition quieter than whatever produced it.

Validated against 192 synthetic combinations of beat rate, amplitude, rate,
beat error, tick/tock asymmetry and spurious extra noises: rate within
1.5 s/day, beat error within 0.25 ms, amplitude within 15°, down to 14 dB SNR.
`--selftest` runs a representative subset.

---

## My Watches

The app has four pages, switched from the buttons at the top left: **MEASURE**
is the live instrument, **MY WATCHES** is your collection, **SYNC** is the
reference clock, **HELP** is the quick reference. Ctrl+1/2/3/4 switch between
them. They are different activities with different rhythms, and the collection
gets a full page rather than a strip below the trace.

My Watches keeps a profile, a timing history, a service log and a
power-reserve history for each watch you own. The right pane is tabbed:
**Timing history** (the trend chart and the run table), **Service log**, and
**Power reserve**.

**Printable report.** *Print / save watch report* produces one self-contained
HTML page: the photo, the full profile, the movement and its regulating
hardware, ownership and service dates, the whole timing history as a table,
a trend chart, the most recent run position by position, the trend verdicts,
the standard the run was graded against, and the service history with any
scanned invoices embedded. Print to PDF from the browser. Images are embedded
as data URIs, so the file survives being emailed on its own.

**Portfolio report.** *Portfolio report (all watches)* is one page over the
whole collection: purchase-value and service-spend totals by currency, an
at-a-glance table of every watch, then a card each with identity, movement,
the latest run and its trend verdicts, and a service summary.

**Backup.** *File -> Back up collection* writes a zip of `collection.json`
plus the photos and documents folders; *Restore collection* replaces the
current collection from one, keeping a timestamped copy of the old file first.

**Attributing a run.** Pick the watch from the dropdown in Test conditions
before you measure. That applies its caliber and lift angle automatically, and
"Save current run to this watch" files the results into its history. The
My Watches button beside it jumps straight to the tab.

Both this tab and **Find by watch model** read one shared catalogue -- roughly
475 model/reference entries across 80-plus brands -- so anything you can look
up is something you can save, and vice versa. It covers Rolex, Omega, Tudor,
Seiko, Grand Seiko, Citizen, Orient, Hamilton, Tissot, Certina, Mido, Rado,
Longines, Oris, Sinn, Stowa, Laco, Damasko, Nomos, Junghans, IWC, JLC, Zenith,
Panerai, Breitling, TAG Heuer, Blancpain, AP, Cartier, the Russian and Chinese
makers, and the microbrands from Baltic and Lorier through to San Martin,
Halios, Monta, Zodiac, Serica and anOrdain.

**Profiles.** Brand, model, reference, serial, movement serial, caliber, case
material, bezel, crystal, size, water resistance, bracelet, production year,
purchase date, price and currency, condition, dealer, last service date,
service interval, target rate, photo and notes.

The reference field does the work. Pick a brand and the model list fills; pick
a model and its known references appear. Selecting one fills in the movement,
case metal, bezel, crystal and nickname -- choose a Submariner Date 126613LB
and it knows that is yellow Rolesor with a blue bezel and that everyone calls
it a Bluesy. Everything stays editable, because the database does not know
about your service replacement bezel.

Uncatalogued Rolex references still decode, because modern Rolex numbering is
systematic: the final digit is the case metal and the letter suffix is the
bezel colour abbreviated in French. So 126619LB resolves to white gold with a
blue bezel even though nobody wrote that row down.

**Illustrations.** A watch with no photo shows a generated schematic instead of
a blank box -- a clean front-facing line drawing built from the case metal,
bezel style, dial colour and movement, so a fluted-bezel Datejust reads
differently from a dive Submariner from a Royal Oak. These are diagrams, not
photographs (manufacturer press images are copyrighted and cannot ship with the
app); add your own photo any time to replace one. `python -m tools.render_catalogue`
dumps an SVG for every catalogued reference to `images/catalogue/` if you want
the files on disk.

**Caliber cross-reference.** Pick a caliber and the info line names its
equivalents -- ETA 2824-2 lists the Sellita SW200-1, Sea-Gull ST2130, STP1-11
and Hangzhou 6300, with a note on what is actually shared (escapement geometry,
train parts, mainsprings) and what is not. Covers the 2824, 2892, 7750, Unitas
6497, Seiko NH35 and 7S26, and Miyota 9015 families.

**Movement info.** The button by the caliber picker opens the full reference
for that movement: the regulating hardware, where the lift angle comes from
(documented, measured, community, inherited), the expected beat rate and
amplitude band, how much vertical drop is normal, beat-error and
positional-delta targets, the service interval, that caliber's known weak
points -- the reversing wheels on a 2824, the fragile centre-seconds spring on
a 2892, the chrono amplitude drop on a 7750 -- and its equivalent movements.

**Documents.** A per-watch vault in My Watches for warranty cards, receipts,
box-and-papers photos, manuals, valuations and provenance -- tagged by kind
with a note and date. Files are copied into the collection's `docs/` folder so
the originals can move or be deleted, and they appear in the watch report.

**Wrist rate.** A watch's bench rate and how it actually keeps time on the
wrist are different numbers -- position mix, temperature and how much you wear
it all move the real figure. The Sync tab's watch-drift tool (mark when you set
it to true time, read it days later) can log each check against a watch; the
**Wrist rate** tab in My Watches plots the real s/day over time with the bench
mean rate as a dashed reference. A few weeks of checks tells you what to
actually regulate toward.

**History and trends.** Save a run against a watch and it joins that watch's
record. Mean rate, positional delta and peak amplitude are plotted against date,
with a least-squares slope per year for each. The chart carries two scales:
**rate and positional delta in s/day on the blue left axis**, **peak amplitude
in degrees on the green right axis**, each axis coloured to its line.

Those slopes are reported against the measurement's own repeatability, and a
slope smaller than that is called stable rather than dressed up as a finding.
Three measurements on a hobby timegrapher will always produce *some* slope; the
question is whether it is bigger than the noise. Amplitude falling 15 degrees a
year is invisible in any single measurement and unmistakable across six, and
that is the whole reason to keep the history.

Runs can be marked post-service, since comparing the runs either side of a
service is the clearest read on whether it achieved anything.

**Service log.** Each entry records the date, type (full service, regulation,
repair, warranty...), who did it and where, cost and currency, warranty
period, a water-resistance test (pass/fail, the rating held, the method and
test pressure), notes, and any number of attached documents -- a scanned
invoice or service report, PDF or image. The water-resistance result shows in
the log line and the watch report.

**Before / after report.** *Before / after report...* on the Service log tab
takes a service entry, finds the timing run saved just before it and the
post-service run just after (mark that run post-service when you save it), and
lays them side by side -- mean rate, positional delta, amplitude high and low,
worst beat error, each with the change -- and calls out what the service
actually did: amplitude recovered, delta tightened, beat error set.

**Service checklist.** *Service checklist...* opens a working checklist for the
watch's caliber -- phases in order (teardown, cleaning, inspection, lubrication
and assembly, timing and closing), the lubrication map (which oil where), the
specs worth having on the bench, and that caliber's known weak points. A
generic Swiss-lever template covers anything; ETA 2824 / 2892 / 7750, Unitas
6497 and the Seiko NH35 / 7S26 add their own detail. Tick items as you go, add
notes, and *Attach to a new service entry* files the completed checklist as a
markdown document against the watch. Logging a service updates the watch's
last-serviced date, so the service-due reminder and the trend markers follow.
Attachments are copied into the collection when you save them, so deleting the
original file elsewhere does not lose them.

Storage is `watches/collection.json` plus `photos/` and `docs/` folders --
plain text on purpose, because a collection record should outlive the software
that made it. When you pick a photo or a document the app copies it into those
folders and stores only the filename, so the record is self-contained. Reports,
exports and recordings default to the `reports/` folder.

To keep all of that somewhere else -- a synced drive, a separate data
partition -- set the `WATCHGRAPHER_HOME` environment variable to a folder of
your choice before launching; the collection, reports and `settings.json` move
there.

---

## Chrono

Two chronograph checks a bench timegrapher cannot do on its own.

**Stopwatch comparison.** Start the on-screen stopwatch and your watch's
chronograph together, run for several minutes, stop both, and enter what the
chrono reads. It works out the error as s/day and ppm. A chronograph running
off the same balance can only be as accurate as the watch is regulated -- an
error much over a few s/day means regulate the watch, not the chronograph.
Your reaction time on the two stop presses is worth a few tenths, so a long run
dilutes it; do not read a short one too closely.

**Chronograph load test.** Capture a steady reading with the chronograph
stopped, then running. A 20-40 degree amplitude drop is normal for a coupled
chronograph; much more points at the oscillating pinion or vertical clutch
dragging, and a large rate shift is a case for regulating with the chrono in
its normal running state.

## Sync

A reference clock for hand-setting a watch to true time. Pick a time source --
this computer's clock, a public NTP server (the pool, Cloudflare, Google,
Apple, or a regional pool), or a manual offset -- and press **Sync now**. It
runs a small SNTP query, corrects for the network round trip, and shows the
corrected time on an analog face and a digital readout. The panel also reports
how far the computer's own clock is off from the source.

Turn on **flash the face on every second** or **beep on every second** to land
the seconds hand precisely against a hack. **Mark** when you set the watch,
come back later, type in what it reads, and it works out the daily rate.

**Sample-clock calibration.** Rate is measured against the sound card's own
sample clock, which is typically 20-100 ppm off nominal -- 50 ppm is 4.3 s/day
of systematic error on every rate reading, and cheap interfaces are worse.
Start listening on the real device (a watch does not need to be on the
pickup), then press **Calibrate**: for the window you set, it fixes the true
time against NTP every 25 s and least-squares-fits the sample count against
it. Twenty minutes gets it to a few ppm. Switching tabs is fine. If the
stall-recovery watchdog rebuilds the audio stream partway through, calibration
keeps going on the new stream: it fits each unbroken stretch on its own and
combines them, so a restart costs a fix or two, not the run. Only pressing Stop
ends it early. The result is stored per device, dated,
and applied to the rate output only (amplitude and beat error are ratios
within one capture and do not care). One good calibration lasts a year unless
you move the setup or the room temperature swings with the season -- the Sync
label shows how old it is, and past four months you get a one-line nudge when
you start listening. You can also enter a ppm figure by hand, or
derive one from a watch whose real rate you know from a hardware timegrapher
(*From reference*: enter what this app reads and what it should read).

---

## Beyond the four numbers

**Diagnostics tab.** Split into **Live signal**, **Stability** and **Faults**.

*Live signal* -- three plots update every analysis cycle:

- **Per-beat amplitude** -- a histogram of the individual amplitude estimates
  behind the single median. Wide or bimodal points at poor coupling, a
  tick/tock mismatch, or a real escapement fault.
- **Beat error over the run** -- the beat-error figure tracked across the
  session, so a drift or a settling watch is visible.
- **Audio spectrum** -- a log-binned spectrum of the raw signal with the
  current filter band shaded, so you can see where the escapement energy sits
  and whether rotor or case-resonance energy is leaking in.

*Stability* -- the **Allan deviation** of the rate history: the rate scatter
left after averaging for a time tau. A curve that keeps falling along the
dashed tau**-0.5 line means the reading is only noise-limited and a longer
capture tightens it. A curve that flattens or turns up means the rate itself
is wandering -- the floor it settles at is roughly how much the rate will move
between captures no matter how carefully it is regulated. Needs a minute or
more of clean listening, or a power-reserve log.

Below it, **instantaneous rate** -- each beat's own period against nominal,
smoothed to about a second, plotted across the analysis window with the last
few windows left faint behind it as a waterfall. A flat line is a healthy
escapement. A slow wave while amplitude holds steady is poor isochronism or a
train fault; the fault scan on the next sub-tab identifies which part.

**Diagnostics tab -- periodic fault scan.** Rate, amplitude and beat error
describe a movement's average behaviour. They say nothing about whether that
average is produced smoothly. A bent escape wheel tooth or an eccentric pinion
barely moves the mean rate; it makes the watch run fast and slow in a cycle
whose period matches the rotation of the guilty part. On a paper tape that is
the wavy trace an experienced watchmaker reads instantly. A periodogram of the
fit residuals finds it more reliably.

The identification is the useful part. The residual spectrum is drawn with the
whole gear train overlaid as dashed lines -- balance/roller at two beats, the
escape wheel and its half cycle, the fourth wheel (the seconds hand, once a
minute), and estimated third and centre wheels -- with the ones beyond the
capture's reach greyed and labelled. Which line a peak sits on tells you which
part to inspect. A caliber can carry exact train periods in its record; without
them the fourth wheel is taken as the seconds hand and the third wheel
estimated. Note the honest limit the tab reports: three cycles are needed
before a period can be called real, so a 60-second capture cannot see a fourth
wheel at all, and a centre wheel needs three hours.

Two-beat cycles are excluded on purpose -- that IS beat error, reported as its
own number. A genuine impulse-jewel fault also lands at two beats and is not
separable from beat error by this method.

**Fault signature library.** *Match fault signatures* on the Faults sub-tab
weighs everything on hand -- the current reading, the captured positions, the
last periodic scan, a demagnetiser A/B -- against the patterns a watchmaker
carries in their head: high rate with normal amplitude that moves on
demagnetising is a magnetised hairspring; a big positional spread while dial-up
and dial-down agree is a poise error; low amplitude with a clean beat is power
delivery, with a noisy beat is the escapement. Each match shows a confidence,
why it fired, and what to look at. They are weighted guesses from the numbers,
not a diagnosis.

**Auto-capture when stable.** Ticks over in Test conditions. It captures the
position shown in the dropdown once six consecutive readings agree; move the
watch and change the dropdown yourself for the next one. It
refuses to fire on a beat-rate mismatch, a low template match, a missing
amplitude, or more than four noises per beat -- a bad pickup produces perfectly
steady numbers, and steady wrong numbers are exactly what an unguarded
auto-capture would record six times over.

**Power reserve tab.** Logs rate and amplitude at an interval you set, plotted
on twin axes against elapsed hours. Set a target in hours and it announces when
it is done; set 0 to run until you stop it.

This is a long run, not a twenty second test. With the default five minute
interval nothing visible happens between samples, so the label beside the
buttons counts down to the next one and shows time remaining -- that is there
purely so a working run does not look like a crashed one. Keep the watch on the
pickup throughout. Pressing **Stop** on the main run ends the reserve log too
and files what it has so far -- a reserve log has no meaning once the app is not
listening.

Throughout the run, and in the summary at the end, it gives two figures:

- **Power reserve** -- full wind to the watch running down, taken as amplitude
  reaching about 135 degrees.
- **Good time to** -- full wind to amplitude 200 degrees, below which rate and
  positional stability degrade. This, not the moment the watch stops, is the
  practical end of the useful reserve.

Each is read from where the run actually crossed that level. If the run was
stopped before reaching it, the figure is **projected** from the decay curve
(shown with a range and marked *estimated*), and it tightens as the run goes
on -- so a shorter run still gives a usable number.

If a watch is selected in the Measure tab, the finished run is filed to its
history by default (*Save to the selected watch*, on unless you turn it off).
It then shows up under **My Watches -> Power reserve** -- date, power reserve,
good-time-to, run length, amplitude start to end and the isochronism grade --
and double-clicking a row reloads it on the Power reserve tab. It is also in the
watch report and the portfolio report.

**Year in review** (button on My Watches) writes a one-page summary for a
calendar year: watches acquired, every service with cost and water-resistance
result, total spend, timing and power-reserve activity per watch, and
wrist-rate checks. Handy at the end of the year, or for an insurer.

Below the plot, the **Isochronism** panel turns that same data into the tests
a hobby timegrapher usually cannot do: a scatter of rate against amplitude
with a fitted line (graded good / fair / poor by how much the rate swings as
amplitude falls), the beat-error-versus-amplitude slope, and a projected
runway to 220 and 200 degrees from the end-of-run decay. The fits reject gross
outliers -- a sample where the pickup caught a knock or a truncated capture --
rather than let one bad point tilt the result; discarded points are drawn
dimmed with an x and the panel says how many were set aside.

**Non-linear (quadratic) isochronism fit** is a checkbox under the plot. Real
isochronism error is rarely a straight line -- it curves, often with a
shallow minimum. The quadratic fit reports the amplitude of least rate
sensitivity, which is the best place to sit the balance when you regulate so
the rate drifts least as the mainspring runs down.

**Post-wind kick.** The panel measures how fast amplitude falls in the first
hour of a reserve run. A drop far steeper than the run's average -- then
levelling off -- is the mainspring slipping at the barrel wall or hard braking
grease. Normal on an automatic (that slip is the whole design); a fault on a
hand-wind.

**Rebanking (knocking).** The amplitude readout warns from 330 degrees and
flags REBANKING at 355: the balance is swinging far enough that the impulse
pin strikes the fork outside the horns and slams the banking. Almost always
too strong a mainspring after a service. Sustained knocking damages the
impulse pin, so stop the watch until it is sorted.

**Per-pickup profiles.** *Save profile* stores the filter band, envelope
window and sub-noise threshold against the selected input device; picking that
device again loads them. A piezo disc and an open-air microphone want very
different bands, and this remembers each.

**Phone / browser pickup.** Choose it in the audio input dropdown to use a
phone's microphone over Wi-Fi instead of a wired pickup -- full instructions
are under *Using a phone as the pickup* near the top of this file.

**Microphone response calibration** (Tools menu) plays a 4-second sine sweep
out the default output and measures what the pickup returns, showing where it
rolls off. It captures the whole chain -- speaker, room, mic -- so it is
advisory, not an absolute calibration; its use is choosing the filter band. If
your pickup is 15 dB down by 8 kHz there is no point running the high band at
12. The curve is stored in the pickup profile and, on load, nudges you to lower
the high band if the response ends well below it.

**Escapement animation** (View menu). A schematic lever escapement -- balance,
pallet fork, escape wheel -- driven from the live amplitude, beat rate and
unlocking interval, with a 1x to 1/50 speed slider and the unlock / impulse /
drop stages lit as the beat passes through them. *Play the beat, slowed* plays
the most recent beat back at a reduced sample rate so the three noises are
audible separately.

**Theme** (View menu). Dark, light, or follow the system. The choice is saved
to `settings.json` and applied on the next start.

**Undo** (Edit menu, Ctrl+Z). Steps back through changes to the watch
collection -- an added or edited watch, a deleted service, run or reserve log,
a filed report -- up to 25 deep. It snapshots the collection file before each
save, so it does not touch the current measurement and cannot undo the very
first watch you add (there is no prior state).

**Session presets.** The *Preset* dropdown in the Test Conditions section sets
the analysis window, timed-run length, settle, auto-capture, position and
active tab in one go -- *Quick check* (8 s window, open-ended), *Timed 30 s*,
*Full 6-position* (auto-capture on, Positions tab), *Power reserve* (opens that
tab, sets a 48 h target), *Vintage / low beat* (longer window and run). Changing
any of those settings by hand drops the dropdown back to *Custom*.

**Demagnetiser A/B** (Tools tab). Capture before, demagnetise, capture after.
The signature to look for is a large rate drop with amplitude roughly
unchanged: magnetised hairspring coils cling together and behave like a shorter
spring. The app names the pattern and reminds you that any regulation done
while magnetised is now wrong.

**Service report** (Tools tab). One self-contained HTML file -- readings,
six-position table, the trace redrawn as inline SVG, assessment, the standard
grade, fault scan and measurement conditions. No external assets, so it
survives being emailed. Print to PDF from the browser if you need one; that
avoids dragging a PDF library into the dependency list for something used once
a job.

---

## When two instruments disagree

Record the same audio once and analyze it twice, rather than comparing two
live sessions taken minutes apart -- amplitude genuinely falls as the
mainspring unwinds, and rate moves with it.

Then check, in this order:

1. **Beat rate.** If the two tools report different bph, nothing else is
   comparable. This app shows the measured value, not the one you selected.
2. **Lift angle.** Amplitude scales with it and rate does not. If amplitude
   disagrees but rate and beat error match, suspect the lift angle first.
3. **The beat panel, Average view.** The two markers show exactly which noises the
   amplitude calculation used. If they are not on obvious peaks, that number
   is wrong regardless of what any other tool says.
4. **The status line.** It reports a tick/tock anchor correction and a
   noises-per-beat count. More than about 3.3 noises per beat means something
   beyond the escapement is being picked up, and amplitude is the reading at
   risk.

A dedicated phone timegrapher app and this app are both microphone
timegraphers with the same physical limits -- and with this app's phone pickup
you can even feed it the same phone mic. Where two readings differ, the one
whose beat-panel markers sit on real peaks is the one to believe.

---

## Limits

- Amplitude is the fragile measurement. Rate and beat error survive a mediocre
  pickup; amplitude does not.
- Amplitude accuracy is capped by how well you know the lift angle, not by the
  software.
- Rate accuracy is capped by the sound card's sample clock -- typically a few
  s/day of systematic error until you calibrate it against NTP on the Sync
  tab.
- **View -> Hold readouts on a weak signal** (off by default). When on, the
  rate/amplitude/beat-error readouts freeze at the last trustworthy value and
  grey out whenever the beats stop matching their template, the beat rate
  disagrees with the caliber, or more than ~4.5 noises per beat are resolved --
  rather than showing a fresh but meaningless number. The trace, waveform and
  diagnostics keep updating so you can see why.
- Chronographs read lower with the chrono running -- that's real, not an error.
- Co-axial and other non-lever escapements have a different noise signature.
  The 38° lift angle is handled, but expect to tune the sub-noise threshold.
- USB speakerphones (Jabra Speak, Poly, etc.) often only expose their mic at
  16 kHz and run heavy noise-suppression DSP that mangles the escapement
  click. The app will open them -- falling back to 16 kHz and telling you --
  but a piezo disc on a plain interface is a different class of result.
- Static bench readings aren't wrist performance. Use these numbers to set the
  watch up, then track it on the wrist for a few days.
