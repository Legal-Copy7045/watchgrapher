// Host driver for the DSP core.
//
//   wgcli FILE.f32 FS [lift] [forced_bph]   analyse raw little-endian float32
//   wgcli --sim SECONDS [bph amp lift rate be snr]   analyse the C++ simulator
//
// Prints one JSON object, the same fields compare.py reads from the Python.
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <algorithm>
#include <cstring>
#include <vector>

#include "wgdsp.h"
#include "wgsim.h"

static double now_ms() {
    using namespace std::chrono;
    return duration<double, std::milli>(steady_clock::now().time_since_epoch()).count();
}

static void num(const char* k, double v, bool comma = true) {
    if (std::isfinite(v)) printf("\"%s\": %.9g", k, v);
    else printf("\"%s\": null", k);
    if (comma) printf(", ");
}

int main(int argc, char** argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: wgcli FILE.f32 FS [lift] [forced_bph] | --sim SECONDS [...]\n");
        return 2;
    }
    std::vector<float> x;
    int fs = 48000;
    wg::Config cfg;
    if (!strcmp(argv[1], "--sim")) {
        double secs = atof(argv[2]);
        wg::SimParams p;
        if (argc > 3) p.bph = atoi(argv[3]);
        if (argc > 4) p.amplitude = atof(argv[4]);
        if (argc > 5) p.lift_angle = atof(argv[5]);
        if (argc > 6) p.rate_spd = atof(argv[6]);
        if (argc > 7) p.beat_error_ms = atof(argv[7]);
        if (argc > 8) p.snr_db = atof(argv[8]);
        cfg.lift_angle = p.lift_angle;
        wg::SimWatch sim(fs);
        sim.set_params(p);
        x.resize((size_t)(secs * fs));
        for (size_t o = 0; o < x.size(); o += 2400)
            sim.generate(x.data() + o, std::min<size_t>(2400, x.size() - o));
    } else {
        fs = atoi(argv[2]);
        if (argc > 3) cfg.lift_angle = atof(argv[3]);
        if (argc > 4) cfg.forced_bph = atoi(argv[4]);
        FILE* f = fopen(argv[1], "rb");
        if (!f) { perror(argv[1]); return 1; }
        fseek(f, 0, SEEK_END);
        long sz = ftell(f);
        fseek(f, 0, SEEK_SET);
        x.resize(sz / sizeof(float));
        if (fread(x.data(), sizeof(float), x.size(), f) != x.size()) { fclose(f); return 1; }
        fclose(f);
    }

    wg::Measurement m;
    const char* mode = getenv("WG_MODE");
    if (mode && !strcmp(mode, "stream")) {
        // Feed the audio through Stream in uneven blocks, as the device does
        // (analysis every ~0.5 s), and report the final window -- the whole
        // capture minus the filter margin. Timing: the average update +
        // analysis over the last half of the run, once the window is full.
        size_t win = x.size();
        wg::Stream st(win, nullptr, nullptr, now_ms);
        if (!st.ok()) { fprintf(stderr, "alloc failed\n"); return 1; }
        auto read = [&](float* dst, size_t n, uint64_t end) -> size_t {
            if (end > x.size() || n > end) return 0;
            memcpy(dst, x.data() + (end - n), n * sizeof(float));
            return n;
        };
        uint64_t avail = 0;
        unsigned seed = 1;
        double t_sum = 0;
        int t_n = 0;
        while (avail < x.size()) {
            seed = seed * 1103515245u + 12345u;
            size_t step = fs / 4 + (seed >> 16) % (fs / 2);   // 0.25-0.75 s of new audio
            avail = std::min<uint64_t>(x.size(), avail + step);
            double t0 = now_ms();
            st.update(avail, 0, fs, cfg, read);
            m = st.analyze(win, st.end(), cfg);
            if (avail > x.size() / 2) { t_sum += now_ms() - t0; ++t_n; }
        }
        m.t_total_ms = t_n ? (float)(t_sum / t_n) : 0.0f;
    } else {
        wg::Analyzer an(x.size(), nullptr, nullptr, now_ms);
        if (!an.ok()) { fprintf(stderr, "alloc failed\n"); return 1; }
        memcpy(an.input(), x.data(), x.size() * sizeof(float));
        m = an.analyze(x.size(), fs, cfg);
    }

    printf("{\"ok\": %s, ", m.ok ? "true" : "false");
    num("detected_bph", m.detected_bph);
    num("raw_bph", m.raw_bph);
    num("rate", m.rate);
    num("rate_ci", m.rate_ci);
    num("beat_error", m.beat_error);
    num("amplitude", m.amplitude);
    num("amplitude_spread", m.amplitude_spread);
    num("beats", m.beats);
    num("snr_db", m.snr_db);
    num("quality", m.quality);
    num("extra_peaks", m.extra_peaks);
    num("valid_frac", m.valid_frac);
    num("parity_correction", m.parity_correction);
    num("ms_total", m.t_total_ms);
    num("ms_filter", m.t_filter_ms);
    num("ms_period", m.t_period_ms);
    num("ms_refine", m.t_refine_ms);
    num("ms_impulse", m.t_impulse_ms);
    printf("\"message\": \"");
    for (char c : m.message) putchar(c == '"' ? '\'' : c);
    printf("\"}\n");
    return 0;
}
