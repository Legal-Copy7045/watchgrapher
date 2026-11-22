#include "settings.h"

#include <Preferences.h>

Settings g_settings;

static SemaphoreHandle_t settings_mtx() {
    // Created on first use, before any task exists (settings_load runs in setup).
    static SemaphoreHandle_t m = xSemaphoreCreateRecursiveMutex();
    return m;
}
SettingsLock::SettingsLock() { xSemaphoreTakeRecursive(settings_mtx(), portMAX_DELAY); }
SettingsLock::~SettingsLock() { xSemaphoreGiveRecursive(settings_mtx()); }

static Preferences prefs;

void settings_load() {
    SettingsLock lock;
    Settings d;  // defaults
    Settings& s = g_settings;
    prefs.begin("wg", true);
    uint8_t src = prefs.getUChar("src", (uint8_t)d.source);
    s.source = src <= (uint8_t)Source::USB ? (Source)src : d.source;
    s.sample_rate = prefs.getInt("fs", d.sample_rate);
    s.pin_bclk = prefs.getInt("p_bclk", d.pin_bclk);
    s.pin_ws = prefs.getInt("p_ws", d.pin_ws);
    s.pin_din = prefs.getInt("p_din", d.pin_din);
    s.pin_mclk = prefs.getInt("p_mclk", d.pin_mclk);
    s.i2s_right = prefs.getInt("i2s_r", d.i2s_right);
    s.gain_db = prefs.getFloat("gain", d.gain_db);
    s.agc = prefs.getBool("agc", d.agc);
    s.window_s = prefs.getFloat("win", d.window_s);

    s.cfg.band_lo = prefs.getFloat("b_lo", d.cfg.band_lo);
    s.cfg.band_hi = prefs.getFloat("b_hi", d.cfg.band_hi);
    s.cfg.env_win_ms = prefs.getFloat("envw", d.cfg.env_win_ms);
    s.cfg.sub_threshold = prefs.getFloat("thr", d.cfg.sub_threshold);
    s.cfg.lift_angle = prefs.getFloat("lift", d.cfg.lift_angle);
    s.cfg.forced_bph = prefs.getInt("bph", d.cfg.forced_bph);
    s.cfg.exact_amplitude = prefs.getBool("exact", d.cfg.exact_amplitude);
    s.cfg.no_parity_fix = prefs.getBool("nopar", d.cfg.no_parity_fix);

    s.sim.bph = prefs.getInt("s_bph", d.sim.bph);
    s.sim.amplitude = prefs.getFloat("s_amp", d.sim.amplitude);
    s.sim.lift_angle = prefs.getFloat("s_lift", d.sim.lift_angle);
    s.sim.rate_spd = prefs.getFloat("s_rate", d.sim.rate_spd);
    s.sim.beat_error_ms = prefs.getFloat("s_be", d.sim.beat_error_ms);
    s.sim.snr_db = prefs.getFloat("s_snr", d.sim.snr_db);

    s.ap_ssid = prefs.getString("ap_ssid", d.ap_ssid);
    s.ap_pass = prefs.getString("ap_pass", d.ap_pass);
    s.sta_ssid = prefs.getString("sta_ssid", d.sta_ssid);
    s.sta_pass = prefs.getString("sta_pass", d.sta_pass);
    s.hostname = prefs.getString("host", d.hostname);
    s.pin_led = prefs.getInt("p_led", d.pin_led);
    prefs.end();
}

void settings_save() {
    SettingsLock lock;
    const Settings& s = g_settings;
    prefs.begin("wg", false);
    prefs.putUChar("src", (uint8_t)s.source);
    prefs.putInt("fs", s.sample_rate);
    prefs.putInt("p_bclk", s.pin_bclk);
    prefs.putInt("p_ws", s.pin_ws);
    prefs.putInt("p_din", s.pin_din);
    prefs.putInt("p_mclk", s.pin_mclk);
    prefs.putInt("i2s_r", s.i2s_right);
    prefs.putFloat("gain", s.gain_db);
    prefs.putBool("agc", s.agc);
    prefs.putFloat("win", s.window_s);

    prefs.putFloat("b_lo", s.cfg.band_lo);
    prefs.putFloat("b_hi", s.cfg.band_hi);
    prefs.putFloat("envw", s.cfg.env_win_ms);
    prefs.putFloat("thr", s.cfg.sub_threshold);
    prefs.putFloat("lift", s.cfg.lift_angle);
    prefs.putInt("bph", s.cfg.forced_bph);
    prefs.putBool("exact", s.cfg.exact_amplitude);
    prefs.putBool("nopar", s.cfg.no_parity_fix);

    prefs.putInt("s_bph", s.sim.bph);
    prefs.putFloat("s_amp", s.sim.amplitude);
    prefs.putFloat("s_lift", s.sim.lift_angle);
    prefs.putFloat("s_rate", s.sim.rate_spd);
    prefs.putFloat("s_be", s.sim.beat_error_ms);
    prefs.putFloat("s_snr", s.sim.snr_db);

    prefs.putString("ap_ssid", s.ap_ssid);
    prefs.putString("ap_pass", s.ap_pass);
    prefs.putString("sta_ssid", s.sta_ssid);
    prefs.putString("sta_pass", s.sta_pass);
    prefs.putString("host", s.hostname);
    prefs.putInt("p_led", s.pin_led);
    prefs.end();
}

void settings_reset() {
    prefs.begin("wg", false);
    prefs.clear();
    prefs.end();
    SettingsLock lock;
    g_settings = Settings();
}
