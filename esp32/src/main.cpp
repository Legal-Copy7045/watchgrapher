// WatchGrapher standalone -- an ESP32-S3 acoustic timegrapher.
//
// Core 0: Wi-Fi, and the audio capture task (I2S -> ring buffer in PSRAM).
// Core 1: the Arduino loop (web server) and the analysis task, time-sliced.
//
// The device puts up its own Wi-Fi hotspot ("WatchGrapher-XXXX", password
// "watchgrapher" until you change it) and serves the app at
// http://192.168.4.1 -- or http://watchgrapher.local on a network it joins,
// in which case the hotspot steps aside while that network is reachable.
#include <Arduino.h>
#include <LittleFS.h>
#include <WiFi.h>
#include <esp_heap_caps.h>

#include "audio_in.h"
#include "engine.h"
#include "improv.h"
#include "led.h"
#include "logx.h"
#include "net.h"
#include "settings.h"
#include "web.h"

// Leave this much PSRAM for everything that is not the two big buffers:
// the analyzer's working vectors, JSON replies, uploads, Wi-Fi buffers.
static constexpr size_t PSRAM_RESERVE = 1536 * 1024;
static constexpr float RING_EXTRA_S = 1.5f;   // ring holds the window plus this

void setup() {
    settings_load();
    logx::begin(g_settings.source == Source::USB);
    logx::printf("\nWatchGrapher %s (%s)\n", WG_FW_VERSION, WG_VARIANT);
    // Answer the browser installer straight away. It asks for Wi-Fi details
    // within a second or two of flashing; a first boot then spends several
    // seconds formatting storage, and an Improv that started after that was
    // never heard -- the installer just skipped Wi-Fi setup.
    improv::begin(g_settings.source == Source::USB);
    led::begin(g_settings.pin_led);
    logx::printf("Mounting storage (the first start after installing formats it -- up to a minute)\n");
    if (!LittleFS.begin(true)) logx::printf("LittleFS mount failed\n");
    engine::recover_interrupted_pr();

    // Size the analysis window to the PSRAM fitted: per second of window the
    // analyzer needs two floats per sample and the ring one int16.
    const int fs = g_settings.sample_rate;
    size_t ps = psramFound() ? heap_caps_get_free_size(MALLOC_CAP_SPIRAM) : 0;
    float cap = 0.0f;
    if (ps > PSRAM_RESERVE) {
        // cap*fs*8 (analyzer) + (cap + extra)*fs*2 (ring) <= free - reserve
        const float per_s = fs * (2.0f * sizeof(float) + sizeof(int16_t));
        cap = ((float)(ps - PSRAM_RESERVE) - RING_EXTRA_S * fs * sizeof(int16_t)) / per_s;
        cap = std::min(20.0f, cap);
    }
    logx::printf("PSRAM free %u, analysis window up to %.1f s at %d Hz\n", (unsigned)ps, cap, fs);
    if (cap >= 4.0f) {
        if (!audio::begin(cap + RING_EXTRA_S)) logx::printf("audio: %s\n", audio::status().c_str());
        if (!engine::begin(cap)) logx::printf("engine: analyzer allocation failed\n");
    } else {
        logx::printf("Not enough PSRAM. WatchGrapher needs an ESP32-S3 with 8 MB PSRAM (N8R8 / N16R8).\n");
        audio::begin(0);   // records the error for the UI
    }

    net::begin();
    web::begin();
}

void loop() {
    web::loop();
    net::loop();
    delay(2);   // yield to the analysis task, which shares this core
}
