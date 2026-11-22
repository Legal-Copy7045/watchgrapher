#include "audio_in.h"

#include <driver/i2s.h>
#include <esp_heap_caps.h>

#include <algorithm>
#include <atomic>
#include <cmath>

#include "usb_audio.h"
#include "wgsim.h"

namespace audio {

static int16_t* ring = nullptr;
static size_t ring_n = 0;
static std::atomic<uint64_t> written{0};
// First frame of the current unbroken stream. Anything before it belongs to
// an earlier stream (before a pause, a source switch or a rate change), and
// must never be analysed together with what follows -- a gap that is not a
// whole number of beats makes nonsense of the rate fit.
static std::atomic<uint64_t> epoch_{0};
static std::atomic<bool> running{false};
static std::atomic<int> fs_{48000};
static int fs_boot = 48000;
static std::atomic<Source> src{Source::I2S};
static std::atomic<bool> i2s_ok{false};
static String err;
static portMUX_TYPE err_mux = portMUX_INITIALIZER_UNLOCKED;

static std::atomic<float> level{-120.0f};
static std::atomic<float> gain_lin{8.0f};
static std::atomic<float> cur_gain_db{18.0f};
static std::atomic<uint32_t> clips{0};
static std::atomic<uint32_t> clips_2s{0};

static wg::SimWatch* sim = nullptr;
static std::atomic<bool> sim_dirty{false};
static std::atomic<bool> restart_stream{false};

static constexpr size_t BLOCK = 480;   // 10 ms at 48 kHz

static void set_err(const String& e) {
    portENTER_CRITICAL(&err_mux);
    err = e;
    portEXIT_CRITICAL(&err_mux);
}

static bool i2s_start(const Settings& s) {
    i2s_config_t c = {};
    c.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
    c.sample_rate = (uint32_t)s.sample_rate;
    c.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
    c.channel_format = s.i2s_right ? I2S_CHANNEL_FMT_ONLY_RIGHT : I2S_CHANNEL_FMT_ONLY_LEFT;
    c.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    c.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    c.dma_buf_count = 8;
    c.dma_buf_len = 512;
    c.use_apll = false;
    c.tx_desc_auto_clear = false;
    c.fixed_mclk = 0;
    c.mclk_multiple = I2S_MCLK_MULTIPLE_256;
    c.bits_per_chan = I2S_BITS_PER_CHAN_32BIT;
    esp_err_t e = i2s_driver_install(I2S_NUM_0, &c, 0, nullptr);
    if (e != ESP_OK) {
        set_err(String("I2S driver install failed: ") + esp_err_to_name(e));
        return false;
    }
    i2s_pin_config_t p = {};
    p.mck_io_num = s.pin_mclk >= 0 ? s.pin_mclk : I2S_PIN_NO_CHANGE;
    p.bck_io_num = s.pin_bclk;
    p.ws_io_num = s.pin_ws;
    p.data_out_num = I2S_PIN_NO_CHANGE;
    p.data_in_num = s.pin_din;
    e = i2s_set_pin(I2S_NUM_0, &p);
    if (e != ESP_OK) {
        set_err(String("I2S pin setup failed: ") + esp_err_to_name(e));
        i2s_driver_uninstall(I2S_NUM_0);
        return false;
    }
    return true;
}

// ---- writing into the ring --------------------------------------------------
// Only ever called from one context at a time: the audio task for I2S and the
// simulator, the USB client task for a USB mic (the audio task idles then).

static float dc_x = 0, dc_y = 0;
static float pk_win = -120.0f;
static uint32_t t_win = 0, clips_win = 0;

static void new_stream() {
    epoch_.store(written.load());
    dc_x = dc_y = 0;
}

static void agc_step(float peak_db_2s);

static void push(const float* x, size_t n, float g, bool dc_block) {
    if (!ring) return;
    if (restart_stream.exchange(false)) new_stream();
    const uint64_t w = written.load(std::memory_order_relaxed);
    size_t pos = (size_t)(w % ring_n);   // one 64-bit modulo per block, not per sample
    const float dc_r = 1.0f - 2.0f * (float)M_PI * 20.0f / fs_;   // ~20 Hz DC blocker
    float pk = 0.0f;
    uint32_t c = 0;
    for (size_t i = 0; i < n; ++i) {
        float v = x[i];
        if (dc_block) {
            dc_y = v - dc_x + dc_r * dc_y;
            dc_x = v;
            v = dc_y;
        }
        v *= g;
        float a = fabsf(v);
        if (a > pk) pk = a;
        if (a >= 0.999f) { ++c; v = v > 0 ? 0.999f : -0.999f; }
        ring[pos] = (int16_t)lrintf(v * 32767.0f);
        if (++pos == ring_n) pos = 0;
    }
    written.store(w + n, std::memory_order_release);
    if (c) clips += c;

    // Meter: fast attack, slow release.
    float db = pk > 1e-6f ? 20.0f * log10f(pk) : -120.0f;
    float l = level;
    level = db > l ? db : l * 0.97f + db * 0.03f;

    pk_win = fmaxf(pk_win, level);
    uint32_t now = millis();
    if (now - t_win >= 2000) {
        uint32_t cc = clips.load();
        clips_2s = cc - clips_win;
        clips_win = cc;
        if (src != Source::SIM) agc_step(pk_win);
        pk_win = -120.0f;
        t_win = now;
    }
}

// Automatic makeup gain: hold the peak near -8 dBFS, changing at most a few
// dB every 2 s. Timing comes from WHEN the noises occur, so a gentle gain
// change inside the analysis window is harmless; clipping is not.
static void agc_step(float peak_db_2s) {
    if (!g_settings.agc) return;
    float g = cur_gain_db;
    if (peak_db_2s > -2.0f) g -= 3.0f;
    else if (peak_db_2s > -5.0f) g -= 1.0f;
    else if (peak_db_2s < -14.0f) g += 2.0f;
    else if (peak_db_2s < -10.0f) g += 1.0f;
    g = fminf(54.0f, fmaxf(0.0f, g));
    cur_gain_db = g;
    gain_lin = powf(10.0f, g / 20.0f);
}

// USB microphone samples arrive on the USB client task.
static void usb_sink(const float* x, size_t n, uint32_t rate) {
    if (!running || src != Source::USB) return;
    if ((int)rate != fs_) {
        // The mic runs at a different (lower) rate than the analysis was set
        // up for: follow it. Lower is always safe -- the buffers were sized in
        // samples for the higher rate, so they simply hold more seconds.
        fs_ = (int)rate;
        restart_stream = true;
    }
    push(x, n, gain_lin, true);
}

static void task(void*) {
    static int32_t raw[BLOCK];
    static float f[BLOCK * 2];
    TickType_t last = xTaskGetTickCount();
    bool was_running = false;

    for (;;) {
        const bool run = running;
        if (run && !was_running) restart_stream = true;   // a fresh stream after every pause
        was_running = run;
        const Source s = src;
        if (!run || s == Source::USB) {
            vTaskDelay(pdMS_TO_TICKS(20));
            last = xTaskGetTickCount();
            continue;
        }
        if (s == Source::SIM) {
            if (fs_ != fs_boot) { fs_ = fs_boot; restart_stream = true; }
            if (sim_dirty.exchange(false)) {
                wg::SimParams sp;
                {
                    SettingsLock lock;
                    sp = g_settings.sim;
                }
                sim->set_params(sp);
            }
            // Exactly 10 ms per block at any rate, so the simulator keeps real time.
            const size_t nb = std::min<size_t>(BLOCK * 2, (size_t)fs_boot / 100);
            sim->generate(f, nb);
            push(f, nb, 1.0f, false);   // the simulator is already at a sensible level
            vTaskDelayUntil(&last, std::max<TickType_t>(1, pdMS_TO_TICKS(10)));
        } else {
            if (!i2s_ok) { vTaskDelay(pdMS_TO_TICKS(100)); continue; }
            if (fs_ != fs_boot) { fs_ = fs_boot; restart_stream = true; }
            size_t got = 0;
            i2s_read(I2S_NUM_0, raw, sizeof raw, &got, pdMS_TO_TICKS(200));
            size_t n = got / sizeof(int32_t);
            for (size_t i = 0; i < n; ++i) f[i] = (float)raw[i] * (1.0f / 2147483648.0f);   // MSB-aligned
            if (n) push(f, n, gain_lin, true);
        }
    }
}

bool begin(float seconds) {
    const Settings& s = g_settings;
    fs_boot = s.sample_rate;
    fs_ = fs_boot;
    src = s.source;
    if (seconds <= 0) {
        set_err("Not enough PSRAM. WatchGrapher needs an ESP32-S3 with 8 MB of PSRAM.");
        return false;
    }
    ring_n = (size_t)(seconds * fs_boot);
    ring = (int16_t*)heap_caps_malloc(ring_n * sizeof(int16_t), MALLOC_CAP_SPIRAM);
    if (!ring) {
        set_err("Could not allocate the audio buffer in PSRAM.");
        return false;
    }
    sim = new wg::SimWatch(fs_boot);
    sim->set_params(s.sim);
    cur_gain_db = s.gain_db;
    gain_lin = powf(10.0f, s.gain_db / 20.0f);
    t_win = millis();
    if (src == Source::I2S) i2s_ok = i2s_start(s);
    if (src == Source::USB) usbmic::begin(fs_boot, 32000, usb_sink);
    xTaskCreatePinnedToCore(task, "audio", 6144, nullptr, 10, nullptr, 0);
    return src != Source::I2S || i2s_ok;
}

void set_capturing(bool on) { running = on; }
bool capturing() { return running; }
uint64_t total() { return written.load(std::memory_order_acquire); }
uint64_t epoch() { return epoch_.load(); }
int sample_rate() { return fs_; }
float ring_seconds() { return ring_n ? (float)ring_n / fs_ : 0.0f; }

size_t copy_window(float* dst, size_t n, uint64_t end) {
    if (!ring) return 0;
    uint64_t w = total();
    if (end > w) end = w;
    // The oldest frame still safe to read is measured from the WRITER, not
    // from `end`: a timed run's final window ends at its target frame while
    // the writer has carried on, and half a second is kept clear of it.
    const uint64_t keep = ring_n - std::min<size_t>(ring_n, (size_t)fs_ / 2);
    const uint64_t oldest = w > keep ? w - keep : 0;
    if (end < oldest) return 0;
    if (end - n < oldest) n = (size_t)(end - oldest);
    size_t pos = (size_t)((end - n) % ring_n);
    const float k = 1.0f / 32768.0f;
    size_t first = std::min(n, ring_n - pos);
    for (size_t i = 0; i < first; ++i) dst[i] = ring[pos + i] * k;
    for (size_t i = first; i < n; ++i) dst[i] = ring[i - first] * k;
    return n;
}

float level_db() { return level; }
float gain_db() { return cur_gain_db; }
uint32_t clips_recent() { return clips_2s; }

String status() {
    if (src == Source::USB) {
        char b[160];
        usbmic::status(b, sizeof b);
        return b;
    }
    String e;
    portENTER_CRITICAL(&err_mux);
    e = err;
    portEXIT_CRITICAL(&err_mux);
    return e;
}

bool input_ready() {
    if (src == Source::SIM) return true;
    if (src == Source::USB) return usbmic::streaming();
    return i2s_ok;
}

void apply_settings() {
    const Settings& s = g_settings;
    if (!s.agc) {
        cur_gain_db = s.gain_db;
        gain_lin = powf(10.0f, s.gain_db / 20.0f);
    }
    sim_dirty = true;
    // Switching source works live; pins and the analysis rate are read at boot.
    if (s.source == Source::I2S && !i2s_ok) i2s_ok = i2s_start(s);
    if (s.source == Source::USB && !usbmic::started()) usbmic::begin(fs_boot, 32000, usb_sink);
    if (s.source != src) {
        src = s.source;
        restart_stream = true;
    }
}

}  // namespace audio
