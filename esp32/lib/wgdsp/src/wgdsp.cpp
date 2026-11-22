// WatchGrapher DSP core. See wgdsp.h for what this is and how it differs from
// the Python it was ported from. Function order and comments follow
// watchgrapher/dsp.py and watchgrapher/analysis.py so the two can be read
// side by side.
#include "wgdsp.h"

#include <algorithm>
#include <cmath>
#include <complex>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <numeric>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

namespace wg {

const int STANDARD_BPH[10] = {12000, 14400, 16200, 18000, 19800,
                              21600, 25200, 28800, 36000, 43200};

static const double NaN = std::nan("");

int snap_bph(double measured_bph, double tol_frac) {
    int best = 0;
    double best_err = 1e9;
    for (int std_bph : STANDARD_BPH) {
        double err = std::fabs(measured_bph - std_bph) / std_bph;
        if (err < best_err) { best = std_bph; best_err = err; }
    }
    return best_err <= tol_frac ? best : 0;
}

double amplitude_from_dt(double dt, double bph, double lift_angle, bool exact) {
    if (!std::isfinite(dt) || dt <= 0) return NaN;
    if (exact) {
        double s = std::sin(M_PI * dt * bph / 7200.0);
        if (s <= 1e-9) return NaN;
        return (lift_angle / 2.0) / s;
    }
    return 3600.0 * lift_angle / (M_PI * dt * bph);
}

double dt_from_amplitude(double amp, double bph, double lift_angle) {
    double t_osc = 2.0 * 3600.0 / bph;
    double s = (lift_angle / 2.0) / std::max(amp, 1e-6);
    s = std::min(1.0, std::max(-1.0, s));
    return (t_osc / M_PI) * std::asin(s);
}

double solve_lift_angle(double dt, double bph, double known_amplitude) {
    return 2.0 * known_amplitude * std::sin(M_PI * dt * bph / 7200.0);
}

// ---------------------------------------------------------------------------
// Small statistics helpers
// ---------------------------------------------------------------------------

template <typename T>
static double median_inplace(std::vector<T>& v) {
    size_t n = v.size();
    if (n == 0) return NaN;
    size_t h = n / 2;
    std::nth_element(v.begin(), v.begin() + h, v.end());
    double hi = (double)v[h];
    if (n % 2) return hi;
    double lo = (double)*std::max_element(v.begin(), v.begin() + h);
    return 0.5 * (lo + hi);
}

template <typename T>
static double median_of(std::vector<T> v) { return median_inplace(v); }

// numpy.percentile, default linear interpolation.
static double percentile(std::vector<double> v, double q) {
    if (v.empty()) return NaN;
    std::sort(v.begin(), v.end());
    double pos = q / 100.0 * (double)(v.size() - 1);
    size_t i = (size_t)std::floor(pos);
    if (i + 1 >= v.size()) return v.back();
    double f = pos - (double)i;
    return v[i] + (v[i + 1] - v[i]) * f;
}

// Python round(): half to even.
static long pyround(double x) { return (long)std::nearbyint(x); }

// ---------------------------------------------------------------------------
// Filtering and envelope
// ---------------------------------------------------------------------------

int design_bandpass(int fs, float lo_f, float hi_f, Biquad out[4]) {
    using cd = std::complex<double>;
    const int N = 4;
    double nyq = fs / 2.0;
    double hi = std::min((double)hi_f, nyq * 0.95);
    double lo = std::max((double)lo_f, 20.0);
    if (lo >= hi) lo = hi * 0.1;

    // scipy.signal.butter: pre-warp on a normalised fs of 2.
    const double fsn = 2.0;
    double w0 = 2.0 * fsn * std::tan(M_PI * (lo / nyq) / fsn);
    double w1 = 2.0 * fsn * std::tan(M_PI * (hi / nyq) / fsn);
    double bw = w1 - w0, wo = std::sqrt(w0 * w1);

    // Analogue prototype (buttap), then lp2bp_zpk, then bilinear_zpk.
    cd pb[2 * N];
    for (int i = 0; i < N; ++i) {
        int m = -N + 1 + 2 * i;
        cd p = -std::exp(cd(0.0, M_PI * m / (2.0 * N)));
        cd ph = p * (bw / 2.0);
        cd r = std::sqrt(ph * ph - wo * wo);
        pb[i] = ph + r;
        pb[i + N] = ph - r;
    }
    const double fs2 = 2.0 * fsn;
    cd num(std::pow(fs2, N), 0.0);  // N zeros at s = 0
    cd den(1.0, 0.0);
    cd pd[2 * N];
    for (int i = 0; i < 2 * N; ++i) {
        den *= (fs2 - pb[i]);
        pd[i] = (fs2 + pb[i]) / (fs2 - pb[i]);
    }
    double kd = std::pow(bw, N) * (num / den).real();

    // Zeros land on z = +1 (N of them) and z = -1 (N of them). Pair one of
    // each with every conjugate pole pair: (1 - z^-2) / (1 - 2Re(p) z^-1 + |p|^2 z^-2).
    double g = std::pow(std::fabs(kd), 1.0 / N);
    int k = 0;
    for (int i = 0; i < 2 * N && k < N; ++i) {
        if (pd[i].imag() <= 0) continue;
        double gs = (k == 0 && kd < 0) ? -g : g;
        out[k].b0 = (float)gs;
        out[k].b1 = 0.0f;
        out[k].b2 = (float)-gs;
        out[k].a1 = (float)(-2.0 * pd[i].real());
        out[k].a2 = (float)std::norm(pd[i]);
        ++k;
    }
    return k;
}

// Steady-state state of a cascade for a unit step (scipy.signal.sosfilt_zi),
// in transposed direct form II.
static void sos_zi(const Biquad* sos, int nsec, float zi[][2]) {
    double scale = 1.0;
    for (int s = 0; s < nsec; ++s) {
        const Biquad& q = sos[s];
        double bsum = (double)q.b0 + q.b1 + q.b2;
        double asum = 1.0 + q.a1 + q.a2;
        double G = bsum / asum;
        double z2 = q.b2 - q.a2 * G;
        double z1 = q.b1 - q.a1 * G + z2;
        zi[s][0] = (float)(scale * z1);
        zi[s][1] = (float)(scale * z2);
        scale *= G;
    }
}

static inline float sos_step(const Biquad* sos, int nsec, float z[][2], float x) {
    for (int s = 0; s < nsec; ++s) {
        const Biquad& q = sos[s];
        float y = q.b0 * x + z[s][0];
        z[s][0] = q.b1 * x - q.a1 * y + z[s][1];
        z[s][1] = q.b2 * x - q.a2 * y;
        x = y;
    }
    return x;
}

void sosfiltfilt(const Biquad* sos, int nsec, float* x, size_t n) {
    // scipy's default pad: 3 * (2*nsec + 1 - #trailing-zero coefficients).
    int ntaps = 2 * nsec + 1;
    int zb = 0, za = 0;
    for (int s = 0; s < nsec; ++s) { zb += sos[s].b2 == 0.0f; za += sos[s].a2 == 0.0f; }
    ntaps -= std::min(zb, za);
    const size_t edge = (size_t)(3 * ntaps);
    if (n <= edge + 1 || nsec > 8) return;

    float zi[8][2], z[8][2];
    sos_zi(sos, nsec, zi);

    // The odd extensions are built from ORIGINAL samples; the right-hand one
    // would otherwise be read after the forward pass has overwritten it.
    std::vector<float> right(edge);
    for (size_t i = 0; i < edge; ++i) right[i] = 2.0f * x[n - 1] - x[n - 2 - i];

    // Forward pass over [left extension | x | right extension].
    const float x0 = 2.0f * x[0] - x[edge];
    for (int s = 0; s < nsec; ++s) { z[s][0] = zi[s][0] * x0; z[s][1] = zi[s][1] * x0; }
    for (size_t i = edge; i >= 1; --i) sos_step(sos, nsec, z, 2.0f * x[0] - x[i]);
    for (size_t i = 0; i < n; ++i) x[i] = sos_step(sos, nsec, z, x[i]);
    for (size_t i = 0; i < edge; ++i) right[i] = sos_step(sos, nsec, z, right[i]);

    // Backward pass, starting from the far end of the extended signal. The
    // left extension's backward output is trimmed, so it is never computed.
    const float y0 = right[edge - 1];
    for (int s = 0; s < nsec; ++s) { z[s][0] = zi[s][0] * y0; z[s][1] = zi[s][1] * y0; }
    for (size_t i = edge; i-- > 0;) sos_step(sos, nsec, z, right[i]);
    for (size_t i = n; i-- > 0;) x[i] = sos_step(sos, nsec, z, x[i]);
}

void envelope(const float* x, float* env, size_t n, int fs, float win_ms) {
    // Deliberately short (a few hundred microseconds) so the three sub-noises
    // inside one beat stay resolvable. RMS, not energy, so relative peak
    // heights scale linearly with sound pressure -- see dsp.envelope.
    int k = std::max(3, (int)pyround(fs * win_ms / 1000.0));
    if (k % 2 == 0) k += 1;
    std::vector<float> w(k);
    double sum = 0;
    for (int i = 0; i < k; ++i) {
        w[i] = (float)(0.5 - 0.5 * std::cos(2.0 * M_PI * i / (k - 1)));  // np.hanning
        sum += w[i];
    }
    for (int i = 0; i < k; ++i) w[i] = (float)(w[i] / sum);
    const long h = (k - 1) / 2;
    const long N = (long)n;
    for (long i = 0; i < N; ++i) {
        float acc = 0.0f;
        long j0 = i - h;
        if (j0 >= 0 && i + h < N) {
            const float* xp = x + j0;
            for (int t = 0; t < k; ++t) acc += xp[t] * xp[t] * w[t];
        } else {
            for (int t = 0; t < k; ++t) {
                long j = j0 + t;
                if (j >= 0 && j < N) acc += x[j] * x[j] * w[t];
            }
        }
        env[i] = std::sqrt(std::max(0.0f, acc));
    }
}

// ---------------------------------------------------------------------------
// find_peaks (scipy semantics: local maxima with plateau midpoints, then
// height, then distance by descending height, then prominence)
// ---------------------------------------------------------------------------

static void local_maxima(const float* x, size_t n, std::vector<int>& out) {
    out.clear();
    if (n < 3) return;
    size_t i = 1, i_max = n - 1;
    while (i < i_max) {
        if (x[i - 1] < x[i]) {
            size_t ahead = i + 1;
            while (ahead < i_max && x[ahead] == x[i]) ++ahead;
            if (x[ahead] < x[i]) {
                out.push_back((int)((i + ahead - 1) / 2));
                i = ahead;
            }
        }
        ++i;
    }
}

static float prominence_of(const float* x, size_t n, int peak) {
    const float xp = x[peak];
    float left_min = xp;
    for (long i = peak; i >= 0 && x[i] <= xp; --i)
        if (x[i] < left_min) left_min = x[i];
    float right_min = xp;
    for (size_t i = (size_t)peak; i < n && x[i] <= xp; ++i)
        if (x[i] < right_min) right_min = x[i];
    return xp - std::max(left_min, right_min);
}

void find_peaks(const float* x, size_t n, const PeakOpts& opt, std::vector<int>& peaks,
                std::vector<float>* heights) {
    local_maxima(x, n, peaks);
    if (opt.use_height) {
        size_t w = 0;
        for (size_t r = 0; r < peaks.size(); ++r)
            if (x[peaks[r]] >= opt.height) peaks[w++] = peaks[r];
        peaks.resize(w);
    }
    if (opt.distance > 1 && peaks.size() > 1) {
        const int d = opt.distance;
        const size_t m = peaks.size();
        std::vector<int> order(m);
        std::iota(order.begin(), order.end(), 0);
        std::stable_sort(order.begin(), order.end(),
                         [&](int a, int b) { return x[peaks[a]] < x[peaks[b]]; });
        std::vector<uint8_t> keep(m, 1);
        for (size_t r = m; r-- > 0;) {
            int j = order[r];
            if (!keep[j]) continue;
            for (int k = j - 1; k >= 0 && peaks[j] - peaks[k] < d; --k) keep[k] = 0;
            for (size_t k = j + 1; k < m && peaks[k] - peaks[j] < d; ++k) keep[k] = 0;
        }
        size_t w = 0;
        for (size_t r = 0; r < m; ++r)
            if (keep[r]) peaks[w++] = peaks[r];
        peaks.resize(w);
    }
    if (opt.use_prominence) {
        size_t w = 0;
        for (size_t r = 0; r < peaks.size(); ++r)
            if (prominence_of(x, n, peaks[r]) >= opt.prominence) peaks[w++] = peaks[r];
        peaks.resize(w);
    }
    if (heights) {
        heights->resize(peaks.size());
        for (size_t r = 0; r < peaks.size(); ++r) (*heights)[r] = x[peaks[r]];
    }
}

// ---------------------------------------------------------------------------
// Beat period estimation
// ---------------------------------------------------------------------------

// Autocorrelate the envelope and return the beat period in seconds, or 0.
// Returns the SMALLEST lag close to the global maximum (the fundamental, not
// a harmonic), then applies the sub-harmonic guard -- see
// dsp.estimate_beat_period. Runs on a decimated copy; see wgdsp.h.
static double estimate_beat_period(const float* env, size_t n, int fs, AllocFn fast,
                                   FreeFn fast_free, int bph_min = 12000, int bph_max = 43200) {
    if (n < (size_t)(fs / 4)) return 0.0;

    const int D = std::max(1, (int)pyround(fs / 4000.0));
    const double fsd = (double)fs / D;
    // The most recent 8 s is plenty to find a period of at most 0.3 s.
    size_t use = std::min(n, (size_t)(8.0 * fs));
    const float* src = env + (n - use);
    size_t nd = use / D;
    if (nd < 16) return 0.0;
    // Every lag streams the whole of e[], so it goes in fast internal RAM when
    // the platform offers it (32k floats from PSRAM per lag is what made this
    // the slowest stage on the ESP32).
    struct Scratch {
        float* p; FreeFn f;
        ~Scratch() { if (p) f(p); }
    } scratch{fast ? (float*)fast(nd * sizeof(float)) : nullptr, fast_free};
    std::vector<float> heap_e;
    float* e = scratch.p;
    if (!e) { heap_e.resize(nd); e = heap_e.data(); }
    double mean = 0;
    for (size_t i = 0; i < nd; ++i) {
        float acc = 0;
        for (int t = 0; t < D; ++t) acc += src[i * D + t];
        e[i] = acc / D;
        mean += e[i];
    }
    mean /= nd;
    bool any = false;
    for (size_t i = 0; i < nd; ++i) { e[i] -= (float)mean; any |= e[i] != 0.0f; }
    if (!any) return 0.0;

    const long len = (long)nd;
    const long lag_min = (long)(fsd * 3600.0 / bph_max);
    // 3% to spare: a peak exactly AT the end of the searched range is not a
    // peak to find_peaks, which made a 12000 bph watch read as ~12400 bph.
    long lag_max = (long)(fsd * 3600.0 / (bph_min * 0.97));
    lag_max = std::min(lag_max, len - 2);
    if (lag_max <= lag_min + 2) return 0.0;

    // ac[] only where it is read: lag 0 and the search range. The sub-harmonic
    // guard's half-lag probes stop at half >= lag_min, so never below lag_min - 2.
    const long a_lo = std::max(1L, lag_min - 3);
    const long a_hi = std::min(len - 1, lag_max + 2);
    std::vector<float> ac(a_hi + 1, 0.0f);
    auto corr = [&](long lag) {
        double tot = 0;
        float acc = 0;
        long cnt = 0;
        for (long i = 0; i + lag < len; ++i) {
            acc += e[i] * e[i + lag];
            if (++cnt == 256) { tot += acc; acc = 0; cnt = 0; }
        }
        return tot + acc;
    };
    double ac0 = corr(0);
    if (ac0 <= 0) return 0.0;
    ac[0] = 1.0f;
    for (long L = a_lo; L <= a_hi; ++L) ac[L] = (float)(corr(L) / ac0);

    PeakOpts po;
    po.use_height = true;
    po.height = 0.05f;
    po.distance = (int)std::max(2L, lag_min / 4);
    std::vector<int> pk;
    std::vector<float> hts;
    find_peaks(ac.data() + lag_min, (size_t)(lag_max - lag_min), po, pk, &hts);
    if (pk.empty()) return 0.0;

    float hmax = *std::max_element(hts.begin(), hts.end());
    long lag = -1;
    for (size_t i = 0; i < pk.size(); ++i)
        if (hts[i] >= 0.55f * hmax && (lag < 0 || pk[i] < lag)) lag = pk[i];
    if (lag < 0) lag = pk[std::max_element(hts.begin(), hts.end()) - hts.begin()];
    lag += lag_min;

    // Sub-harmonic guard: if there is real correlation at half the chosen
    // lag, that half is the fundamental (tick and tock never sound alike).
    for (int it = 0; it < 2; ++it) {
        long half = lag / 2;
        if (half < lag_min || half >= (long)ac.size() - 1) break;
        long lo = std::max(0L, half - 2), hi = std::min((long)ac.size(), half + 3);
        long arg = lo;
        for (long i = lo; i < hi; ++i)
            if (ac[i] > ac[arg]) arg = i;
        float nmax = ac[arg];
        float ref = ac[std::min(lag, (long)ac.size() - 1)];
        if (nmax > 0.32f * ref && nmax > 0.08f) lag = arg;
        else break;
    }

    double flag = (double)lag;
    if (lag > 0 && lag < (long)ac.size() - 1) {
        double y0 = ac[lag - 1], y1 = ac[lag], y2 = ac[lag + 1];
        double den = y0 - 2 * y1 + y2;
        if (den != 0) flag = lag + 0.5 * (y0 - y2) / den;
    }
    return flag / fsd;
}

// Robust floor of the normalised envelope: median and MAD over a strided
// subsample (see wgdsp.h).
struct Floor { double median, mad; };
static Floor envelope_floor(const float* env, size_t n, float emax) {
    size_t stride = std::max<size_t>(1, n / 65536);
    std::vector<float> s;
    s.reserve(n / stride + 1);
    for (size_t i = 0; i < n; i += stride) s.push_back(env[i] / emax);
    double med = median_of(s);
    for (float& v : s) v = std::fabs(v - (float)med);
    double mad = median_inplace(s);
    return {med, mad};
}

// Cross-check the autocorrelation estimate against actual peak spacing; it
// can settle on twice the beat interval when tick and tock differ a lot.
static double verify_period(const float* env, size_t n, int fs, double period,
                            float emax, const Floor& fl) {
    if (emax <= 0) return period;
    double prom = std::max(0.10, 4.0 * (fl.mad + 1e-12));
    PeakOpts po;
    po.distance = std::max(3, (int)(0.38 * period * fs));
    po.use_prominence = true;
    po.prominence = (float)(std::min(prom, 0.5) * emax);
    // A peak's prominence never exceeds its height, so prefiltering on height
    // gives the same peaks and spares sorting every noise maximum.
    po.use_height = true;
    po.height = po.prominence;
    std::vector<int> idx;
    find_peaks(env, n, po, idx);
    if (idx.size() < 8) return period;
    std::vector<double> d(idx.size() - 1);
    for (size_t i = 1; i < idx.size(); ++i) d[i - 1] = idx[i] - idx[i - 1];
    std::vector<double> dd = d;
    double med = median_inplace(dd) / fs;
    if (0.40 * period < med && med < 0.64 * period) {
        // Real beats at half the period are evenly spaced and similar in
        // height; noise peaks between beats are neither -- see dsp.verify_period.
        for (double& v : d) v = std::fabs(v / fs - med);
        double spread = median_inplace(d) / (med + 1e-12);
        std::vector<double> h(idx.size());
        for (size_t i = 0; i < idx.size(); ++i) h[i] = env[idx[i]];
        double hratio = percentile(h, 25) / (percentile(h, 75) + 1e-12);
        if (spread < 0.06 || (spread < 0.10 && hratio > 0.4)) return period / 2.0;
        return period;
    }
    if (1.55 * period < med && med < 2.60 * period) return period * 2.0;
    return period;
}

// Coarse: one detection per beat, using a refractory distance.
static void detect_beats(const float* env, size_t n, int fs, double period, float emax,
                         const Floor& fl, std::vector<int>& out,
                         double prominence_frac = 0.10) {
    out.clear();
    if (emax <= 0) return;
    double prom = std::max(prominence_frac, 4.0 * (fl.mad + 1e-12));
    PeakOpts po;
    po.distance = std::max(4, (int)(0.55 * period * fs));
    po.use_prominence = true;
    po.prominence = (float)(std::min(prom, 0.5) * emax);
    po.use_height = true;          // same peaks, far fewer to sort (see verify_period)
    po.height = po.prominence;
    find_peaks(env, n, po, out);
}

// ---------------------------------------------------------------------------
// Beat refinement
// ---------------------------------------------------------------------------

struct BeatSet {
    std::vector<double> times;  // seconds from buffer start
    double period = 0;
    double snr_db = 0;
    double quality = 0;
};

static double parabolic_peak(const std::vector<float>& y, long i) {
    if (i <= 0 || i >= (long)y.size() - 1) return (double)i;
    double d = (double)y[i - 1] - 2.0 * y[i] + y[i + 1];
    if (d == 0) return (double)i;
    return i + 0.5 * ((double)y[i - 1] - y[i + 1]) / d;
}

// 'same'-mode cross-correlation of two equal-length vectors, evaluated only
// for indices [lo, hi) plus one either side (for the parabolic fit).
// cc[mid + L] = sum_n a[n + L] * b[n], mid = len/2 -- scipy.signal.correlate.
static void corr_same(const float* a, const float* b, long len, long lo, long hi,
                      std::vector<float>& cc) {
    cc.assign(len, 0.0f);
    const long mid = len / 2;
    for (long j = std::max(0L, lo - 1); j < std::min(len, hi + 1); ++j) {
        long L = j - mid;
        long n0 = std::max(0L, -L), n1 = std::min(len, len - L);
        float acc = 0.0f;
        for (long t = n0; t < n1; ++t) acc += a[t + L] * b[t];
        cc[j] = acc;
    }
}

// Cross-correlate each beat against an averaged template for sub-sample
// timing. Even and odd beats get SEPARATE templates so the tick/tock waveform
// difference cannot leak into beat error; the offset between the two
// templates' anchors is measured and taken back out -- see dsp.refine_beats.
static BeatSet refine_beats(const float* xf, size_t n, int fs, const std::vector<int>& coarse,
                            double period, double pre_ms = 12.0, double post_ms = 15.0,
                            int iterations = 2) {
    BeatSet bs;
    bs.period = period;
    const long pre = (long)std::min(fs * pre_ms / 1000.0, 0.28 * period * fs);
    const long post = (long)std::min(fs * post_ms / 1000.0, 0.32 * period * fs);
    const long L = pre + post;
    const size_t nb = coarse.size();
    std::vector<double> centers(nb);
    for (size_t i = 0; i < nb; ++i) centers[i] = coarse[i];
    if (nb < 8) {
        bs.times.resize(nb);
        for (size_t i = 0; i < nb; ++i) bs.times[i] = centers[i] / fs;
        return bs;
    }

    std::vector<float> tpl[2];
    std::vector<float> row(L), cc;
    std::vector<long> ic(nb);
    std::vector<uint8_t> ok(nb);
    std::vector<double> shifts(nb), corrs(nb);
    const long lim = (long)(fs * 1.5 / 1000.0);
    const long mid = L / 2;
    double quality = 0.0;

    for (int it = 0; it < iterations; ++it) {
        size_t nok = 0;
        for (size_t i = 0; i < nb; ++i) {
            ic[i] = pyround(centers[i]);
            ok[i] = ic[i] >= pre && ic[i] < (long)n - post;
            nok += ok[i];
        }
        if (nok < 8) break;
        std::fill(shifts.begin(), shifts.end(), 0.0);
        std::fill(corrs.begin(), corrs.end(), 0.0);

        for (int p = 0; p < 2; ++p) {
            std::vector<size_t> sel;
            for (size_t i = p; i < nb; i += 2)
                if (ok[i]) sel.push_back(i);
            if (sel.size() < 3) continue;
            // Template: mean of the rows, mean-removed, unit norm.
            std::vector<double> acc(L, 0.0);
            for (size_t k : sel) {
                const float* r = xf + ic[k] - pre;
                for (long t = 0; t < L; ++t) acc[t] += r[t];
            }
            std::vector<float> t(L);
            double tm = 0;
            const double inv_n = 1.0 / sel.size();
            for (long q = 0; q < L; ++q) { acc[q] *= inv_n; tm += acc[q]; }
            tm /= L;
            double tn = 0;
            for (long q = 0; q < L; ++q) { acc[q] -= tm; tn += acc[q] * acc[q]; }
            tn = std::sqrt(tn);
            if (tn == 0) continue;
            const double inv_tn = 1.0 / tn;
            for (long q = 0; q < L; ++q) t[q] = (float)(acc[q] * inv_tn);
            tpl[p] = t;

            for (size_t k : sel) {
                // Single precision: ~1300 samples per row, and double is
                // software-emulated on the ESP32.
                const float* r0 = xf + ic[k] - pre;
                float rm = 0;
                for (long q = 0; q < L; ++q) rm += r0[q];
                rm /= (float)L;
                float rn = 0;
                for (long q = 0; q < L; ++q) {
                    row[q] = r0[q] - rm;
                    rn += row[q] * row[q];
                }
                rn = std::sqrt(rn);
                if (!(rn > 0)) continue;
                const float inv = 1.0f / rn;
                for (long q = 0; q < L; ++q) row[q] *= inv;
                long lo = std::max(0L, mid - lim), hi = std::min(L, mid + lim + 1);
                corr_same(row.data(), t.data(), L, lo, hi, cc);
                long j = lo;
                for (long q = lo; q < hi; ++q)
                    if (cc[q] > cc[j]) j = q;
                shifts[k] = parabolic_peak(cc, j) - mid;
                corrs[k] = cc[j];
            }
        }

        if (!tpl[0].empty() && !tpl[1].empty() && tpl[0].size() == tpl[1].size()) {
            long lim4 = (long)(fs * 4.0 / 1000.0);
            long lo = std::max(0L, mid - lim4), hi = std::min(L, mid + lim4 + 1);
            corr_same(tpl[1].data(), tpl[0].data(), L, lo, hi, cc);
            long j = lo;
            for (long q = lo; q < hi; ++q)
                if (cc[q] > cc[j]) j = q;
            double d = parabolic_peak(cc, j) - mid;
            for (size_t i = 1; i < nb; i += 2) shifts[i] += d;
        }

        for (size_t i = 0; i < nb; ++i) centers[i] += shifts[i];
        double qs = 0;
        for (size_t i = 0; i < nb; ++i)
            if (ok[i]) qs += std::min(1.0, std::max(0.0, corrs[i]));
        quality = qs / nok;
    }

    // Rough SNR: signal at the beats against the median level between them.
    std::vector<float> pk(nb);
    for (size_t i = 0; i < nb; ++i) {
        long c = std::min((long)n - 1, std::max(0L, (long)pyround(centers[i])));
        pk[i] = std::fabs(xf[c]);
    }
    size_t stride = std::max<size_t>(1, n / 65536);
    std::vector<float> ab;
    ab.reserve(n / stride + 1);
    for (size_t i = 0; i < n; i += stride) ab.push_back(std::fabs(xf[i]));
    double floor = median_inplace(ab) + 1e-12;
    bs.snr_db = 20.0 * std::log10((median_inplace(pk) + 1e-12) / floor);

    bs.times.resize(nb);
    for (size_t i = 0; i < nb; ++i) bs.times[i] = centers[i] / fs;
    bs.quality = quality;
    return bs;
}

// ---------------------------------------------------------------------------
// Impulse interval (drives amplitude)
// ---------------------------------------------------------------------------

struct ImpulseResult {
    std::vector<double> dt;       // per beat, NaN where invalid
    std::vector<double> p1_off;   // per beat, samples, NaN where not found
    std::vector<float> mean_shape;
    int shape_pre = 0;
    float p1 = 0, p3 = 0;
    double extra_peaks = 0;
    size_t valid = 0;
};

// Physically plausible window for the 1st-to-3rd noise interval, from
// lift/2 = A sin(pi dt / T_osc).
static void dt_bounds(double bph, double lift, double amp_min, double amp_max,
                      double& lo, double& hi) {
    lo = dt_from_amplitude(amp_max, bph, lift);
    hi = dt_from_amplitude(amp_min, bph, lift);
}

// The three sub-noises of one beat form a tight cluster: walk outward from
// the peak nearest the anchor while gaps stay inside max_gap, then end the
// span on the loudest peak (the drop) so a trailing echo cannot stretch it.
static bool group_span(const std::vector<int>& peaks, const std::vector<float>& heights,
                       long anchor, long max_gap, long& first, long& last, int& n_found) {
    n_found = 0;
    if (peaks.empty()) return false;
    size_t i = 0;
    long best = std::labs(peaks[0] - anchor);
    for (size_t k = 1; k < peaks.size(); ++k) {
        long d = std::labs(peaks[k] - anchor);
        if (d < best) { best = d; i = k; }
    }
    size_t lo = i, hi = i;
    while (lo > 0 && peaks[lo] - peaks[lo - 1] <= max_gap) --lo;
    while (hi + 1 < peaks.size() && peaks[hi + 1] - peaks[hi] <= max_gap) ++hi;
    n_found = (int)(hi - lo + 1);
    size_t end = std::min(hi, lo + 2);
    if (hi > lo) {
        size_t j = lo;
        for (size_t k = lo; k <= hi; ++k)
            if (heights[k] > heights[j]) j = k;
        if (j > lo) end = j;
    }
    first = peaks[lo];
    last = peaks[end];
    return true;
}

static ImpulseResult measure_impulse(const float* env, size_t n, int fs,
                                     const std::vector<double>& times, double bph,
                                     double lift, double rel_threshold,
                                     double amp_min = 120.0, double amp_max = 355.0) {
    ImpulseResult r;
    double lo_dt, hi_dt;
    dt_bounds(bph, lift, amp_min, amp_max, lo_dt, hi_dt);
    const double period = 3600.0 / bph;
    // Capped at 60 ms: covers every standard beat rate (12000 bph needs ~46 ms)
    // and keeps a nonsense forced rate from allocating megabytes.
    const long half = (long)std::min(std::min(fs * (hi_dt + 0.004), 0.42 * period * fs), 0.060 * fs);
    const long pre = half, post = half, W = pre + post;
    const size_t nb = times.size();
    r.dt.assign(nb, NaN);
    r.p1_off.assign(nb, NaN);
    const long lo_n = (long)(lo_dt * fs), hi_n = (long)(hi_dt * fs);
    const int min_sep = std::max(2, (int)(fs * 0.30 / 1000.0));
    const long max_gap = hi_n;

    std::vector<double> shape(W, 0.0);
    size_t rows = 0, n_ok = 0;
    long extra = 0;
    std::vector<int> pk;
    std::vector<float> ph;
    std::vector<float> s(W);
    PeakOpts po;
    po.use_height = true;
    po.distance = min_sep;

    for (size_t k = 0; k < nb; ++k) {
        long c = pyround(times[k] * fs);
        if (!(c >= pre && c < (long)n - post)) continue;
        ++n_ok;
        const float* seg = env + c - pre;
        float mx = *std::max_element(seg, seg + W);
        if (W < 8 || mx <= 0) continue;
        for (long q = 0; q < W; ++q) s[q] = seg[q] / mx;
        for (long q = 0; q < W; ++q) shape[q] += s[q];
        ++rows;
        po.height = (float)rel_threshold;
        find_peaks(s.data(), W, po, pk, &ph);
        long first, last;
        int nf;
        if (!group_span(pk, ph, pre, max_gap, first, last, nf)) continue;
        extra += std::max(0, nf - 3);
        r.p1_off[k] = (double)(first - pre);
        long width = last - first;
        if (lo_n <= width && width <= hi_n) {
            r.dt[k] = (double)width / fs;
            ++r.valid;
        }
    }

    if (rows) {
        r.mean_shape.resize(W);
        double mx = 0;
        for (long q = 0; q < W; ++q) { shape[q] /= rows; mx = std::max(mx, shape[q]); }
        for (long q = 0; q < W; ++q) r.mean_shape[q] = (float)(shape[q] / (mx + 1e-12));
        po.height = (float)rel_threshold;
        find_peaks(r.mean_shape.data(), W, po, pk, &ph);
        long first, last;
        int nf;
        if (group_span(pk, ph, pre, max_gap, first, last, nf)) {
            r.p1 = (float)first;
            r.p3 = (float)last;
        }
    }
    r.shape_pre = (int)pre;
    r.extra_peaks = (double)extra / std::max<size_t>(1, n_ok);
    return r;
}

// ---------------------------------------------------------------------------
// Rate and beat error
// ---------------------------------------------------------------------------

// Least squares for t = a + b*i + c*(-1)^i over the kept points. The
// alternating column is the beat error; without it the slope is biased
// whenever the window holds an odd number of beats.
static void lstsq3(const std::vector<double>& x, const std::vector<double>& y,
                   const std::vector<uint8_t>* keep, double& a, double& b, double& c) {
    // Centre x for conditioning, then solve the 3x3 normal equations.
    double n = 0, xm = 0;
    for (size_t i = 0; i < x.size(); ++i)
        if (!keep || (*keep)[i]) { n += 1; xm += x[i]; }
    xm /= n;
    double S[3][3] = {{0}}, R[3] = {0};
    for (size_t i = 0; i < x.size(); ++i) {
        if (keep && !(*keep)[i]) continue;
        const double v[3] = {1.0, x[i] - xm, ((long long)x[i] % 2) ? -1.0 : 1.0};
        for (int r = 0; r < 3; ++r) {
            R[r] += v[r] * y[i];
            for (int q = 0; q < 3; ++q) S[r][q] += v[r] * v[q];
        }
    }
    // Gaussian elimination with partial pivoting.
    double M[3][4];
    for (int r = 0; r < 3; ++r) { for (int q = 0; q < 3; ++q) M[r][q] = S[r][q]; M[r][3] = R[r]; }
    for (int col = 0; col < 3; ++col) {
        int piv = col;
        for (int r = col + 1; r < 3; ++r) if (std::fabs(M[r][col]) > std::fabs(M[piv][col])) piv = r;
        if (piv != col) for (int q = 0; q < 4; ++q) std::swap(M[col][q], M[piv][q]);
        if (M[col][col] == 0) { a = y.empty() ? 0 : y[0]; b = 0; c = 0; return; }
        for (int r = col + 1; r < 3; ++r) {
            double f = M[r][col] / M[col][col];
            for (int q = col; q < 4; ++q) M[r][q] -= f * M[col][q];
        }
    }
    double sol[3];
    for (int r = 2; r >= 0; --r) {
        double acc = M[r][3];
        for (int q = r + 1; q < 3; ++q) acc -= M[r][q] * sol[q];
        sol[r] = acc / M[r][r];
    }
    b = sol[1];
    a = sol[0] - b * xm;
    c = sol[2];
}

// Least-squares fit of beat time against beat INDEX, with indices assigned
// from successive intervals so a missed beat shifts one point instead of
// corrupting everything after it -- see analysis.fit_rate. Residuals are
// about the straight line alone, so they still carry the beat error.
static bool fit_rate(const std::vector<double>& t, double period_guess,
                     double& a, double& period, std::vector<double>& idx,
                     std::vector<double>& resid, int passes = 3) {
    const size_t n = t.size();
    period = period_guess;
    if (n < 8) { idx.clear(); resid.clear(); return false; }
    a = t[0];
    idx.assign(n, 0.0);
    resid.assign(n, 0.0);
    std::vector<double> full(n), tmp;
    for (int p = 0; p < passes; ++p) {
        idx[0] = 0;
        for (size_t i = 1; i < n; ++i) {
            double st = std::nearbyint((t[i] - t[i - 1]) / period);
            st = std::min(8.0, std::max(1.0, st));
            idx[i] = idx[i - 1] + st;
        }
        double c;
        lstsq3(idx, t, nullptr, a, period, c);
        for (size_t i = 0; i < n; ++i)
            full[i] = t[i] - (a + period * idx[i] + (((long long)idx[i] % 2) ? -c : c));
        if (n > 20) {
            tmp = full;
            double med = median_inplace(tmp);
            for (size_t i = 0; i < n; ++i) tmp[i] = std::fabs(full[i] - med);
            double s = 1.4826 * median_inplace(tmp) + 1e-12;
            std::vector<uint8_t> keep(n);
            size_t nk = 0;
            for (size_t i = 0; i < n; ++i) {
                keep[i] = std::fabs(full[i] - med) < 5 * s;
                nk += keep[i];
            }
            if (nk > 8 && nk < n) lstsq3(idx, t, &keep, a, period, c);
        }
        for (size_t i = 0; i < n; ++i) resid[i] = t[i] - (a + period * idx[i]);
    }
    return true;
}

static double rate_spd(double measured_period, double nominal_bph) {
    if (measured_period <= 0) return NaN;
    return ((3600.0 / nominal_bph) / measured_period - 1.0) * 86400.0;
}

// Beat error = |T1 - T2| / 2 = gap between the mean residual of even and
// odd beats.
static double beat_error_ms(const std::vector<double>& idx, const std::vector<double>& resid) {
    if (idx.size() < 8) return NaN;
    double se = 0, so = 0;
    size_t ne = 0, no = 0;
    for (size_t i = 0; i < idx.size(); ++i) {
        if (((long long)idx[i]) % 2 == 0) { se += resid[i]; ++ne; }
        else { so += resid[i]; ++no; }
    }
    if (ne < 3 || no < 3) return NaN;
    return std::fabs(se / ne - so / no) * 1000.0;
}

// ---------------------------------------------------------------------------
// Analyzer
// ---------------------------------------------------------------------------

Analyzer::Analyzer(size_t max_samples, AllocFn alloc, FreeFn release, ClockFn clock,
                   AllocFn fast, FreeFn fast_free)
    : cap_(max_samples), free_(release ? release : std::free), clock_(clock), fast_(fast),
      fast_free_(fast_free ? fast_free : std::free) {
    AllocFn a = alloc ? alloc : std::malloc;
    xf_ = (float*)a(max_samples * sizeof(float));
    env_ = (float*)a(max_samples * sizeof(float));
    if (!xf_ || !env_) {
        if (xf_) free_(xf_);
        if (env_) free_(env_);
        xf_ = env_ = nullptr;
        cap_ = 0;
    }
}

Analyzer::~Analyzer() {
    if (xf_) free_(xf_);
    if (env_) free_(env_);
}

static std::string fmt(const char* f, ...) __attribute__((format(printf, 1, 2)));
static std::string fmt(const char* f, ...) {
    char buf[320];
    va_list ap;
    va_start(ap, f);
    vsnprintf(buf, sizeof buf, f, ap);
    va_end(ap);
    return buf;
}

Measurement Analyzer::analyze(size_t n, int fs, const Config& cfg) {
    Measurement m;
    m.lift_angle = cfg.lift_angle;
    const double t_start = now();
    if (n > cap_) n = cap_;
    float* x = xf_;
    m.duration = (double)n / fs;

    if (n < (size_t)(fs / 2)) {
        m.message = "Not enough audio yet.";
        return m;
    }
    // Blocked float sums: exact enough, and no software double per sample.
    float peak = 0;
    double mean = 0;
    for (size_t i0 = 0; i0 < n; i0 += 1024) {
        float acc = 0;
        const size_t i1 = std::min(n, i0 + 1024);
        for (size_t i = i0; i < i1; ++i) {
            float v = x[i];
            acc += v;
            float a = std::fabs(v);
            if (a > peak) peak = a;
        }
        mean += acc;
    }
    if (peak < 1e-5f) {
        m.message = "Signal is silent. Check the microphone input and gain.";
        return m;
    }
    mean /= n;
    const float fmean = (float)mean;
    for (size_t i = 0; i < n; ++i) x[i] -= fmean;

    Biquad sos[4];
    int nsec = design_bandpass(fs, cfg.band_lo, cfg.band_hi, sos);
    sosfiltfilt(sos, nsec, x, n);
    envelope(x, env_, n, fs, cfg.env_win_ms);
    const double t_filt = now();
    m.t_filter_ms = (float)(t_filt - t_start);

    double period = estimate_beat_period(env_, n, fs, fast_, fast_free_);
    if (!(period > 0)) {
        m.message = "No periodic escapement signal found. Reposition the watch on the pickup.";
        return m;
    }
    float emax = *std::max_element(env_, env_ + n);
    Floor fl = envelope_floor(env_, n, emax > 0 ? emax : 1.0f);
    period = verify_period(env_, n, fs, period, emax, fl);
    m.raw_bph = 3600.0 / period;

    std::vector<int> coarse;
    detect_beats(env_, n, fs, period, emax, fl, coarse);
    const double t_per = now();
    m.t_period_ms = (float)(t_per - t_filt);
    if (coarse.size() < 12) {
        m.message = fmt("Only %d beats detected. Signal is too weak or too noisy.",
                        (int)coarse.size());
        return m;
    }

    BeatSet beats = refine_beats(x, n, fs, coarse, period);
    m.snr_db = beats.snr_db;
    m.quality = beats.quality;
    m.beats = (int)beats.times.size();
    const double t_ref = now();
    m.t_refine_ms = (float)(t_ref - t_per);

    // Always record what the audio measured, independently of a forced value
    // -- overwriting it hides a caliber selection that does not match.
    int snapped = snap_bph(m.raw_bph, 0.02);
    m.detected_bph = snapped ? snapped : (int)pyround(m.raw_bph);
    const int nominal = cfg.forced_bph > 0 ? cfg.forced_bph : m.detected_bph;
    m.nominal_bph = nominal;
    if (cfg.forced_bph > 0 && m.detected_bph != nominal) {
        m.message = fmt("Measured %d bph but rate is being computed against %d bph. The rate "
                        "figure is meaningless until these agree -- check the caliber "
                        "selection, or switch beat rate to Auto-detect.",
                        m.detected_bph, nominal);
    }

    ImpulseResult imp = measure_impulse(env_, n, fs, beats.times, nominal, cfg.lift_angle,
                                        cfg.sub_threshold);
    m.t_impulse_ms = (float)(now() - t_ref);

    // Parity anchor correction: the unlocking noise is the same physical
    // event on both half swings, so the even/odd difference in its offset
    // from the anchor is the anchor error and nothing else. Gated hard -- a
    // genuine anchor flip is several ms; a small offset is noise, and
    // "correcting" it would subtract real beat error. See analysis.analyze.
    std::vector<double> times = beats.times;
    {
        std::vector<double> ev, od, dts;
        for (size_t i = 0; i < times.size(); ++i) {
            if (!std::isfinite(imp.p1_off[i])) continue;
            (i % 2 ? od : ev).push_back(imp.p1_off[i]);
        }
        for (double d : imp.dt)
            if (std::isfinite(d)) dts.push_back(d);
        if (ev.size() >= 5 && od.size() >= 5) {
            double d = (median_inplace(od) - median_inplace(ev)) / fs;
            double dt_ref = dts.empty() ? 0.008 : median_inplace(dts);
            double floor = std::max(0.0012, 0.25 * dt_ref);
            m.parity_offset_seen = d * 1000.0;
            if (floor < std::fabs(d) && std::fabs(d) < 0.4 * period && !cfg.no_parity_fix) {
                for (size_t i = 1; i < times.size(); i += 2) times[i] += d;
                m.parity_correction = d * 1000.0;
            }
        }
    }

    double a, fitted;
    std::vector<double> idx, resid;
    if (!fit_rate(times, period, a, fitted, idx, resid)) {
        m.message = "Could not fit a stable beat sequence.";
        return m;
    }
    // Beat rate from the fit, which is exact, rather than the autocorrelation
    // estimate, which a large beat error biases by a few percent -- see
    // analysis.analyze.
    int nominal_bph = nominal;
    if (fitted > 0) {
        m.raw_bph = 3600.0 / fitted;
        int snapped2 = snap_bph(m.raw_bph, 0.02);
        int detected = snapped2 ? snapped2 : (int)pyround(m.raw_bph);
        if (detected != m.detected_bph) {
            m.detected_bph = detected;
            if (cfg.forced_bph <= 0) {
                nominal_bph = detected;
                m.nominal_bph = nominal_bph;
            } else if (detected == nominal_bph) {
                m.message.clear();
            } else {
                m.message = fmt("Measured %d bph but rate is being computed against %d bph. The "
                                "rate figure is meaningless until these agree -- check the "
                                "caliber selection, or switch beat rate to Auto-detect.",
                                m.detected_bph, nominal_bph);
            }
        }
    }
    if (!(STANDARD_BPH[0] * 0.97 <= m.raw_bph && m.raw_bph <= STANDARD_BPH[9] * 1.03)) {
        m.message = fmt("Measured %.0f bph, outside any watch's range. The pickup is hearing "
                        "something other than the escapement.", m.raw_bph);
        return m;
    }
    m.rate = rate_spd(fitted, nominal_bph);
    m.beat_error = beat_error_ms(idx, resid);
    const double nominal_period = 3600.0 / nominal_bph;

    // Instantaneous rate, ~1 s running mean ("valid" mode, no drooping edges).
    if (times.size() >= 12) {
        std::vector<double> inst, tm;
        for (size_t i = 1; i < times.size(); ++i) {
            double di = idx[i] - idx[i - 1];
            if (di == 0) continue;
            double dtb = (times[i] - times[i - 1]) / di;
            double v = (nominal_period / dtb - 1.0) * 86400.0;
            if (!std::isfinite(v)) continue;
            inst.push_back(v);
            tm.push_back((times[i] + times[i - 1]) / 2.0 - times[0]);
        }
        if (inst.size() >= 8) {
            size_t k = std::max(3L, (long)pyround(1.0 / nominal_period) | 1L);
            if (inst.size() > k) {
                size_t ns = inst.size() - k + 1, off = (inst.size() - ns) / 2;
                double run = 0;
                for (size_t i = 0; i < k; ++i) run += inst[i];
                for (size_t i = 0; i < ns; ++i) {
                    if (i) run += inst[i + k - 1] - inst[i - 1];
                    m.inst_rate.push_back((float)(run / k));
                    m.inst_rate_t.push_back((float)tm[off + i]);
                }
            } else {
                for (size_t i = 0; i < inst.size(); ++i) {
                    m.inst_rate.push_back((float)inst[i]);
                    m.inst_rate_t.push_back((float)tm[i]);
                }
            }
        }
    }

    // 95% confidence half-width on the rate, from the beat-time scatter
    // about the fitted line.
    if (idx.size() >= 8 && fitted > 0) {
        double im = 0;
        for (double v : idx) im += v;
        im /= idx.size();
        double ss = 0, rr = 0;
        for (size_t i = 0; i < idx.size(); ++i) {
            ss += (idx[i] - im) * (idx[i] - im);
            rr += resid[i] * resid[i];
        }
        if (ss > 0) {
            double dof = std::max<double>(1, (double)idx.size() - 2);
            double se_period = std::sqrt(rr / dof) / std::sqrt(ss);
            double dr = nominal_period / (fitted * fitted) * 86400.0;
            m.rate_ci = 1.96 * dr * se_period;
        }
    }

    if (imp.valid >= 8) {
        std::vector<double> dts;
        for (double d : imp.dt)
            if (std::isfinite(d)) dts.push_back(d);
        m.dt_mean = median_of(dts);
        m.amplitude = amplitude_from_dt(m.dt_mean, nominal_bph, cfg.lift_angle, cfg.exact_amplitude);
        std::vector<double> amps;
        for (double d : dts) {
            double v = amplitude_from_dt(d, nominal_bph, cfg.lift_angle, cfg.exact_amplitude);
            if (std::isfinite(v)) {
                amps.push_back(v);
                m.amp_samples.push_back((float)v);
            }
        }
        if (amps.size() > 4) m.amplitude_spread = percentile(amps, 75) - percentile(amps, 25);
    } else {
        m.message = "Rate and beat error are good, but the unlocking noise is not clearly "
                    "resolvable -- amplitude unavailable. Try more gain, better acoustic "
                    "coupling, or a lower sub-noise threshold.";
    }

    m.extra_peaks = imp.extra_peaks;
    m.valid_frac = (double)imp.valid / std::max<size_t>(1, imp.dt.size());
    m.mean_shape = std::move(imp.mean_shape);
    m.shape_fs = fs;
    m.shape_pre = imp.shape_pre;
    m.p1_idx = imp.p1;
    m.p3_idx = imp.p3;
    if (imp.extra_peaks > 0.35 && m.message.empty()) {
        m.message = fmt("Detecting %.1f noises per beat instead of 3. Something beyond the "
                        "escapement is being picked up -- case resonance, the rotor, or a "
                        "reflection. Amplitude is the reading at risk; check the beat "
                        "waveform panel.",
                        3 + imp.extra_peaks);
    }
    m.times = std::move(times);
    m.index = std::move(idx);
    m.resid = std::move(resid);
    m.ok = true;
    if (m.message.empty()) m.message = "OK";
    m.t_total_ms = (float)(now() - t_start);
    return m;
}

}  // namespace wg
