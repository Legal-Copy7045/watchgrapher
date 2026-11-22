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

    wg::Analyzer an(x.size(), nullptr, nullptr, now_ms);
    if (!an.ok()) { fprintf(stderr, "alloc failed\n"); return 1; }
    memcpy(an.input(), x.data(), x.size() * sizeof(float));
    wg::Measurement m = an.analyze(x.size(), fs, cfg);

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
    printf("\"message\": \"");
    for (char c : m.message) putchar(c == '"' ? '\'' : c);
    printf("\"}\n");
    return 0;
}
