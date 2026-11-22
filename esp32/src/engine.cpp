#include "engine.h"

#include <LittleFS.h>
#include <Preferences.h>
#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <sys/stat.h>
#include <sys/time.h>

#include <algorithm>
#include <cmath>
#include <deque>
#include <vector>

#include "audio_in.h"
#include "dsps_dotprod.h"
#include "json.h"
#include "led.h"
#include "logx.h"
#include "settings.h"
#include "storage.h"
#include "wgdsp.h"

namespace engine {

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

enum class RunState { IDLE, SETTLE, MEASURE, DONE };
static const char* run_name(RunState s) {
    switch (s) {
        case RunState::SETTLE: return "settle";
        case RunState::MEASURE: return "measure";
        case RunState::DONE: return "done";
        default: return "idle";
    }
}

struct Run {
    RunState state = RunState::IDLE;
    uint32_t t0_ms = 0;         // when the run was started
    float settle_s = 0, duration_s = 30;
    uint64_t measure_start = 0; // frame the measurement phase began
    bool stop_requested = false;
    String position, wind, watch_id, caliber_key;
    // Per-window readings taken during the measurement phase, with the
    // length of audio each was taken over.
    std::vector<double> rate, ci, amp, be, len_s;
    int bph = 0;
    float lift = 52;
    std::string result;         // JSON object once DONE
};

struct PowerReserve {
    bool active = false;
    String id;
    uint32_t t0_ms = 0;
    uint32_t interval_s = 300;
    uint32_t next_ms = 0;
    int samples = 0, misses = 0;
    // ok readings since the last sample
    std::vector<double> rate, amp, be;
    double last[4] = {NAN, NAN, NAN, NAN};
    std::vector<float> spark;   // amplitude per sample, for the live sparkline
};

struct Hist { float t, rate, amp, be; };

static SemaphoreHandle_t mtx;
static wg::Stream* stream = nullptr;
static bool dot_simd = false;
static float cap_s = 12.0f;
static bool listening = false;
static Run run;
static PowerReserve pr;
static std::deque<Hist> hist;
static uint32_t seq = 0;
static std::string m_json = "null";   // last measurement
static float last_an_ms = 0;
static bool clock_set = false;

struct Lock {
    Lock() { xSemaphoreTake(mtx, portMAX_DELAY); }
    ~Lock() { xSemaphoreGive(mtx); }
};

static uint32_t now_ms() { return (uint32_t)(esp_timer_get_time() / 1000); }
static double clock_ms() { return esp_timer_get_time() / 1000.0; }

// ---------------------------------------------------------------------------
// Clock
// ---------------------------------------------------------------------------

void set_clock_ms(int64_t epoch_ms) {
    if (epoch_ms < 1600000000000LL) return;
    struct timeval tv;
    gettimeofday(&tv, nullptr);
    int64_t cur = (int64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
    if (clock_set && llabs(cur - epoch_ms) < 2000) return;
    tv.tv_sec = (time_t)(epoch_ms / 1000);
    tv.tv_usec = (suseconds_t)((epoch_ms % 1000) * 1000);
    settimeofday(&tv, nullptr);
    clock_set = true;
}

void set_tz_offset_min(int west) {
    static int cur = 1 << 30;
    if (west == cur || west < -900 || west > 900) return;
    cur = west;
    // POSIX TZ offsets have the same sign as getTimezoneOffset (west positive).
    char tz[24];
    snprintf(tz, sizeof tz, "LOC%c%d:%02d", west < 0 ? '-' : '+', abs(west) / 60, abs(west) % 60);
    setenv("TZ", tz, 1);
    tzset();
}

bool clock_valid() { return clock_set || time(nullptr) > 1600000000; }

String iso_now() {
    if (!clock_valid()) return "";
    time_t t = time(nullptr);
    struct tm tm;
    localtime_r(&t, &tm);
    char b[24];
    strftime(b, sizeof b, "%Y-%m-%dT%H:%M:%S", &tm);
    return b;
}

// ---------------------------------------------------------------------------
// Measurement -> JSON
// ---------------------------------------------------------------------------

static void measurement_json(const wg::Measurement& m, std::string& o) {
    using namespace json;
    o.clear();
    o.reserve(6000);
    o += '{';
    kv(o, "ok", m.ok);
    kv(o, "msg", m.message.c_str());
    kv(o, "bph", m.detected_bph);
    kv(o, "nominal", m.nominal_bph);
    kv(o, "raw_bph", m.raw_bph, 2);
    kv(o, "rate", m.rate, 2);
    kv(o, "ci", m.rate_ci, 2);
    kv(o, "be", m.beat_error, 2);
    kv(o, "amp", m.amplitude, 1);
    kv(o, "amp_iqr", m.amplitude_spread, 1);
    kv(o, "beats", m.beats);
    kv(o, "dur", m.duration, 2);
    kv(o, "snr", m.snr_db, 1);
    kv(o, "q", m.quality, 3);
    kv(o, "lift", m.lift_angle, 1);
    kv(o, "dt_ms", m.dt_mean * 1000.0, 3);
    kv(o, "par_ms", m.parity_correction, 2);
    kv(o, "par_seen_ms", m.parity_offset_seen, 2);
    kv(o, "extra", m.extra_peaks, 2);
    kv(o, "valid", m.valid_frac, 3);
    kv(o, "an_ms", m.t_total_ms, 0);
    kv(o, "t_upd", m.t_filter_ms, 0);
    kv(o, "t_per", m.t_period_ms, 0);
    kv(o, "t_ref", m.t_refine_ms, 0);
    kv(o, "t_imp", m.t_impulse_ms, 0);
    kv(o, "simd", dot_simd);

    // Trace: deviation of each beat from a perfect grid at the nominal rate,
    // centred on the first few beats (analysis.trace_points, unwrapped -- the
    // browser wraps it into whatever window it is drawing).
    if (m.ok && m.times.size() >= 4 && m.nominal_bph > 0) {
        const double np = 3600.0 / m.nominal_bph;
        const size_t n = m.times.size();
        std::vector<double> dev(n);
        for (size_t i = 0; i < n; ++i)
            dev[i] = ((m.times[i] - m.times[0]) - m.index[i] * np) * 1000.0;
        size_t k = std::max<size_t>(4, n / 20);
        std::vector<double> head(dev.begin(), dev.begin() + std::min(k, n));
        std::sort(head.begin(), head.end());
        double c = head.size() % 2 ? head[head.size() / 2]
                                   : 0.5 * (head[head.size() / 2 - 1] + head[head.size() / 2]);
        o += "\"trace\":[";
        for (size_t i = 0; i < n; ++i) {
            if (i) o += ',';
            o += '[';
            num(o, dev[i] - c, 3);
            o += ',';
            num(o, m.times[i] - m.times[0], 3);
            o += ',';
            o += ((long long)m.index[i] % 2) ? '1' : '0';
            o += ']';
        }
        o += "],";
    }

    // Averaged beat envelope, max-pooled to at most 320 points.
    if (!m.mean_shape.empty() && m.shape_fs > 0) {
        const size_t L = m.mean_shape.size();
        const size_t step = std::max<size_t>(1, (L + 319) / 320);
        o += "\"shape\":[";
        for (size_t i = 0; i < L; i += step) {
            float v = 0;
            for (size_t j = i; j < std::min(L, i + step); ++j) v = std::max(v, m.mean_shape[j]);
            if (i) o += ',';
            num(o, v, 3);
        }
        o += "],";
        const double ms = 1000.0 / m.shape_fs;
        kv(o, "shape_ms", L * ms, 2);
        kv(o, "shape_pre_ms", m.shape_pre * ms, 2);
        kv(o, "p1_ms", m.p1_idx * ms, 2);
        kv(o, "p3_ms", m.p3_idx * ms, 2);
    }

    // Instantaneous rate, thinned to at most 160 points.
    if (!m.inst_rate.empty()) {
        const size_t n = m.inst_rate.size();
        const size_t step = std::max<size_t>(1, (n + 159) / 160);
        o += "\"inst\":[";
        for (size_t i = 0; i < n; i += step) {
            if (i) o += ',';
            o += '[';
            num(o, m.inst_rate_t[i], 2);
            o += ',';
            num(o, m.inst_rate[i], 1);
            o += ']';
        }
        o += "],";
    }
    close_obj(o);
}

// ---------------------------------------------------------------------------
// Timed runs
// ---------------------------------------------------------------------------

static double mean(const std::vector<double>& v) {
    double s = 0;
    for (double x : v) s += x;
    return v.empty() ? NAN : s / v.size();
}
static double median(std::vector<double> v) {
    if (v.empty()) return NAN;
    std::sort(v.begin(), v.end());
    size_t h = v.size() / 2;
    return v.size() % 2 ? v[h] : 0.5 * (v[h - 1] + v[h]);
}

// A run no longer than the analysis window is reported from the one reading
// over the whole run -- what the desktop does. A longer run uses only the
// full-length windows (the early, short ones are the noisiest); those overlap,
// so they are not independent: the rate is their mean and the confidence
// interval is scaled by the number of NON-overlapping windows the run spans.
static void finish_run(double window_s) {
    using namespace json;
    Run& r = run;
    if (!r.rate.empty()) {
        double longest = *std::max_element(r.len_s.begin(), r.len_s.end());
        double want = std::min(longest, std::min<double>(window_s, r.duration_s)) * 0.95;
        Run kept = Run();
        for (size_t i = 0; i < r.rate.size(); ++i) {
            if (r.len_s[i] < want) continue;
            kept.rate.push_back(r.rate[i]);
            kept.ci.push_back(r.ci[i]);
            kept.amp.push_back(r.amp[i]);
            kept.be.push_back(r.be[i]);
            kept.len_s.push_back(r.len_s[i]);
        }
        r.rate.swap(kept.rate);
        r.ci.swap(kept.ci);
        r.amp.swap(kept.amp);
        r.be.swap(kept.be);
        r.len_s.swap(kept.len_s);
    }
    std::string& o = r.result;
    o = "{";
    kv(o, "position", r.position.c_str());
    kv(o, "wind", r.wind.c_str());
    kv(o, "watch_id", r.watch_id.c_str());
    kv(o, "caliber_key", r.caliber_key.c_str());
    kv(o, "when", iso_now().c_str());
    kv(o, "lift_angle", r.lift, 1);
    kv(o, "bph", r.bph);
    kv(o, "windows", (int)r.rate.size());
    double measured = (audio::total() - r.measure_start) / (double)audio::sample_rate();
    kv(o, "seconds", std::min<double>(measured, r.duration_s), 1);
    if (r.rate.empty()) {
        kv(o, "ok", false);
        kv(o, "msg", "No usable reading during the run. Check the pickup and try again.");
    } else {
        double win = std::min<double>(window_s, r.duration_s);
        double n_ind = std::max(1.0, std::min<double>(measured, r.duration_s) / std::max(1.0, win));
        double mr = mean(r.rate);
        double sd = 0, ci2 = 0;
        for (size_t i = 0; i < r.rate.size(); ++i) {
            sd += (r.rate[i] - mr) * (r.rate[i] - mr);
            ci2 += std::isfinite(r.ci[i]) ? r.ci[i] * r.ci[i] : 0;
        }
        sd = r.rate.size() > 1 ? std::sqrt(sd / (r.rate.size() - 1)) : 0;
        double ci = std::max(std::sqrt(ci2 / r.rate.size()), 1.96 * sd) / std::sqrt(n_ind);
        std::vector<double> amps;
        for (double a : r.amp)
            if (std::isfinite(a)) amps.push_back(a);
        std::vector<double> bes;
        for (double b : r.be)
            if (std::isfinite(b)) bes.push_back(b);
        kv(o, "ok", true);
        kv(o, "rate", mr, 2);
        kv(o, "rate_ci", ci, 2);
        kv(o, "amplitude", median(amps), 1);
        kv(o, "beat_error", median(bes), 2);
        kv(o, "msg", amps.empty() ? "Amplitude unavailable -- the unlocking noise was not resolved."
                                  : "OK");
    }
    close_obj(o);
    r.state = RunState::DONE;
}

// ---------------------------------------------------------------------------
// Power reserve
// ---------------------------------------------------------------------------

static String pr_path(const String& id, const char* ext) { return "/pr/" + id + ext; }

static void pr_end(const char* reason) {
    if (!pr.active) return;
    File f = LittleFS.open(pr_path(pr.id, ".end"), "w");
    if (f) {
        std::string o = "{";
        json::kv(o, "reason", reason);
        json::kv(o, "ended", iso_now().c_str());
        json::kv(o, "hours", (now_ms() - pr.t0_ms) / 3.6e6, 3);
        json::kv(o, "samples", pr.samples);
        json::close_obj(o);
        f.print(o.c_str());
        f.close();
    }
    Preferences p;
    p.begin("wg", false);
    p.remove("pr_live");
    p.end();
    pr.active = false;
}

static void pr_sample(uint32_t t) {
    pr.next_ms += pr.interval_s * 1000;
    if (pr.rate.empty()) {
        // No usable reading for a whole interval. Three in a row after the run
        // has produced samples means the watch has stopped.
        if (++pr.misses >= 3 && pr.samples > 0) pr_end("stopped");
        return;
    }
    pr.misses = 0;
    double el = (t - pr.t0_ms) / 1000.0;
    // Late in a run many readings have rate but no amplitude: NaN must not
    // reach the median (sorting NaN is undefined) exactly where the 200 and
    // 135 degree crossings are.
    auto finite_median = [](const std::vector<double>& v) {
        std::vector<double> f;
        for (double x : v)
            if (std::isfinite(x)) f.push_back(x);
        return median(f);
    };
    double v[4] = {el, finite_median(pr.rate), finite_median(pr.amp), finite_median(pr.be)};
    pr.rate.clear();
    pr.amp.clear();
    pr.be.clear();
    File f = LittleFS.open(pr_path(pr.id, ".csv"), "a");
    if (f) {
        std::string line;
        for (int i = 0; i < 4; ++i) {
            if (i) line += ',';
            if (std::isfinite(v[i])) json::num(line, v[i], i == 0 ? 0 : 3);
        }
        line += '\n';
        f.print(line.c_str());
        f.close();
    }
    memcpy(pr.last, v, sizeof v);
    pr.samples++;
    pr.spark.push_back(std::isfinite(v[2]) ? (float)v[2] : NAN);
    if (pr.spark.size() > 240) pr.spark.erase(pr.spark.begin());
}

void storage_ready() {
    LittleFS.mkdir("/pr");
    recover_interrupted_pr();
}

void recover_interrupted_pr() {
    Preferences p;
    p.begin("wg", true);
    String id = p.isKey("pr_live") ? p.getString("pr_live", "") : String();
    p.end();
    if (id.isEmpty()) return;
    struct stat st;
    if (::stat(("/littlefs" + pr_path(id, ".end")).c_str(), &st) != 0) {
        // How far it got: the elapsed time of the last sample written.
        double hours = NAN;
        int samples = 0;
        File c = ::stat(("/littlefs" + pr_path(id, ".csv")).c_str(), &st) == 0
                     ? LittleFS.open(pr_path(id, ".csv"), "r") : File();
        if (c) {
            while (c.available()) {
                String line = c.readStringUntil('\n');
                if (line.length() < 2) continue;
                ++samples;
                hours = line.toFloat() / 3600.0;
            }
            c.close();
        }
        File f = LittleFS.open(pr_path(id, ".end"), "w");
        if (f) {
            std::string o = "{";
            json::kv(o, "reason", "interrupted (power lost)");
            json::kv(o, "ended", "");
            json::kv(o, "hours", hours, 3);
            json::kv(o, "samples", samples);
            json::close_obj(o);
            f.print(o.c_str());
            f.close();
        }
    }
    p.begin("wg", false);
    p.remove("pr_live");
    p.end();
}

// ---------------------------------------------------------------------------
// The analysis loop
// ---------------------------------------------------------------------------

static void task(void*) {
    for (;;) {
        bool want;
        {
            Lock l;
            want = listening || pr.active || run.state == RunState::SETTLE ||
                   run.state == RunState::MEASURE;
        }
        audio::set_capturing(want);
        if (!want) {
            led::set(led::IDLE);
            vTaskDelay(pdMS_TO_TICKS(200));
            continue;
        }
        const uint32_t t_loop = now_ms();
        wg::Config cfg;
        float window_s;
        {
            SettingsLock sl;
            cfg = g_settings.cfg;
            window_s = std::min(g_settings.window_s, cap_s);
        }
        // Filter and envelope only the audio that arrived since last time;
        // the stream keeps the rest. A new epoch (pause, source switch, rate
        // change) or new filter settings make it start over from the ring.
        const uint64_t epoch = audio::epoch();
        const double tu = clock_ms();
        stream->update(audio::total(), epoch, audio::sample_rate(), cfg,
                       [](float* d, size_t n, uint64_t e) { return audio::copy_window(d, n, e); });
        const float upd_ms = (float)(clock_ms() - tu);
        const int fs = stream->fs() > 0 ? stream->fs() : audio::sample_rate();
        // Processed audio is final up to here (a filter margin behind the newest).
        uint64_t end = stream->end();
        const uint64_t s0 = std::max(epoch, stream->start());
        size_t n = (size_t)std::min<uint64_t>((uint64_t)(window_s * fs), end - std::min(end, s0));
        bool final_window = false;

        {
            Lock l;
            if (run.state == RunState::SETTLE && (t_loop - run.t0_ms) >= run.settle_s * 1000) {
                run.state = RunState::MEASURE;
                run.measure_start = end;
            }
            if (run.state == RunState::MEASURE) {
                if (run.measure_start < epoch) run.measure_start = epoch;   // stream restarted
                uint64_t target = run.measure_start + (uint64_t)(run.duration_s * fs);
                if (end >= target) {
                    end = target;
                    final_window = true;
                }
                if (run.stop_requested) final_window = true;
                n = std::min<uint64_t>(n, end - std::min(end, run.measure_start));
            }
        }

        if (n < (size_t)(3 * fs)) {
            if (final_window) {
                Lock l;
                finish_run(window_s);
            }
            if (!audio::input_ready()) led::set(led::NOSIGNAL);
            vTaskDelay(pdMS_TO_TICKS(250));
            continue;
        }

        wg::Measurement m = stream->analyze(n, end, cfg);
        m.t_filter_ms = upd_ms;
        m.t_total_ms += upd_ms;
        std::string js;
        measurement_json(m, js);

        {
            Lock l;
            m_json.swap(js);
            ++seq;
            last_an_ms = m.t_total_ms;
            if (m.ok) hist.push_back({t_loop / 1000.0f, (float)m.rate, (float)m.amplitude,
                                      (float)m.beat_error});
            else hist.push_back({t_loop / 1000.0f, NAN, NAN, NAN});
            if (hist.size() > 900) hist.pop_front();

            if (run.state == RunState::MEASURE) {
                if (m.ok) {
                    run.rate.push_back(m.rate);
                    run.ci.push_back(m.rate_ci);
                    run.amp.push_back(m.amplitude);
                    run.be.push_back(m.beat_error);
                    run.len_s.push_back((double)n / fs);
                    run.bph = m.nominal_bph;
                }
                if (final_window) finish_run(window_s);
            }
            if (pr.active) {
                if (m.ok) {
                    pr.rate.push_back(m.rate);
                    pr.amp.push_back(m.amplitude);
                    pr.be.push_back(m.beat_error);
                }
                uint32_t t = now_ms();
                if ((int32_t)(t - pr.next_ms) >= 0) pr_sample(t);
            }
        }

        if (audio::clips_recent() > 0) led::set(led::CLIP);
        else if (m.ok && std::isfinite(m.amplitude)) led::set(led::GOOD);
        else if (m.ok) led::set(led::WEAK);
        else led::set(led::NOSIGNAL);

        // Pace: at most ~2 analyses a second, and always leave the web server
        // and Wi-Fi some air.
        uint32_t spent = now_ms() - t_loop;
        vTaskDelay(pdMS_TO_TICKS(spent < 500 ? 500 - spent : 20));
    }
}

static float dot_dsp(const float* a, const float* b, int n) {
    if (n <= 0) return 0.0f;
    float r = 0.0f;
    dsps_dotprod_f32(a, b, &r, n);
    return r;
}

static bool dot_selftest() {
    std::vector<float> va(512), vb(512);
    float* a = va.data();
    float* b = vb.data();
    uint32_t x = 12345;
    for (int i = 0; i < 512; ++i) {
        x = x * 1103515245u + 12345u;
        a[i] = ((x >> 8) & 0xFFFF) / 32768.0f - 1.0f;
        x = x * 1103515245u + 12345u;
        b[i] = ((x >> 8) & 0xFFFF) / 32768.0f - 1.0f;
    }
    for (int oa = 0; oa < 4; ++oa)
        for (int ob = 0; ob < 4; ++ob)
            for (int len : {1, 2, 3, 4, 5, 7, 8, 15, 16, 17, 31, 64, 129, 255, 400}) {
                double ref = 0;
                for (int i = 0; i < len; ++i) ref += (double)a[oa + i] * b[ob + i];
                float got = dot_dsp(a + oa, b + ob, len);
                if (std::fabs(got - ref) > 1e-4 * (1.0 + std::fabs(ref))) return false;
            }
    return true;
}

bool begin(float window_cap) {
    mtx = xSemaphoreCreateMutex();
    cap_s = window_cap;
    const size_t samples = (size_t)(cap_s * g_settings.sample_rate);
    stream = new wg::Stream(
        samples, [](size_t b) { return heap_caps_malloc(b, MALLOC_CAP_SPIRAM); },
        [](void* p) { heap_caps_free(p); }, clock_ms,
        // Hot scratch (the period search's decimated envelope) in internal RAM if it fits.
        [](size_t b) { return heap_caps_malloc(b, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT); },
        [](void* p) { heap_caps_free(p); });
    if (!stream->ok()) return false;
    // The S3's vector unit for every correlation -- after checking it agrees
    // with a plain loop on unaligned, odd-length input, as the analysis uses it.
    dot_simd = dot_selftest();
    wg::set_dot(dot_simd ? dot_dsp : nullptr);
    logx::printf("Correlations: %s\n", dot_simd ? "esp-dsp SIMD" : "plain C (SIMD self-test failed)");
    // Core 1, same priority as the Arduino loop (web server): they time-slice,
    // so a long analysis never stalls a page load.
    xTaskCreatePinnedToCore(task, "analysis", 16384, nullptr, 1, nullptr, 1);
    return true;
}

float window_cap_s() { return cap_s; }

// ---------------------------------------------------------------------------
// Commands and state
// ---------------------------------------------------------------------------

static std::string ok_reply(const char* msg = "ok") {
    std::string o = "{";
    json::kv(o, "ok", true);
    json::kv(o, "msg", msg);
    json::close_obj(o);
    return o;
}
static std::string err_reply(const char* msg) {
    std::string o = "{";
    json::kv(o, "ok", false);
    json::kv(o, "msg", msg);
    json::close_obj(o);
    return o;
}

std::string command(const String& action, const ArgFn& arg) {
    Lock l;
    if (action == "listen") {
        listening = arg("on") != "0";
        return ok_reply();
    }
    if (action == "run_start") {
        if (run.state == RunState::SETTLE || run.state == RunState::MEASURE)
            return err_reply("A run is already in progress.");
        run = Run();
        run.state = RunState::SETTLE;
        run.t0_ms = now_ms();
        run.settle_s = std::max(0.0f, std::min(120.0f, arg("settle").toFloat()));
        run.duration_s = std::max(6.0f, std::min(3600.0f, arg("duration").toFloat()));
        run.position = arg("position");
        run.wind = arg("wind");
        run.watch_id = arg("watch_id");
        run.caliber_key = arg("caliber_key");
        {
            SettingsLock sl;
            run.lift = g_settings.cfg.lift_angle;
        }
        return ok_reply();
    }
    if (action == "run_stop") {
        if (run.state == RunState::SETTLE) run = Run();
        else if (run.state == RunState::MEASURE) run.stop_requested = true;
        return ok_reply();
    }
    if (action == "run_clear") {
        if (run.state == RunState::DONE) run = Run();
        return ok_reply();
    }
    if (action == "pr_start") {
        if (pr.active) return err_reply("A power-reserve log is already running.");
        if (!storage::ready())
            return err_reply("Storage is still being prepared (first start after installing) -- try again in a minute.");
        pr = PowerReserve();
        String iso = iso_now();
        if (clock_valid()) {
            char b[20];
            time_t t = time(nullptr);
            struct tm tm;
            localtime_r(&t, &tm);
            strftime(b, sizeof b, "%Y%m%d-%H%M%S", &tm);
            pr.id = b;
        } else {
            pr.id = "run-" + String((uint32_t)esp_random(), HEX);
        }
        pr.interval_s = (uint32_t)std::max(30L, std::min(3600L, arg("interval").toInt()));
        pr.t0_ms = now_ms();
        // First sample one interval in, so it is a median over real readings.
        pr.next_ms = pr.t0_ms + pr.interval_s * 1000;
        File f = LittleFS.open(pr_path(pr.id, ".json"), "w");
        if (!f) return err_reply("Could not create the log file.");
        std::string o = "{";
        json::kv(o, "id", pr.id.c_str());
        json::kv(o, "when", iso.c_str());
        json::kv(o, "watch_id", arg("watch_id").c_str());
        json::kv(o, "caliber_key", arg("caliber_key").c_str());
        float lift;
        {
            SettingsLock sl;
            lift = g_settings.cfg.lift_angle;
        }
        json::kv(o, "lift_angle", lift, 1);
        json::kv(o, "interval_s", (int)pr.interval_s);
        json::kv(o, "target_h", arg("target_h").toFloat(), 1);
        json::kv(o, "notes", arg("notes").c_str());
        json::close_obj(o);
        f.print(o.c_str());
        f.close();
        pr.active = true;
        Preferences p;
        p.begin("wg", false);
        p.putString("pr_live", pr.id);
        p.end();
        return ok_reply();
    }
    if (action == "pr_stop") {
        pr_end("stopped by user");
        return ok_reply();
    }
    if (action == "pr_delete") {
        String id = arg("id");
        if (id.isEmpty() || id.indexOf('/') >= 0 || id.indexOf("..") >= 0)
            return err_reply("Bad id.");
        if (pr.active && pr.id == id) return err_reply("Stop the log first.");
        if (!storage::ready()) return err_reply("Storage is still being prepared.");
        for (const char* e : {".json", ".csv", ".end"}) LittleFS.remove(pr_path(id, e));
        return ok_reply();
    }
    if (action == "history_clear") {
        hist.clear();
        return ok_reply();
    }
    return err_reply("Unknown action.");
}

void state_json(uint32_t since, std::string& o) {
    using namespace json;
    Lock l;
    const uint32_t t = now_ms();
    const int fs = audio::sample_rate();
    o.clear();
    o.reserve(m_json.size() + 1024);
    o += '{';
    kv(o, "seq", (long long)seq);
    kv(o, "up", t / 1000.0, 0);
    kv(o, "listening", listening);
    kv(o, "capturing", audio::capturing());
    const Source src = g_settings.source;
    kv(o, "src", src == Source::SIM ? "sim" : src == Source::USB ? "usb" : "i2s");
    kv(o, "fs", fs);
    kv(o, "level", audio::level_db(), 1);
    kv(o, "gain", audio::gain_db(), 1);
    kv(o, "agc", g_settings.agc);
    kv(o, "clips", (long long)audio::clips_recent());
    kv(o, "window", std::min(g_settings.window_s, cap_s), 1);
    kv(o, "window_cap", cap_s, 1);
    kv(o, "an_ms", last_an_ms, 0);
    kv(o, "clock", clock_valid());
    kv(o, "storage", storage::ready());
    kv(o, "coll_rev", (long long)storage::coll_rev());
    kv(o, "input", audio::status().c_str());
    kv(o, "input_ready", audio::input_ready());

    o += "\"run\":{";
    kv(o, "state", run_name(run.state));
    if (run.state != RunState::IDLE) {
        kv(o, "elapsed", (t - run.t0_ms) / 1000.0, 1);
        kv(o, "settle", run.settle_s, 0);
        kv(o, "duration", run.duration_s, 0);
        kv(o, "position", run.position.c_str());
        kv(o, "wind", run.wind.c_str());
        kv(o, "watch_id", run.watch_id.c_str());
        kv(o, "windows", (int)run.rate.size());
        if (run.state == RunState::MEASURE)
            kv(o, "measured", (audio::total() - run.measure_start) / (double)fs, 1);
        if (run.state == RunState::DONE) {
            o += "\"result\":";
            o += run.result;
            o += ',';
        }
    }
    close_obj(o);
    o += ',';

    o += "\"pr\":{";
    kv(o, "active", pr.active);
    if (pr.active) {
        kv(o, "id", pr.id.c_str());
        kv(o, "elapsed", (t - pr.t0_ms) / 1000.0, 0);
        kv(o, "interval", (int)pr.interval_s);
        kv(o, "samples", pr.samples);
        kv(o, "next", ((int32_t)(pr.next_ms - t)) / 1000.0, 0);
        o += "\"last\":[";
        for (int i = 0; i < 4; ++i) {
            if (i) o += ',';
            num(o, pr.last[i], 2);
        }
        o += "],";
        // Sample number of the sparkline's first point (it keeps the last 240).
        kv(o, "spark0", pr.samples - (int)pr.spark.size());
        o += "\"spark\":[";
        for (size_t i = 0; i < pr.spark.size(); ++i) {
            if (i) o += ',';
            num(o, pr.spark[i], 1);
        }
        o += "],";
    }
    close_obj(o);
    o += ',';

    if (since != seq) {
        o += "\"m\":";
        o += m_json;
        o += ',';
    }
    close_obj(o);
}

void history_json(std::string& o) {
    using namespace json;
    Lock l;
    o = "[";
    bool first = true;
    for (const Hist& h : hist) {
        if (!first) o += ',';
        first = false;
        o += '[';
        num(o, h.t, 1);
        o += ',';
        num(o, h.rate, 2);
        o += ',';
        num(o, h.amp, 1);
        o += ',';
        num(o, h.be, 2);
        o += ']';
    }
    o += ']';
}

void pr_list_json(std::string& o) {
    String live;
    {
        Lock l;
        if (pr.active) live = pr.id;
    }
    o = "[";
    if (!storage::ready()) { o += ']'; return; }
    File dir = LittleFS.open("/pr");
    bool first = true;
    if (dir && dir.isDirectory()) {
        for (File f = dir.openNextFile(); f; f = dir.openNextFile()) {
            String name = f.name();
            int slash = name.lastIndexOf('/');
            if (slash >= 0) name = name.substring(slash + 1);
            if (!name.endsWith(".json")) continue;
            String id = name.substring(0, name.length() - 5);
            // Pasted in verbatim, so a file cut short by a power loss must not
            // make the whole list invalid JSON: anything that is not one
            // closed object becomes null.
            auto object_or_null = [](String v) {
                v.trim();
                return (v.length() >= 2 && v[0] == '{' && v[v.length() - 1] == '}') ? v : String("null");
            };
            String meta = object_or_null(f.readString());
            f.close();
            String end = "null";
            struct stat st;
            File e = ::stat(("/littlefs" + pr_path(id, ".end")).c_str(), &st) == 0
                         ? LittleFS.open(pr_path(id, ".end"), "r") : File();
            if (e) { end = object_or_null(e.readString()); e.close(); }
            size_t bytes = 0;
            if (::stat(("/littlefs" + pr_path(id, ".csv")).c_str(), &st) == 0) bytes = st.st_size;
            if (!first) o += ',';
            first = false;
            o += "{\"meta\":";
            o += meta.c_str();
            o += ",\"end\":";
            o += end.c_str();
            o += ",\"active\":";
            o += (live.length() && live == id) ? "true" : "false";
            o += ",\"csv_bytes\":";
            o += std::to_string(bytes);
            o += '}';
        }
    }
    o += ']';
}

}  // namespace engine
