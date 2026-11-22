// WatchGrapher standalone -- an ESP32-S3 acoustic timegrapher.
//
// Core 0: Wi-Fi, and the audio capture task (I2S -> ring buffer in PSRAM).
// Core 1: the Arduino loop (web server) and the analysis task, time-sliced.
//
// The device puts up its own Wi-Fi hotspot ("WatchGrapher-XXXX", password
// "watchgrapher" until you change it) and serves the app at
// http://192.168.4.1 -- or http://watchgrapher.local on a network it joins.
#include <Arduino.h>
#include <ESPmDNS.h>
#include <LittleFS.h>
#include <WiFi.h>
#include <esp_heap_caps.h>
#include <esp_mac.h>

#include "audio_in.h"
#include "engine.h"
#include "led.h"
#include "settings.h"
#include "web.h"

// Leave this much PSRAM for everything that is not the two big buffers:
// the analyzer's working vectors, JSON replies, uploads, Wi-Fi buffers.
static constexpr size_t PSRAM_RESERVE = 1536 * 1024;
static constexpr float RING_EXTRA_S = 1.5f;   // ring holds the window plus this

static void wifi_start() {
    const Settings& s = g_settings;
    String ssid = s.ap_ssid;
    if (ssid.isEmpty()) {
        uint8_t mac[6];
        esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP);
        char b[24];
        snprintf(b, sizeof b, "WatchGrapher-%02X%02X", mac[4], mac[5]);
        ssid = b;
    }
    String pass = s.ap_pass.length() >= 8 ? s.ap_pass : String("watchgrapher");
    const bool sta = s.sta_ssid.length() > 0;
    WiFi.mode(sta ? WIFI_AP_STA : WIFI_AP);
    WiFi.setHostname(s.hostname.c_str());
    WiFi.softAP(ssid.c_str(), pass.c_str());
    if (sta) {
        WiFi.begin(s.sta_ssid.c_str(), s.sta_pass.c_str());
        configTime(0, 0, "pool.ntp.org", "time.cloudflare.com");
    }
    if (MDNS.begin(s.hostname.c_str())) MDNS.addService("http", "tcp", 80);
    Serial.printf("Hotspot \"%s\" -> http://%s\n", ssid.c_str(),
                  WiFi.softAPIP().toString().c_str());
}

void setup() {
    Serial.begin(115200);
    delay(200);
    settings_load();
    led::begin(g_settings.pin_led);
    if (!LittleFS.begin(true)) Serial.println("LittleFS mount failed");
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
    Serial.printf("PSRAM free %u, analysis window up to %.1f s at %d Hz\n", (unsigned)ps, cap, fs);
    if (cap >= 4.0f) {
        if (!audio::begin(cap + RING_EXTRA_S)) Serial.printf("audio: %s\n", audio::error());
        if (!engine::begin(cap)) Serial.println("engine: analyzer allocation failed");
    } else {
        Serial.println("Not enough PSRAM. WatchGrapher needs an ESP32-S3 with 8 MB PSRAM (N8R8 / N16R8).");
        audio::begin(0);   // records the error for the UI
    }

    wifi_start();
    web::begin();
}

void loop() {
    web::loop();
    delay(2);   // yield to the analysis task, which shares this core
}
