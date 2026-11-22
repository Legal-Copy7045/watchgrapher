#include "audio_in.h"

#include <driver/i2s.h>
#include <esp_heap_caps.h>

#include <atomic>
#include <algorithm>
#include <cmath>

#include "wgsim.h"

namespace audio {

static int16_t* ring = nullptr;
static size_t ring_n = 0;
static std::atomic<uint64_t> written{0};
static std::atomic<bool> running{false};
static int fs = 48000;
static Source src = Source::I2S;
static bool i2s_ok = false;
static String err;

static volatile float level = -120.0f;
static volatile float gain_lin = 8.0f;
static volatile float cur_gain_db = 18.0f;
static std::atomic<uint32_t> clips{0};
static volatile uint32_t clips_2s = 0;

static wg::SimWatch* sim = nullptr;
static volatile bool sim_dirty = false;

static constexpr size_t BLOCK = 480;   // 10 ms at 48 kHz

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
        err = String("I2S driver install failed: ") + esp_err_to_name(e);
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
        err = String("I2S pin setup failed: ") + esp_err_to_name(e);
        i2s_driver_uninstall(I2S_NUM_0);
        return false;
    }
    return true;
}

static inline void push(const float* x, size_t n, float g) {
    uint64_t w = written.load(std::memory_order_relaxed);
    float pk = 0.0f;
    uint32_t c = 0;
    for (size_t i = 0; i < n; ++i) {
        float v = x[i] * g;
        float a = fabsf(v);
        if (a > pk) pk = a;
        if (a >= 0.999f) { ++c; v = v > 0 ? 0.999f : -0.999f; }
        ring[(w + i) % ring_n] = (int16_t)lrintf(v * 32767.0f);
    }
    written.store(w + n, std::memory_order_release);
    if (c) clips += c;

    // Meter: fast attack, slow release.
    float db = pk > 1e-6f ? 20.0f * log10f(pk) : -120.0f;
    level = db > level ? db : level * 0.97f + db * 0.03f;
}

// Automatic makeup gain: hold the block peak near -8 dBFS, change at most
// ~2 dB every 2 s. Timing comes from WHEN the noises occur, so a gentle gain
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

static void task(void*) {
    static int32_t raw[BLOCK];
    static float f[BLOCK];
    float dc_x = 0, dc_y = 0;
    const float dc_r = 1.0f - 2.0f * (float)M_PI * 20.0f / fs;  // ~20 Hz DC blocker
    uint32_t t_win = millis();
    float pk_win = -120.0f;
    uint32_t clips_win = 0;
    TickType_t last = xTaskGetTickCount();
    // Pace the simulator in real time. BLOCK is 10 ms at 48 kHz.
    const TickType_t sim_ticks = std::max<TickType_t>(1, pdMS_TO_TICKS(1000 * BLOCK / fs));

    for (;;) {
        if (!running) {
            vTaskDelay(pdMS_TO_TICKS(20));
            last = xTaskGetTickCount();
            continue;
        }
        if (src == Source::SIM) {
            if (sim_dirty) { sim->set_params(g_settings.sim); sim_dirty = false; }
            sim->generate(f, BLOCK);
            push(f, BLOCK, 1.0f);   // the simulator is already at a sensible level
            vTaskDelayUntil(&last, sim_ticks);
        } else {
            if (!i2s_ok) { vTaskDelay(pdMS_TO_TICKS(100)); continue; }
            size_t got = 0;
            i2s_read(I2S_NUM_0, raw, sizeof raw, &got, pdMS_TO_TICKS(200));
            size_t n = got / sizeof(int32_t);
            for (size_t i = 0; i < n; ++i) {
                float x = (float)raw[i] / 2147483648.0f;   // 24-bit data, MSB-aligned
                dc_y = x - dc_x + dc_r * dc_y;
                dc_x = x;
                f[i] = dc_y;
            }
            if (n) push(f, n, gain_lin);
        }
        pk_win = fmaxf(pk_win, level);
        uint32_t now = millis();
        if (now - t_win >= 2000) {
            uint32_t c = clips.load();
            clips_2s = c - clips_win;
            clips_win = c;
            if (src == Source::I2S) agc_step(pk_win);
            pk_win = -120.0f;
            t_win = now;
        }
    }
}

bool begin(float seconds) {
    const Settings& s = g_settings;
    fs = s.sample_rate;
    src = s.source;
    if (seconds <= 0) {
        err = "Not enough PSRAM. WatchGrapher needs an ESP32-S3 with 8 MB of PSRAM.";
        return false;
    }
    ring_n = (size_t)(seconds * fs);
    ring = (int16_t*)heap_caps_malloc(ring_n * sizeof(int16_t), MALLOC_CAP_SPIRAM);
    if (!ring) {
        err = "Could not allocate the audio buffer in PSRAM.";
        return false;
    }
    sim = new wg::SimWatch(fs);
    sim->set_params(s.sim);
    cur_gain_db = s.gain_db;
    gain_lin = powf(10.0f, s.gain_db / 20.0f);
    if (src == Source::I2S) i2s_ok = i2s_start(s);
    xTaskCreatePinnedToCore(task, "audio", 6144, nullptr, 10, nullptr, 0);
    return src == Source::SIM || i2s_ok;
}

void set_capturing(bool on) { running = on; }
bool capturing() { return running; }
uint64_t total() { return written.load(std::memory_order_acquire); }
int sample_rate() { return fs; }
float ring_seconds() { return (float)ring_n / fs; }

size_t copy_window(float* dst, size_t n, uint64_t end) {
    uint64_t w = total();
    if (end > w) end = w;
    uint64_t avail = std::min<uint64_t>(end, ring_n - (size_t)fs / 2);  // keep clear of the writer
    if (n > avail) n = (size_t)avail;
    uint64_t start = end - n;
    const float k = 1.0f / 32768.0f;
    for (size_t i = 0; i < n; ++i) dst[i] = ring[(start + i) % ring_n] * k;
    return n;
}

float level_db() { return level; }
float gain_db() { return cur_gain_db; }
uint32_t clips_recent() { return clips_2s; }
const char* error() { return err.c_str(); }

void apply_settings() {
    const Settings& s = g_settings;
    // Switching between the pickup and the simulator works live; pins and
    // sample rate are read once at boot.
    if (s.source == Source::I2S && !i2s_ok) i2s_ok = i2s_start(s);
    src = s.source;
    if (!s.agc) {
        cur_gain_db = s.gain_db;
        gain_lin = powf(10.0f, s.gain_db / 20.0f);
    }
    sim_dirty = true;
}

}  // namespace audio
