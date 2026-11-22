#include "wgsim.h"

#include <algorithm>
#include <cmath>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

namespace wg {

SimWatch::SimWatch(int fs, uint32_t seed) : fs_(fs), rng_(seed ? seed : 1) {
    set_params(p_);
}

void SimWatch::set_params(const SimParams& p) {
    p_ = p;
    double nominal = 3600.0 / p_.bph;
    period_ = nominal / (1.0 + p_.rate_spd / 86400.0);
    double t_osc = 2.0 * 3600.0 / p_.bph;
    // Same harmonic relation the analyzer inverts, run forwards.
    double s = (p_.lift_angle / 2.0) / std::max(1.0f, p_.amplitude);
    s = std::min(1.0, std::max(-1.0, s));
    dt_ = (t_osc / M_PI) * std::asin(s);
    c_ = (p_.beat_error_ms / 1000.0) / 2.0;
    // Re-anchor the beat phase at the playhead, so a live change does not
    // multiply the old beat index by the new period.
    t0_ = (double)abs_ / fs_ + 0.01;
    k_ = 0;
}

float SimWatch::gauss() {
    if (have_spare_) { have_spare_ = false; return spare_; }
    auto uni = [this]() {
        rng_ ^= rng_ << 13; rng_ ^= rng_ >> 17; rng_ ^= rng_ << 5;   // xorshift32
        return ((rng_ >> 8) + 0.5f) / 16777216.0f;
    };
    float u = uni(), v = uni();
    float r = std::sqrt(-2.0f * std::log(u));
    spare_ = r * std::sin(2.0f * (float)M_PI * v);
    have_spare_ = true;
    return r * std::cos(2.0f * (float)M_PI * v);
}

void SimWatch::generate(float* out, size_t n) {
    const long pad = (long)(fs_ * (dt_ + 0.006)) + 4;
    std::vector<float> buf(n + pad, 0.0f);
    for (size_t i = 0; i < tail_.size() && i < buf.size(); ++i) buf[i] += tail_[i];

    const long ln = (long)(fs_ * 0.004);
    for (;;) {
        double t_beat = t0_ + k_ * period_ + ((k_ % 2) ? -c_ : c_);
        long long s_beat = std::llround(t_beat * fs_);
        if (s_beat >= abs_ + (long long)n) break;
        long local = (long)(s_beat - abs_);
        if (local < 0) { ++k_; continue; }
        double tone = (k_ % 2 == 0) ? 4200.0 : 4700.0;
        // 1: unlocking (quiet)  2: impulse  3: drop onto the opposite stone (loud)
        const double ev[3][4] = {{0.0, 0.35, tone * 1.15, 0.35},
                                 {dt_ * 0.55, 0.55, tone, 0.45},
                                 {dt_, 1.00, tone * 0.92, 0.60}};
        for (const auto& e : ev) {
            long s = local + (long)(e[0] * fs_);
            if (s < 0 || s + ln >= (long)buf.size()) continue;
            double w = 2.0 * M_PI * e[2] / fs_, tau = e[3] / 1000.0 * fs_;
            for (long i = 0; i < ln; ++i)
                buf[s + i] += (float)(e[1] * std::sin(w * i) * std::exp(-i / tau));
        }
        ++k_;
    }
    tail_.assign(buf.begin() + n, buf.end());

    const float noise_rms = 0.09f / std::pow(10.0f, p_.snr_db / 20.0f) * 3.0f;
    for (size_t i = 0; i < n; ++i) out[i] = (buf[i] + noise_rms * gauss()) * 0.7f;
    abs_ += (long long)n;
}

}  // namespace wg
