// Simulated watch: a port of audio.SimulatedRecorder's synthesis. Generates
// escapement audio with a known rate, amplitude and beat error in streaming
// chunks, so the firmware can be exercised end to end with no pickup fitted.
#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace wg {

struct SimParams {
    int bph = 28800;
    float amplitude = 275.0f;
    float lift_angle = 52.0f;
    float rate_spd = 0.0f;
    float beat_error_ms = 0.0f;
    float snr_db = 18.0f;
};

class SimWatch {
public:
    explicit SimWatch(int fs = 48000, uint32_t seed = 12345);
    void set_params(const SimParams& p);
    const SimParams& params() const { return p_; }
    // Fill `out` with the next n samples (full scale +/-1).
    void generate(float* out, size_t n);

private:
    float gauss();
    int fs_;
    SimParams p_;
    double period_ = 0.125, dt_ = 0.01, c_ = 0.0, t0_ = 0.0;
    long long abs_ = 0;   // absolute sample position of the next chunk
    long k_ = 0;          // beat index since the last phase anchor
    std::vector<float> tail_;
    uint32_t rng_;
    bool have_spare_ = false;
    float spare_ = 0.0f;
};

}  // namespace wg
