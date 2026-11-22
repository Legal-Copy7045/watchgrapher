// WatchGrapher DSP core -- a portable C++ port of watchgrapher/dsp.py and the
// measurement half of watchgrapher/analysis.py.
//
// No Arduino or ESP-IDF dependencies: the same sources build into the ESP32-S3
// firmware and into the host test tool (esp32/host), which is how the port is
// checked against the Python analyzer.
//
// Numerics: sample buffers are float (the S3 has a single-precision FPU;
// double is emulated in software and slow). Anything that carries absolute
// time or feeds the rate fit -- beat times, the regression, statistics -- is
// double, because a 0.01 s/day rate is a 1e-7 relative change in period.
//
// Differences from the Python, all deliberate and all to fit in PSRAM:
//   * Beat-period autocorrelation runs on the envelope decimated to ~4 kHz
//     and over the most recent 8 s, computed directly over the plausible lag
//     range instead of by a full-length FFT (which would need ~16 MB). The
//     period is only a starting guess -- verify_period, the template
//     refinement and the least-squares fit set the real figures.
//   * The robust noise floor (median / MAD) used for peak prominence is taken
//     over a strided subsample of the buffer instead of every sample.
//   * No coarse spectrum and no raw beat waveform (the averaged beat shape is
//     kept). The browser does the charting.
#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace wg {

extern const int STANDARD_BPH[10];

// Snap a measured beat rate to the nearest standard value; 0 if none is close.
int snap_bph(double measured_bph, double tol_frac = 0.02);

// Amplitude in degrees from the 1st-to-3rd noise interval (exact harmonic
// form by default; `exact=false` gives the small-angle textbook formula).
double amplitude_from_dt(double dt, double bph, double lift_angle, bool exact = true);
double dt_from_amplitude(double amp, double bph, double lift_angle);
double solve_lift_angle(double dt, double bph, double known_amplitude);

struct Config {
    float band_lo = 1500.0f;
    float band_hi = 12000.0f;
    float env_win_ms = 0.35f;
    float sub_threshold = 0.16f;   // relative height for sub-noise peaks
    float lift_angle = 52.0f;
    int forced_bph = 0;            // 0 = auto-detect
    bool exact_amplitude = true;
    bool no_parity_fix = false;    // disable the tick/tock anchor correction
};

struct Measurement {
    bool ok = false;
    std::string message;
    int detected_bph = 0;          // what the audio measured (snapped)
    int nominal_bph = 0;           // what rate was computed against
    double raw_bph = 0.0 / 0.0;
    double rate = 0.0 / 0.0;       // s/day, + = gaining
    double rate_ci = 0.0 / 0.0;    // 95% half-width, s/day
    double beat_error = 0.0 / 0.0; // ms
    double amplitude = 0.0 / 0.0;  // degrees
    double amplitude_spread = 0.0 / 0.0;  // IQR of per-beat amplitude
    int beats = 0;
    double duration = 0.0;         // seconds of audio analysed
    double snr_db = 0.0;
    double quality = 0.0;          // 0..1 template correlation
    double lift_angle = 52.0;
    double dt_mean = 0.0 / 0.0;    // s, 1st-to-3rd noise
    double parity_correction = 0.0;    // ms removed
    double parity_offset_seen = 0.0;   // ms seen, applied or not
    double extra_peaks = 0.0;      // noises per beat beyond three
    double valid_frac = 0.0;       // beats yielding a usable interval

    // Diagnostics / plotting
    std::vector<double> times;     // beat times, s from buffer start (parity corrected)
    std::vector<double> index;     // beat number of each time
    std::vector<double> resid;     // s, residual about the fitted line
    std::vector<float> mean_shape; // averaged, normalised envelope of one beat
    int shape_fs = 0;
    int shape_pre = 0;             // samples of mean_shape before the anchor
    float p1_idx = 0.0f, p3_idx = 0.0f;  // unlocking / drop positions in mean_shape
    std::vector<float> amp_samples;      // per-beat amplitude, degrees
    std::vector<float> inst_rate_t;      // s within capture
    std::vector<float> inst_rate;        // s/day, ~1 s smoothed

    // Milliseconds spent in each stage, for tuning on the device.
    float t_filter_ms = 0, t_period_ms = 0, t_refine_ms = 0, t_impulse_ms = 0, t_total_ms = 0;
};

using AllocFn = void* (*)(size_t);
using FreeFn = void (*)(void*);
using ClockFn = double (*)();   // monotonic milliseconds, optional

// Holds the large scratch buffers (two floats per sample) so they are
// allocated once -- on the ESP32 in PSRAM -- instead of on every analysis.
class Analyzer {
public:
    // `fast` / `fast_free` optionally allocate small, hot scratch buffers
    // (tens of kB) from faster memory -- internal RAM on the ESP32.
    explicit Analyzer(size_t max_samples, AllocFn alloc = nullptr, FreeFn release = nullptr,
                      ClockFn clock = nullptr, AllocFn fast = nullptr, FreeFn fast_free = nullptr);
    ~Analyzer();
    Analyzer(const Analyzer&) = delete;
    Analyzer& operator=(const Analyzer&) = delete;

    bool ok() const { return xf_ != nullptr && env_ != nullptr; }
    size_t capacity() const { return cap_; }

    // Write up to capacity() samples (full scale = +/-1.0) here, then call
    // analyze(). The buffer is filtered in place, so its contents are
    // consumed by the call.
    float* input() { return xf_; }

    Measurement analyze(size_t n, int fs, const Config& cfg);

private:
    size_t cap_ = 0;
    float* xf_ = nullptr;    // raw in, band-passed out
    float* env_ = nullptr;   // RMS envelope
    FreeFn free_ = nullptr;
    ClockFn clock_ = nullptr;
    AllocFn fast_ = nullptr;
    FreeFn fast_free_ = nullptr;
    double now() const { return clock_ ? clock_() : 0.0; }
};

// Streaming analysis for a device that measures continuously.
//
// Analyzer re-filters its whole window on every call. Stream instead filters
// and envelopes each NEW block of audio once, as it arrives, and keeps the
// band-passed signal and its envelope in a sliding buffer, so an analysis
// only runs the measurement stages over data that is already prepared.
//
// The filter is still zero-phase: each block is run through sosfiltfilt with
// a margin of real audio on both sides (several of the band-pass filter's
// time constants), and only its middle is kept -- the same values a
// whole-window sosfiltfilt gives, apart from its edge transients. Processed
// audio therefore trails the newest sample by that margin (tens of ms).
struct Biquad { float b0, b1, b2, a1, a2; };

using ReadFn = std::function<size_t(float* dst, size_t n, uint64_t end)>;

class Stream {
public:
    // `window_cap`: the longest window analyze() will be asked for, samples.
    Stream(size_t window_cap, AllocFn alloc = nullptr, FreeFn release = nullptr,
           ClockFn clock = nullptr, AllocFn fast = nullptr, FreeFn fast_free = nullptr);
    ~Stream();
    Stream(const Stream&) = delete;
    Stream& operator=(const Stream&) = delete;
    bool ok() const { return xf_ && env_ && scratch_ && bmax_ && dec_; }

    // Process raw audio up to absolute frame `avail_end`. `epoch` is the first
    // frame of the current unbroken stream; a change of epoch, rate or filter
    // settings starts over. `read(dst, n, end)` copies the n raw samples
    // ending at absolute frame `end` and returns how many it could.
    void update(uint64_t avail_end, uint64_t epoch, int fs, const Config& cfg, const ReadFn& read);

    uint64_t end() const { return base_ + fill_; }      // processed audio is final up to here
    uint64_t start() const;                              // oldest processed frame held
    int fs() const { return fs_; }

    // Analyse the n samples ending at absolute frame `end_frame` (<= end()).
    Measurement analyze(size_t n, uint64_t end_frame, const Config& cfg);

private:
    void reset(uint64_t from, uint64_t epoch, int fs, const Config& cfg);
    size_t cap_ = 0, slack_ = 0, total_ = 0;   // window, extra room, buffer length
    float* xf_ = nullptr;                      // band-passed signal
    float* env_ = nullptr;                     // its RMS envelope
    float* scratch_ = nullptr;                 // one block plus margins
    // Kept up to date as audio arrives, so an analysis need not re-read the
    // whole envelope: the maximum of every BLOCK samples, and the envelope
    // averaged down to ~2 kHz for the period search.
    static constexpr size_t BLOCK = 256;
    float* bmax_ = nullptr;
    float* dec_ = nullptr;
    size_t dec_d_ = 1;
    size_t scratch_len_ = 0;
    uint64_t base_ = 0;                        // absolute frame of xf_[0]
    size_t fill_ = 0;                          // valid samples in the buffers
    uint64_t epoch_ = ~0ull;
    int fs_ = 0;
    float lo_ = 0, hi_ = 0, win_ = 0;          // filter settings in force
    Biquad sos_[4];
    int nsec_ = 0;
    size_t margin_ = 0, half_ = 0;             // filter margin, envelope half-width
    std::vector<float> kernel_;
    AllocFn alloc_ = nullptr;
    FreeFn free_ = nullptr;
    ClockFn clock_ = nullptr;
    AllocFn fast_ = nullptr;
    FreeFn fast_free_ = nullptr;
};

// Dot product used by every correlation; the firmware installs a SIMD one.
using DotFn = float (*)(const float* a, const float* b, int n);
void set_dot(DotFn f);

// ---- lower-level pieces, exposed for the host tests ------------------------


// 4th-order Butterworth band-pass as four second-order sections (8 poles),
// the same design as scipy.signal.butter(4, [lo, hi], 'band', output='sos').
// Returns the number of sections written (4).
int design_bandpass(int fs, float lo, float hi, Biquad out[4]);

// Zero-phase filtering in place with odd-extension padding and steady-state
// initial conditions, matching scipy.signal.sosfiltfilt.
void sosfiltfilt(const Biquad* sos, int nsec, float* x, size_t n);

// RMS envelope: sqrt(convolve(x*x, hann(n)/sum, 'same')).
void envelope(const float* x, float* env, size_t n, int fs, float win_ms);

// scipy.signal.find_peaks with the subset of options the analyzer uses.
struct PeakOpts {
    bool use_height = false;
    float height = 0.0f;
    int distance = 0;          // 0 = off
    bool use_prominence = false;
    float prominence = 0.0f;
};
void find_peaks(const float* x, size_t n, const PeakOpts& opt, std::vector<int>& peaks,
                std::vector<float>* heights = nullptr);

}  // namespace wg
