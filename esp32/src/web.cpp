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
//   POST /api/update?key=HOTSPOT_PASSWORD   firmware .bin as the raw body (OTA)
//
// Built on ESP-IDF's esp_http_server rather than Arduino's WebServer. That one
// serves a single connection at a time and waits up to 5 s for an idle one to
// send its request -- and desktop Chrome and Edge open spare connections that
// send nothing, so every request queued behind them and the app looked dead in
// a full-size browser. esp_http_server multiplexes its sockets, and its task
// runs above the analysis task, so the page stays responsive while measuring.
#include "web.h"

#include <LittleFS.h>
#include <Update.h>
#include <esp_heap_caps.h>
#include <esp_http_server.h>
#include <sys/stat.h>

#include <utility>
#include <vector>

#include "audio_in.h"
#include "engine.h"
#include "json.h"
#include "logx.h"
#include "net.h"
#include "settings.h"
#include "storage.h"
#include "sync.h"
#include "web_assets.h"

namespace web {

static httpd_handle_t server = nullptr;

// ---- request helpers ---------------------------------------------------------

static int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static String url_decode(const char* s, size_t n) {
    String out;
    out.reserve(n);
    for (size_t i = 0; i < n; ++i) {
        char c = s[i];
        if (c == '+') out += ' ';
        else if (c == '%' && i + 2 < n + 0 && hexval(s[i + 1]) >= 0 && hexval(s[i + 2]) >= 0) {
            out += (char)(hexval(s[i + 1]) * 16 + hexval(s[i + 2]));
            i += 2;
        } else out += c;
    }
    return out;
}

// Query string and (optionally) an x-www-form-urlencoded body, as key/value
// pairs -- the same interface the handlers had with Arduino's WebServer.
struct Req {
    httpd_req_t* r;
    std::vector<std::pair<String, String>> args;

    bool hasArg(const char* k) const {
        for (const auto& a : args)
            if (a.first == k) return true;
        return false;
    }
    String arg(const char* k) const {
        for (const auto& a : args)
            if (a.first == k) return a.second;
        return String();
    }
    void parse(const char* s, size_t n) {
        size_t i = 0;
        while (i < n) {
            size_t amp = i;
            while (amp < n && s[amp] != '&') ++amp;
            size_t eq = i;
            while (eq < amp && s[eq] != '=') ++eq;
            if (amp > i) {
                String k = url_decode(s + i, eq - i);
                String v = eq < amp ? url_decode(s + eq + 1, amp - eq - 1) : String();
                args.emplace_back(k, v);
            }
            i = amp + 1;
        }
    }
};

// Read the whole body (up to `max` bytes) into `out`. False on error or too large.
static bool read_body(httpd_req_t* r, String& out, size_t max) {
    const size_t len = r->content_len;
    if (len > max) return false;
    out = String();
    if (!out.reserve(len)) return false;
    char buf[1024];
    size_t got = 0;
    while (got < len) {
        int n = httpd_req_recv(r, buf, std::min(sizeof buf, len - got));
        if (n == HTTPD_SOCK_ERR_TIMEOUT) continue;
        if (n <= 0) return false;
        out.concat(buf, n);
        got += n;
    }
    return true;
}

static Req request(httpd_req_t* r, bool form_body) {
    Req q{r, {}};
    size_t ql = httpd_req_get_url_query_len(r);
    if (ql) {
        std::vector<char> qs(ql + 1);
        if (httpd_req_get_url_query_str(r, qs.data(), qs.size()) == ESP_OK) q.parse(qs.data(), ql);
    }
    if (form_body && r->content_len) {
        String body;
        if (read_body(r, body, 16384)) q.parse(body.c_str(), body.length());
    }
    return q;
}

static const char* status_line(int code) {
    switch (code) {
        case 200: return "200 OK";
        case 302: return "302 Found";
        case 400: return "400 Bad Request";
        case 403: return "403 Forbidden";
        case 404: return "404 Not Found";
        case 409: return "409 Conflict";
        case 413: return "413 Payload Too Large";
        case 500: return "500 Internal Server Error";
        case 503: return "503 Service Unavailable";
        case 507: return "507 Insufficient Storage";
        default: return "500 Internal Server Error";
    }
}

static esp_err_t send_json(httpd_req_t* r, const std::string& s, int code = 200) {
    httpd_resp_set_status(r, status_line(code));
    httpd_resp_set_type(r, "application/json");
    httpd_resp_set_hdr(r, "Cache-Control", "no-store");
    return httpd_resp_send(r, s.data(), s.size());
}

static esp_err_t send_text(httpd_req_t* r, int code, const char* type, const char* text) {
    httpd_resp_set_status(r, status_line(code));
    httpd_resp_set_type(r, type);
    return httpd_resp_send(r, text, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t stream_file(httpd_req_t* r, File& f, const char* type) {
    httpd_resp_set_type(r, type);
    httpd_resp_set_hdr(r, "Cache-Control", "no-store");
    static char buf[2048];   // handlers run one at a time on the server task
    for (;;) {
        size_t n = f.read((uint8_t*)buf, sizeof buf);
        if (!n) break;
        if (httpd_resp_send_chunk(r, buf, n) != ESP_OK) {
            f.close();
            return ESP_FAIL;
        }
    }
    f.close();
    return httpd_resp_send_chunk(r, nullptr, 0);
}

// Restart once the reply has gone out.
static void restart_soon() {
    xTaskCreate([](void*) {
        vTaskDelay(pdMS_TO_TICKS(600));
        ESP.restart();
    }, "restart", 2048, nullptr, 1, nullptr);
}

// exists() would open the file, and a missing file is logged as an error by
// the VFS layer; stat() just answers.
static bool exists(const char* path) {
    struct stat st;
    return ::stat((String("/littlefs") + path).c_str(), &st) == 0;
}

// ---- static assets ---------------------------------------------------------

static esp_err_t h_asset(httpd_req_t* r) {
    const WebAsset* a = (const WebAsset*)r->user_ctx;
    httpd_resp_set_type(r, a->mime);
    httpd_resp_set_hdr(r, "Content-Encoding", "gzip");
    httpd_resp_set_hdr(r, "Cache-Control",
                       strcmp(a->path, "/index.html") == 0 ? "no-cache" : "public, max-age=86400");
    return httpd_resp_send(r, (const char*)a->data, a->len);
}

// ---- API ----------------------------------------------------------------------

static esp_err_t h_state(httpd_req_t* r) {
    Req q = request(r, false);
    if (q.hasArg("tzo")) engine::set_tz_offset_min(q.arg("tzo").toInt());
    if (q.hasArg("now")) engine::set_clock_ms(atoll(q.arg("now").c_str()));
    uint32_t since = q.hasArg("since") ? (uint32_t)strtoul(q.arg("since").c_str(), nullptr, 10)
                                      : 0xFFFFFFFFu;
    std::string s;
    engine::state_json(since, s);
    return send_json(r, s);
}

static esp_err_t h_cmd(httpd_req_t* r) {
    Req q = request(r, true);
    if (q.hasArg("now")) engine::set_clock_ms(atoll(q.arg("now").c_str()));
    std::string out = engine::command(q.arg("action"), [&q](const char* k) { return q.arg(k); });
    return send_json(r, out);
}

static esp_err_t h_history(httpd_req_t* r) {
    std::string s;
    engine::history_json(s);
    return send_json(r, s);
}

static esp_err_t h_settings_get(httpd_req_t* r) {
    using namespace json;
    std::string o = "{";
    {
        SettingsLock lock;
        const Settings& s = g_settings;
        kv(o, "source", s.source == Source::SIM ? "sim" : s.source == Source::USB ? "usb" : "i2s");
        kv(o, "sample_rate", s.sample_rate);
        kv(o, "pin_bclk", s.pin_bclk);
        kv(o, "pin_ws", s.pin_ws);
        kv(o, "pin_din", s.pin_din);
        kv(o, "pin_mclk", s.pin_mclk);
        kv(o, "i2s_right", s.i2s_right);
        kv(o, "gain_db", s.gain_db, 1);
        kv(o, "agc", s.agc);
        kv(o, "window_s", s.window_s, 1);
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
        kv(o, "ap_fallback", s.ap_fallback);
        kv(o, "sta_ssid", s.sta_ssid.c_str());
        kv(o, "hostname", s.hostname.c_str());
        kv(o, "pin_led", s.pin_led);
    }
    kv(o, "window_cap", engine::window_cap_s(), 1);
    kv(o, "ap_ssid_live", net::ap_ssid().c_str());
    kv(o, "net_status", net::status().c_str());
    kv(o, "sta_ip", net::sta_ip().c_str());
    kv(o, "fw", WG_FW_VERSION);
    kv(o, "variant", WG_VARIANT);
    kv(o, "psram_free", (long long)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    kv(o, "heap_free", (long long)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    size_t tot = storage::ready() ? LittleFS.totalBytes() : 0;
    size_t used = storage::ready() ? LittleFS.usedBytes() : 0;
    kv(o, "fs_total", (long long)tot);
    kv(o, "fs_used", (long long)used);
    close_obj(o);
    return send_json(r, o);
}

static esp_err_t h_settings_post(httpd_req_t* r) {
    Req q = request(r, true);
    bool reboot = false;
    {
        SettingsLock lock;
        Settings& s = g_settings;
        auto f = [&](const char* k, float& v, float lo, float hi) {
            if (!q.hasArg(k)) return;
            float x = q.arg(k).toFloat();
            if (std::isfinite(x)) v = constrain(x, lo, hi);   // "nan" would pass constrain()
        };
        auto i = [&](const char* k, int& v, int lo, int hi, bool needs_reboot) {
            if (!q.hasArg(k)) return;
            int nv = constrain((int)q.arg(k).toInt(), lo, hi);
            if (nv != v && needs_reboot) reboot = true;
            v = nv;
        };
        auto b = [&](const char* k, bool& v) {
            if (q.hasArg(k)) v = q.arg(k) == "1" || q.arg(k) == "true";
        };
        auto str = [&](const char* k, String& v, bool needs_reboot) {
            if (!q.hasArg(k)) return;
            String nv = q.arg(k);
            nv.trim();
            if (nv != v && needs_reboot) reboot = true;
            v = nv;
        };

        if (q.hasArg("ap_pass") && q.arg("ap_pass").length() && q.arg("ap_pass").length() < 8)
            return send_json(r, "{\"ok\":false,\"msg\":\"The hotspot password needs at least 8 characters.\"}");

        if (q.hasArg("source")) {
            String v = q.arg("source");
            s.source = v == "sim" ? Source::SIM : v == "usb" ? Source::USB : Source::I2S;
        }
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
        if (q.hasArg("forced_bph")) {
            // Auto (0) or one of the standard rates: anything else is a typo,
            // and a tiny one made the analysis allocate megabytes.
            int v = (int)q.arg("forced_bph").toInt();
            bool std_rate = v == 0;
            for (int rr : wg::STANDARD_BPH) std_rate |= v == rr;
            if (std_rate) s.cfg.forced_bph = v;
        }
        b("exact_amplitude", s.cfg.exact_amplitude);
        b("no_parity_fix", s.cfg.no_parity_fix);
        b("ap_fallback", s.ap_fallback);
        if (q.hasArg("sim_bph")) {
            int v = (int)q.arg("sim_bph").toInt();
            for (int rr : wg::STANDARD_BPH)
                if (v == rr) s.sim.bph = v;
        }
        f("sim_amp", s.sim.amplitude, 90, 360);
        f("sim_lift", s.sim.lift_angle, 10, 80);
        f("sim_rate", s.sim.rate_spd, -500, 500);
        f("sim_be", s.sim.beat_error_ms, 0, 20);
        f("sim_snr", s.sim.snr_db, -10, 60);
        str("ap_ssid", s.ap_ssid, true);
        if (q.hasArg("ap_pass") && q.arg("ap_pass").length()) str("ap_pass", s.ap_pass, true);
        str("sta_ssid", s.sta_ssid, true);
        if (q.hasArg("sta_pass") && (q.arg("sta_pass").length() || s.sta_ssid.isEmpty()))
            str("sta_pass", s.sta_pass, true);
        str("hostname", s.hostname, true);
    }
    settings_save();
    audio::apply_settings();
    return send_json(r, reboot ? "{\"ok\":true,\"reboot\":true,\"msg\":\"Saved. Restart to apply.\"}"
                               : "{\"ok\":true,\"reboot\":false,\"msg\":\"Saved.\"}");
}

static esp_err_t h_reboot(httpd_req_t* r) {
    esp_err_t e = send_json(r, "{\"ok\":true}");
    restart_soon();
    return e;
}

static esp_err_t h_reset(httpd_req_t* r) {
    settings_reset();
    esp_err_t e = send_json(r, "{\"ok\":true}");
    restart_soon();
    return e;
}

// The collection is the desktop's collection.json schema, managed by the
// browser. Saved via a temp file and a backup, so a power cut mid-write
// cannot lose it.
static bool storage_or_503(httpd_req_t* r) {
    if (storage::ready()) return true;
    send_json(r, "{\"ok\":false,\"msg\":\"Storage is still being prepared (first start after installing takes a few minutes).\"}", 503);
    return false;
}

static esp_err_t h_coll_get(httpd_req_t* r) {
    if (!storage_or_503(r)) return ESP_OK;
    storage::CollLock l;
    // The revision goes with it, so a later save can say what it was based on.
    static char rev[16];
    snprintf(rev, sizeof rev, "%u", (unsigned)storage::coll_rev());
    httpd_resp_set_hdr(r, "X-WG-Rev", rev);
    File f;
    if (!storage::coll_open(f)) return send_json(r, "{\"version\":1,\"watches\":[]}");
    return stream_file(r, f, "application/json");
}

static esp_err_t h_coll_post(httpd_req_t* r) {
    if (!storage_or_503(r)) return ESP_OK;
    // A page saving a collection it loaded before a sync changed it would
    // undo the sync: refuse, and the page reloads.
    char base[16] = "";
    if (httpd_req_get_hdr_value_str(r, "X-WG-Rev", base, sizeof base) == ESP_OK && base[0] &&
        strtoul(base, nullptr, 10) != storage::coll_rev())
        return send_json(r, "{\"ok\":false,\"stale\":true,\"msg\":\"The collection changed on the device (a sync) -- reloaded; please make that change again.\"}", 409);
    String body;
    if (!read_body(r, body, 4 * 1024 * 1024))
        return send_json(r, "{\"ok\":false,\"msg\":\"Collection too large or the upload broke off.\"}", 413);
    if (body.length() < 2 || body[0] != '{')
        return send_json(r, "{\"ok\":false,\"msg\":\"Expected a JSON object.\"}", 400);
    if (!storage::coll_replace([&](File& f) { return f.print(body) == body.length(); }))
        return send_json(r, "{\"ok\":false,\"msg\":\"Could not write -- storage full?\"}", 507);
    std::string o = "{\"ok\":true,\"rev\":" + std::to_string(storage::coll_rev()) + "}";
    return send_json(r, o);
}

static esp_err_t h_pr_list(httpd_req_t* r) {
    std::string s;
    engine::pr_list_json(s);
    return send_json(r, s);
}

static esp_err_t h_pr_csv(httpd_req_t* r) {
    if (!storage_or_503(r)) return ESP_OK;
    Req q = request(r, false);
    String id = q.arg("id");
    if (id.isEmpty() || id.indexOf('/') >= 0 || id.indexOf("..") >= 0)
        return send_text(r, 400, "text/plain", "bad id");
    String path = "/pr/" + id + ".csv";
    File f = exists(path.c_str()) ? LittleFS.open(path, "r") : File();
    if (!f) return send_text(r, 200, "text/csv", "");
    return stream_file(r, f, "text/csv");
}

// Stream the newest audio straight out of the ring buffer as a WAV, for
// offline analysis in the desktop app.
static esp_err_t h_wav(httpd_req_t* r) {
    Req q = request(r, false);
    const int fs = audio::sample_rate();
    float secs = q.hasArg("s") ? q.arg("s").toFloat() : 10.0f;
    if (!std::isfinite(secs)) secs = 10.0f;
    secs = constrain(secs, 1.0f, std::max(1.0f, audio::ring_seconds() - 1.0f));
    const uint64_t end = audio::total();
    uint32_t n = (uint32_t)std::min<uint64_t>(end - std::min(end, audio::epoch()), (uint64_t)(secs * fs));
    if (n < (uint32_t)fs / 2)
        return send_text(r, 409, "text/plain", "Not enough audio yet -- start listening first.");
    const uint64_t start = end - n;
    uint8_t h[44];
    auto w32 = [&](int o, uint32_t v) { for (int i = 0; i < 4; ++i) h[o + i] = (v >> (8 * i)) & 255; };
    auto w16 = [&](int o, uint16_t v) { h[o] = v & 255; h[o + 1] = v >> 8; };
    memcpy(h, "RIFF", 4); w32(4, 36 + n * 2); memcpy(h + 8, "WAVEfmt ", 8);
    w32(16, 16); w16(20, 1); w16(22, 1); w32(24, fs); w32(28, fs * 2); w16(32, 2); w16(34, 16);
    memcpy(h + 36, "data", 4); w32(40, n * 2);
    httpd_resp_set_type(r, "audio/wav");
    httpd_resp_set_hdr(r, "Content-Disposition", "attachment; filename=\"watchgrapher.wav\"");
    if (httpd_resp_send_chunk(r, (const char*)h, 44) != ESP_OK) return ESP_FAIL;
    static float fb[1024];
    static int16_t ib[1024];
    for (uint32_t done = 0; done < n;) {
        uint32_t c = std::min<uint32_t>(1024, n - done);
        size_t got = audio::copy_window(fb, c, start + done + c);
        if (got < c) memset(fb + got, 0, (c - got) * sizeof(float));
        for (uint32_t i = 0; i < c; ++i) ib[i] = (int16_t)lrintf(constrain(fb[i], -1.0f, 0.99997f) * 32767.0f);
        if (httpd_resp_send_chunk(r, (const char*)ib, c * 2) != ESP_OK) return ESP_FAIL;
        done += c;
    }
    return httpd_resp_send_chunk(r, nullptr, 0);
}

// Firmware upload: the .bin as the raw request body, with the hotspot password
// in ?key= -- anyone who can reach the page over a shared home network should
// not be able to reflash the device.
static esp_err_t h_update(httpd_req_t* r) {
    Req q = request(r, false);
    String pass;
    {
        SettingsLock lock;
        pass = g_settings.ap_pass.length() >= 8 ? g_settings.ap_pass : String("watchgrapher");
    }
    if (q.arg("key") != pass)
        return send_json(r, "{\"ok\":false,\"msg\":\"Wrong hotspot password -- firmware not changed.\"}", 403);
    const size_t len = r->content_len;
    if (len < 1024 || !Update.begin(len, U_FLASH))
        return send_json(r, "{\"ok\":false,\"msg\":\"That is not a firmware image that fits this board.\"}", 400);
    static uint8_t buf[4096];
    size_t got = 0;
    while (got < len) {
        int n = httpd_req_recv(r, (char*)buf, std::min(sizeof buf, len - got));
        if (n == HTTPD_SOCK_ERR_TIMEOUT) continue;
        if (n <= 0 || Update.write(buf, n) != (size_t)n) {
            Update.abort();
            return send_json(r, "{\"ok\":false,\"msg\":\"Upload broke off -- firmware not changed.\"}", 400);
        }
        got += n;
    }
    if (!Update.end(true) || Update.hasError())
        return send_json(r, "{\"ok\":false,\"msg\":\"Update failed -- firmware not changed.\"}", 500);
    esp_err_t e = send_json(r, "{\"ok\":true,\"msg\":\"Updated. Restarting...\"}");
    restart_soon();
    return e;
}

// Desktop sync: GET for status; POST action=pair (desktop, code) | now |
// forget | auto (on).
static esp_err_t h_sync_get(httpd_req_t* r) {
    std::string s;
    dsync::status_json(s);
    return send_json(r, s);
}

static esp_err_t h_sync_post(httpd_req_t* r) {
    Req q = request(r, true);
    String a = q.arg("action"), msg;
    bool ok = true;
    if (a == "pair") ok = dsync::pair(q.arg("desktop"), q.arg("code"), msg);
    else if (a == "now") ok = dsync::sync_now(msg);
    else if (a == "forget") { dsync::forget(); msg = "Forgotten."; }
    else if (a == "auto") { dsync::set_auto(q.arg("on") != "0"); msg = "Saved."; }
    else { ok = false; msg = "Unknown action."; }
    std::string o = "{";
    json::kv(o, "ok", ok);
    json::kv(o, "msg", msg.c_str());
    json::close_obj(o);
    return send_json(r, o);
}

static esp_err_t h_not_found(httpd_req_t* r, httpd_err_code_t) {
    // Phones probing for a captive portal, and any unknown page: the app.
    if (r->method == HTTP_GET && strncmp(r->uri, "/api/", 5) != 0) {
        httpd_resp_set_status(r, "302 Found");
        httpd_resp_set_hdr(r, "Location", "/");
        return httpd_resp_send(r, nullptr, 0);
    }
    return send_text(r, 404, "text/plain", "not found");
}

static void route(const char* uri, httpd_method_t m, esp_err_t (*h)(httpd_req_t*), void* ctx = nullptr) {
    httpd_uri_t u = {};
    u.uri = uri;
    u.method = m;
    u.handler = h;
    u.user_ctx = ctx;
    httpd_register_uri_handler(server, &u);
}

void begin() {
    httpd_config_t c = HTTPD_DEFAULT_CONFIG();
    c.max_uri_handlers = WEB_ASSET_COUNT + 20;
    c.stack_size = 8192;
    c.lru_purge_enable = true;      // a browser's idle spare connections never lock others out
    c.max_open_sockets = 7;
    c.recv_wait_timeout = 10;
    c.send_wait_timeout = 10;
    if (httpd_start(&server, &c) != ESP_OK) {
        logx::printf("Web server failed to start\n");
        return;
    }
    const WebAsset* index = nullptr;
    for (size_t i = 0; i < WEB_ASSET_COUNT; ++i) {
        route(WEB_ASSETS[i].path, HTTP_GET, h_asset, (void*)&WEB_ASSETS[i]);
        if (strcmp(WEB_ASSETS[i].path, "/index.html") == 0) index = &WEB_ASSETS[i];
    }
    if (index) route("/", HTTP_GET, h_asset, (void*)index);
    route("/api/state", HTTP_GET, h_state);
    route("/api/cmd", HTTP_POST, h_cmd);
    route("/api/history", HTTP_GET, h_history);
    route("/api/settings", HTTP_GET, h_settings_get);
    route("/api/settings", HTTP_POST, h_settings_post);
    route("/api/reboot", HTTP_POST, h_reboot);
    route("/api/factory_reset", HTTP_POST, h_reset);
    route("/api/collection", HTTP_GET, h_coll_get);
    route("/api/collection", HTTP_POST, h_coll_post);
    route("/api/pr", HTTP_GET, h_pr_list);
    route("/api/pr/csv", HTTP_GET, h_pr_csv);
    route("/api/wav", HTTP_GET, h_wav);
    route("/api/update", HTTP_POST, h_update);
    route("/api/sync", HTTP_GET, h_sync_get);
    route("/api/sync", HTTP_POST, h_sync_post);
    httpd_register_err_handler(server, HTTPD_404_NOT_FOUND, h_not_found);
}

void loop() {}   // esp_http_server runs on its own task

}  // namespace web
