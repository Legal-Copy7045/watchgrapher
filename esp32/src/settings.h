// Persistent settings (NVS). Everything the web UI can change lives here.
#pragma once

#include <Arduino.h>

#include "wgdsp.h"
#include "wgsim.h"

enum class Source : uint8_t { I2S = 0, SIM = 1 };

struct Settings {
    // ---- audio input ------------------------------------------------------
    Source source = Source::I2S;
    int sample_rate = 48000;
    // Default pins suit an ESP32-S3-DevKitC-1 with an INMP441 / ICS-43434
    // I2S MEMS microphone or a PCM1808 I2S ADC (which also needs MCLK).
    int pin_bclk = 5;
    int pin_ws = 6;
    int pin_din = 4;
    int pin_mclk = -1;          // -1 = no master clock output
    int i2s_right = 0;          // 1 = read the right slot (mic L/R pin tied high)
    float gain_db = 18.0f;      // digital makeup gain into the analysis buffer
    bool agc = true;            // adjust that gain automatically

    // ---- analysis ---------------------------------------------------------
    float window_s = 12.0f;     // capped at what PSRAM allows (see Engine)
    wg::Config cfg;

    // ---- simulated watch --------------------------------------------------
    wg::SimParams sim;

    // ---- network ----------------------------------------------------------
    String ap_ssid;             // empty = "WatchGrapher-XXXX" from the chip ID
    String ap_pass = "watchgrapher";   // WPA2, at least 8 characters
    String sta_ssid;            // optional: also join an existing network
    String sta_pass;
    String hostname = "watchgrapher";  // mDNS: http://watchgrapher.local

    int pin_led = 48;           // on-board RGB LED (DevKitC-1 v1.0; 38 on v1.1), -1 = off
};

extern Settings g_settings;

void settings_load();
void settings_save();
void settings_reset();
