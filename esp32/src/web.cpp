// HTTP server: the single-page UI (embedded in the firmware, gzipped) and a
// small JSON API. Everything slow or chart-shaped happens in the browser; the
// device measures, logs and stores.
//
//   GET  /api/state?since=SEQ&now=EPOCH_MS&tzo=MIN   live state (+ measurement if new)
//   POST /api/cmd         action=listen|run_start|run_stop|run_clear|pr_start|pr_stop|pr_delete
//   GET  /api/history     recent readings [[t, rate, amp, be], ...]
//   GET  /api/settings    POST /api/settings (form fields)   POST /api/reboot
//   GET  /api/collection  POST /api/collection (raw JSON body) -- desktop collection.json schema
//   GET  /api/pr          power-reserve logs;  GET /api/pr/csv?id=ID  their samples
//   GET  /api/wav?s=SEC   the last SEC seconds of audio as a 16-bit WAV
//   POST /api/update      firmware .bin upload (OTA)
#include "web.h"

#include <LittleFS.h>
#include <Update.h>
#include <WebServer.h>
#include <WiFi.h>
#include <esp_heap_caps.h>

#include "audio_in.h"
#include "engine.h"
#include "json.h"
#include "settings.h"
#include "web_assets.h"

namespace web {

static WebServer server(80);

static void send_json(const std::string& s, int code = 200) {
    server.sendHeader("Cache-Control", "no-store");
    server.setContentLength(s.size());
    server.send(code, "application/json", "");
    server.sendContent(s.c_str(), s.size());
}

static String arg(const char* k) { return server.hasArg(k) ? server.arg(k) : String(); }

// ---- static assets ---------------------------------------------------------

static bool serve_asset(const String& uri) {
    String path = uri == "/" ? "/index.html" : uri;
    for (size_t i = 0; i < WEB_ASSET_COUNT; ++i) {
        const WebAsset& a = WEB_ASSETS[i];
        if (path == a.path) {
            server.sendHeader("Content-Encoding", "gzip");
            server.sendHeader("Cache-Control",
                              path == "/index.html" ? "no-cache" : "public, max-age=86400");
            server.send_P(200, a.mime, (const char*)a.data, a.len);
            return true;
        }
    }
    return false;
}

// ---- API ----------------------------------------------------------------------

static void h_state() {
    if (server.hasArg("tzo")) engine::set_tz_offset_min(arg("tzo").toInt());
    if (server.hasArg("now")) engine::set_clock_ms(atoll(arg("now").c_str()));
    uint32_t since = server.hasArg("since") ? (uint32_t)strtoul(arg("since").c_str(), nullptr, 10)
                                           : 0xFFFFFFFFu;
    std::string s;
    engine::state_json(since, s);
    send_json(s);
}

static void h_cmd() {
    if (server.hasArg("now")) engine::set_clock_ms(atoll(arg("now").c_str()));
    std::string r = engine::command(arg("action"), [](const char* k) { return arg(k); });
    send_json(r);
}

static void h_history() {
    std::string s;
    engine::history_json(s);
    send_json(s);
}

static void h_settings_get() {
    using namespace json;
    const Settings& s = g_settings;
    std::string o = "{";
    kv(o, "source", s.source == Source::SIM ? "sim" : "i2s");
    kv(o, "sample_rate", s.sample_rate);
    kv(o, "pin_bclk", s.pin_bclk);
    kv(o, "pin_ws", s.pin_ws);
    kv(o, "pin_din", s.pin_din);
    kv(o, "pin_mclk", s.pin_mclk);
    kv(o, "i2s_right", s.i2s_right);
    kv(o, "gain_db", s.gain_db, 1);
    kv(o, "agc", s.agc);
    kv(o, "window_s", s.window_s, 1);
    kv(o, "window_cap", engine::window_cap_s(), 1);
    kv(o, "band_lo", s.cfg.band_lo, 0);
    kv(o, "band_hi", s.cfg.band_hi, 0);
    kv(o, "env_win_ms", s.cfg.env_win_ms, 2);
    kv(o, "sub_threshold", s.cfg.sub_threshold, 3);
    kv(o, "lift_angle", s.cfg.lift_angle, 1);
    kv(o, "forced_bph", s.cfg.forced_bph);
    kv(o, "exact_amplitude", s.cfg.exact_amplitude);
    kv(o, "no_parity_fix", s.cfg.no_parity_fix);
    kv(o, "sim_bph", s.sim.bph);
    kv(o, "sim_amp", s.sim.amplitude, 1);
    kv(o, "sim_lift", s.sim.lift_angle, 1);
    kv(o, "sim_rate", s.sim.rate_spd, 1);
    kv(o, "sim_be", s.sim.beat_error_ms, 2);
    kv(o, "sim_snr", s.sim.snr_db, 1);
    kv(o, "ap_ssid", s.ap_ssid.c_str());
    kv(o, "ap_ssid_live", WiFi.softAPSSID().c_str());
    kv(o, "sta_ssid", s.sta_ssid.c_str());
    kv(o, "sta_ip", WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString().c_str() : "");
    kv(o, "hostname", s.hostname.c_str());
    kv(o, "pin_led", s.pin_led);
    kv(o, "fw", WG_FW_VERSION);
    kv(o, "psram_free", (long long)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    kv(o, "heap_free", (long long)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    size_t tot = LittleFS.totalBytes(), used = LittleFS.usedBytes();
    kv(o, "fs_total", (long long)tot);
    kv(o, "fs_used", (long long)used);
    close_obj(o);
    send_json(o);
}

static void h_settings_post() {
    Settings& s = g_settings;
    bool reboot = false;
    auto f = [](const char* k, float& v, float lo, float hi) {
        if (server.hasArg(k)) v = constrain(server.arg(k).toFloat(), lo, hi);
    };
    auto i = [&](const char* k, int& v, int lo, int hi, bool needs_reboot) {
        if (!server.hasArg(k)) return;
        int nv = constrain((int)server.arg(k).toInt(), lo, hi);
        if (nv != v && needs_reboot) reboot = true;
        v = nv;
    };
    auto b = [](const char* k, bool& v) {
        if (server.hasArg(k)) v = server.arg(k) == "1" || server.arg(k) == "true";
    };
    auto str = [&](const char* k, String& v, bool needs_reboot) {
        if (!server.hasArg(k)) return;
        String nv = server.arg(k);
        nv.trim();
        if (nv != v && needs_reboot) reboot = true;
        v = nv;
    };

    if (server.hasArg("source")) s.source = arg("source") == "sim" ? Source::SIM : Source::I2S;
    i("sample_rate", s.sample_rate, 16000, 96000, true);
    i("pin_bclk", s.pin_bclk, -1, 48, true);
    i("pin_ws", s.pin_ws, -1, 48, true);
    i("pin_din", s.pin_din, -1, 48, true);
    i("pin_mclk", s.pin_mclk, -1, 48, true);
    i("i2s_right", s.i2s_right, 0, 1, true);
    i("pin_led", s.pin_led, -1, 48, true);
    f("gain_db", s.gain_db, 0, 54);
    b("agc", s.agc);
    f("window_s", s.window_s, 4, engine::window_cap_s());
    f("band_lo", s.cfg.band_lo, 100, 8000);
    f("band_hi", s.cfg.band_hi, 2000, 40000);
    f("env_win_ms", s.cfg.env_win_ms, 0.1f, 2.0f);
    f("sub_threshold", s.cfg.sub_threshold, 0.03f, 0.6f);
    f("lift_angle", s.cfg.lift_angle, 10, 80);
    i("forced_bph", s.cfg.forced_bph, 0, 72000, false);
    b("exact_amplitude", s.cfg.exact_amplitude);
    b("no_parity_fix", s.cfg.no_parity_fix);
    i("sim_bph", s.sim.bph, 7200, 72000, false);
    f("sim_amp", s.sim.amplitude, 90, 360);
    f("sim_lift", s.sim.lift_angle, 10, 80);
    f("sim_rate", s.sim.rate_spd, -500, 500);
    f("sim_be", s.sim.beat_error_ms, 0, 20);
    f("sim_snr", s.sim.snr_db, -10, 60);
    str("ap_ssid", s.ap_ssid, true);
    if (server.hasArg("ap_pass") && server.arg("ap_pass").length()) {
        if (server.arg("ap_pass").length() < 8) {
            send_json("{\"ok\":false,\"msg\":\"The hotspot password needs at least 8 characters.\"}");
            return;
        }
        str("ap_pass", s.ap_pass, true);
    }
    str("sta_ssid", s.sta_ssid, true);
    if (server.hasArg("sta_pass") && (server.arg("sta_pass").length() || s.sta_ssid.isEmpty()))
        str("sta_pass", s.sta_pass, true);
    str("hostname", s.hostname, true);

    settings_save();
    audio::apply_settings();
    send_json(reboot ? "{\"ok\":true,\"reboot\":true,\"msg\":\"Saved. Restart to apply.\"}"
                     : "{\"ok\":true,\"reboot\":false,\"msg\":\"Saved.\"}");
}

static void h_reboot() {
    send_json("{\"ok\":true}");
    delay(300);
    ESP.restart();
}

static void h_reset() {
    settings_reset();
    send_json("{\"ok\":true}");
    delay(300);
    ESP.restart();
}

// The collection is the desktop's collection.json schema, managed by the
// browser. Saved via a temp file and a backup, so a power cut mid-write
// cannot lose it.
static const char* COLL = "/collection.json";
static const char* COLL_TMP = "/collection.tmp";
static const char* COLL_BAK = "/collection.bak";

static void h_coll_get() {
    const char* p = LittleFS.exists(COLL) ? COLL : (LittleFS.exists(COLL_BAK) ? COLL_BAK : nullptr);
    if (!p) {
        send_json("{\"version\":1,\"watches\":[]}");
        return;
    }
    File f = LittleFS.open(p, "r");
    server.sendHeader("Cache-Control", "no-store");
    server.streamFile(f, "application/json");
    f.close();
}

static void h_coll_post() {
    const String& body = server.arg("plain");
    if (body.length() < 2 || body[0] != '{') {
        send_json("{\"ok\":false,\"msg\":\"Expected a JSON object.\"}", 400);
        return;
    }
    File f = LittleFS.open(COLL_TMP, "w");
    if (!f || f.print(body) != body.length()) {
        if (f) f.close();
        LittleFS.remove(COLL_TMP);
        send_json("{\"ok\":false,\"msg\":\"Could not write -- storage full?\"}", 507);
        return;
    }
    f.close();
    LittleFS.remove(COLL_BAK);
    if (LittleFS.exists(COLL)) LittleFS.rename(COLL, COLL_BAK);
    LittleFS.rename(COLL_TMP, COLL);
    send_json("{\"ok\":true}");
}

static void h_pr_list() {
    std::string s;
    engine::pr_list_json(s);
    send_json(s);
}

static void h_pr_csv() {
    String id = arg("id");
    if (id.isEmpty() || id.indexOf('/') >= 0 || id.indexOf("..") >= 0) {
        server.send(400, "text/plain", "bad id");
        return;
    }
    File f = LittleFS.open("/pr/" + id + ".csv", "r");
    if (!f) {
        server.send(200, "text/csv", "");
        return;
    }
    server.sendHeader("Cache-Control", "no-store");
    server.streamFile(f, "text/csv");
    f.close();
}

// Stream the newest audio straight out of the ring buffer as a WAV, for
// offline analysis in the desktop app.
static void h_wav() {
    const int fs = audio::sample_rate();
    float secs = server.hasArg("s") ? arg("s").toFloat() : 10.0f;
    secs = constrain(secs, 1.0f, audio::ring_seconds() - 1.0f);
    const uint64_t end = audio::total();
    uint32_t n = (uint32_t)std::min<uint64_t>(end, (uint64_t)(secs * fs));
    if (n < (uint32_t)fs / 2) {
        server.send(409, "text/plain", "Not enough audio yet -- start listening first.");
        return;
    }
    const uint64_t start = end - n;
    uint8_t h[44];
    auto w32 = [&](int o, uint32_t v) { for (int i = 0; i < 4; ++i) h[o + i] = (v >> (8 * i)) & 255; };
    auto w16 = [&](int o, uint16_t v) { h[o] = v & 255; h[o + 1] = v >> 8; };
    memcpy(h, "RIFF", 4); w32(4, 36 + n * 2); memcpy(h + 8, "WAVEfmt ", 8);
    w32(16, 16); w16(20, 1); w16(22, 1); w32(24, fs); w32(28, fs * 2); w16(32, 2); w16(34, 16);
    memcpy(h + 36, "data", 4); w32(40, n * 2);
    server.sendHeader("Content-Disposition", "attachment; filename=\"watchgrapher.wav\"");
    server.setContentLength(44 + (size_t)n * 2);
    server.send(200, "audio/wav", "");
    server.sendContent((const char*)h, 44);
    static float fb[1024];
    static int16_t ib[1024];
    for (uint32_t done = 0; done < n;) {
        uint32_t c = std::min<uint32_t>(1024, n - done);
        size_t got = audio::copy_window(fb, c, start + done + c);
        if (got < c) memset(fb + got, 0, (c - got) * sizeof(float));
        for (uint32_t i = 0; i < c; ++i) ib[i] = (int16_t)lrintf(constrain(fb[i], -1.0f, 0.99997f) * 32767.0f);
        server.sendContent((const char*)ib, c * 2);
        done += c;
    }
}

static void h_update_done() {
    bool ok = !Update.hasError();
    send_json(ok ? "{\"ok\":true,\"msg\":\"Updated. Restarting...\"}"
                 : "{\"ok\":false,\"msg\":\"Update failed.\"}");
    if (ok) {
        delay(500);
        ESP.restart();
    }
}

static void h_update_chunk() {
    HTTPUpload& u = server.upload();
    if (u.status == UPLOAD_FILE_START) {
        Update.begin(UPDATE_SIZE_UNKNOWN, U_FLASH);
    } else if (u.status == UPLOAD_FILE_WRITE) {
        Update.write(u.buf, u.currentSize);
    } else if (u.status == UPLOAD_FILE_END) {
        Update.end(true);
    } else if (u.status == UPLOAD_FILE_ABORTED) {
        Update.abort();
    }
}

void begin() {
    server.on("/api/state", HTTP_GET, h_state);
    server.on("/api/cmd", HTTP_POST, h_cmd);
    server.on("/api/history", HTTP_GET, h_history);
    server.on("/api/settings", HTTP_GET, h_settings_get);
    server.on("/api/settings", HTTP_POST, h_settings_post);
    server.on("/api/reboot", HTTP_POST, h_reboot);
    server.on("/api/factory_reset", HTTP_POST, h_reset);
    server.on("/api/collection", HTTP_GET, h_coll_get);
    server.on("/api/collection", HTTP_POST, h_coll_post);
    server.on("/api/pr", HTTP_GET, h_pr_list);
    server.on("/api/pr/csv", HTTP_GET, h_pr_csv);
    server.on("/api/wav", HTTP_GET, h_wav);
    server.on("/api/update", HTTP_POST, h_update_done, h_update_chunk);
    server.onNotFound([]() {
        if (server.method() == HTTP_GET && serve_asset(server.uri())) return;
        // Phones probing for a captive portal, and any unknown path: the app.
        if (server.method() == HTTP_GET && !server.uri().startsWith("/api/")) {
            server.sendHeader("Location", "/");
            server.send(302, "text/plain", "");
            return;
        }
        server.send(404, "text/plain", "not found");
    });
    server.begin();
}

void loop() { server.handleClient(); }

}  // namespace web
