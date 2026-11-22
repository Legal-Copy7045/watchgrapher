#include "sync.h"

#include <HTTPClient.h>
#include <LittleFS.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <esp_mac.h>

#include <atomic>
#include <vector>

#include "json.h"
#include "logx.h"
#include "settings.h"
#include "storage.h"

namespace dsync {

static constexpr uint16_t BEACON_PORT = 8479;
static constexpr uint32_t SEEN_TTL_MS = 15000;           // forget a computer after this
static constexpr uint32_t AUTO_EVERY_MS = 15 * 60 * 1000;
static constexpr uint32_t AFTER_CHANGE_MS = 60 * 1000;   // settle time after an edit

struct Desktop {
    String id, name;
    IPAddress ip;
    uint16_t port = 8478;
    uint32_t seen_ms = 0;
};

static SemaphoreHandle_t mtx;
struct Lock {
    Lock() { xSemaphoreTakeRecursive(mtx, portMAX_DELAY); }
    ~Lock() { xSemaphoreGiveRecursive(mtx); }
};

static std::vector<Desktop> seen;
static String paired_id, paired_name, token, last_sync, device_id;
static bool auto_sync = true;
static std::atomic<bool> busy{false};
static String state = "idle";            // last result, for the UI (under mtx)
static uint32_t last_ok_ms = 0;
static bool synced_this_boot = false;
static uint32_t synced_rev = 0;
static uint32_t rev_changed_ms = 0, rev_seen = 0;

// ---- tiny JSON field extraction (no parser on the device) -------------------
// "key": "value" or "key": 123, tolerating the spaces Python's json.dumps puts in.
static String jstr(const String& s, const char* key) {
    String k = String("\"") + key + "\"";
    int i = s.indexOf(k);
    if (i < 0) return "";
    i += k.length();
    while (i < (int)s.length() && (s[i] == ' ' || s[i] == ':')) ++i;
    if (i >= (int)s.length()) return "";
    if (s[i] == '"') {
        String out;
        for (++i; i < (int)s.length() && s[i] != '"'; ++i) {
            if (s[i] == '\\' && i + 1 < (int)s.length()) ++i;
            out += s[i];
        }
        return out;
    }
    int j = i;
    while (j < (int)s.length() && s[j] != ',' && s[j] != '}' && s[j] != ' ') ++j;
    return s.substring(i, j);
}

static void save_pairing() {
    Preferences p;
    p.begin("wgsync", false);
    p.putString("id", paired_id);
    p.putString("name", paired_name);
    p.putString("tok", token);
    p.putString("last", last_sync);
    p.putBool("auto", auto_sync);
    p.end();
}

static void load_pairing() {
    Preferences p;
    p.begin("wgsync", true);
    if (p.isKey("id")) paired_id = p.getString("id", "");
    if (p.isKey("name")) paired_name = p.getString("name", "");
    if (p.isKey("tok")) token = p.getString("tok", "");
    if (p.isKey("last")) last_sync = p.getString("last", "");
    if (p.isKey("auto")) auto_sync = p.getBool("auto", true);
    p.end();
}

// ---- discovery ------------------------------------------------------------

static void listen_task(void*) {
    WiFiUDP udp;
    bool open = false;
    char buf[512];
    for (;;) {
        if (WiFi.status() != WL_CONNECTED) {
            if (open) { udp.stop(); open = false; }
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }
        if (!open) open = udp.begin(BEACON_PORT);
        int n = udp.parsePacket();
        if (n <= 0) {
            vTaskDelay(pdMS_TO_TICKS(200));
            continue;
        }
        int got = udp.read(buf, sizeof buf - 1);
        if (got <= 8 || memcmp(buf, "WGDESK1 ", 8) != 0) continue;
        buf[got] = 0;
        String body(buf + 8);
        Desktop d;
        d.id = jstr(body, "id");
        d.name = jstr(body, "name");
        d.port = (uint16_t)jstr(body, "port").toInt();
        d.ip = udp.remoteIP();
        d.seen_ms = millis();
        if (d.id.isEmpty() || !d.port) continue;
        Lock l;
        bool found = false;
        for (auto& e : seen)
            if (e.id == d.id) { e = d; found = true; }
        if (!found && seen.size() < 8) seen.push_back(d);
    }
}

static void set_state(const String& v) {
    xSemaphoreTakeRecursive(mtx, portMAX_DELAY);
    state = v;
    xSemaphoreGiveRecursive(mtx);
}

static bool find_seen(const String& id, Desktop& out) {
    Lock l;
    for (const auto& e : seen)
        if (e.id == id && millis() - e.seen_ms < SEEN_TTL_MS) { out = e; return true; }
    return false;
}

static String iso_now() {
    time_t t = time(nullptr);
    if (t < 1600000000) return "";
    struct tm tm;
    localtime_r(&t, &tm);
    char b[24];
    strftime(b, sizeof b, "%Y-%m-%dT%H:%M:%S", &tm);
    return b;
}

// ---- pairing ----------------------------------------------------------------

bool pair(const String& desktop_id, const String& code, String& msg) {
    Desktop d;
    if (!find_seen(desktop_id, d)) { msg = "That computer isn't on the network right now."; return false; }
    String host;
    {
        SettingsLock sl;
        host = g_settings.hostname;
    }
    std::string body = "{";
    json::kv(body, "device_id", device_id.c_str());
    json::kv(body, "device_name", ("WatchGrapher (" + host + ")").c_str());
    json::kv(body, "code", code.c_str());
    json::close_obj(body);
    WiFiClient c;
    HTTPClient http;
    http.setTimeout(8000);
    if (!http.begin(c, d.ip.toString(), d.port, "/wg-sync/pair")) { msg = "Couldn't reach the computer."; return false; }
    http.addHeader("Content-Type", "application/json");
    int code_http = http.POST((uint8_t*)body.data(), body.size());
    String resp = code_http > 0 ? http.getString() : String();
    http.end();
    if (code_http != 200) {
        msg = jstr(resp, "msg");
        if (msg.isEmpty()) msg = code_http > 0 ? "The computer refused." : "Couldn't reach the computer.";
        return false;
    }
    String tok = jstr(resp, "token");
    if (tok.isEmpty()) { msg = "The computer gave no token."; return false; }
    {
        Lock l;
        paired_id = d.id;
        paired_name = d.name;
        token = tok;
        synced_this_boot = false;
        save_pairing();
    }
    msg = "Paired with " + d.name + ".";
    logx::printf("Sync: paired with %s\n", d.name.c_str());
    return true;
}

void forget() {
    Lock l;
    paired_id = paired_name = token = last_sync = "";
    save_pairing();
    set_state("idle");
}

void set_auto(bool on) {
    Lock l;
    auto_sync = on;
    save_pairing();
}

// ---- syncing ------------------------------------------------------------------

static void do_sync() {
    Desktop d;
    String id, tok;
    {
        Lock l;
        id = paired_id;
        tok = token;
    }
    if (id.isEmpty()) { set_state("not paired"); return; }
    if (!find_seen(id, d)) { set_state("the computer isn't on the network"); return; }
    if (!storage::ready()) { set_state("storage not ready"); return; }

    const uint32_t rev0 = storage::coll_rev();
    WiFiClient c;
    HTTPClient http;
    http.setTimeout(30000);
    if (!http.begin(c, d.ip.toString(), d.port, "/wg-sync/sync")) { set_state("couldn't reach the computer"); return; }
    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-WG-Device", device_id);
    http.addHeader("X-WG-Token", tok);
    const char* want[] = {"X-WG-Added-Watches", "X-WG-Added-Records"};
    http.collectHeaders(want, 2);
    int rc;
    {
        // Send the collection as it is now (held, so a save can't swap it mid-send).
        storage::CollLock cl;
        File f;
        if (storage::coll_open(f)) {
            rc = http.sendRequest("POST", &f, f.size());
            f.close();
        } else {
            static const char empty[] = "{\"version\":1,\"watches\":[]}";
            rc = http.POST((uint8_t*)empty, sizeof empty - 1);
        }
    }
    if (rc != 200) {
        String resp = rc > 0 ? http.getString() : String();
        String m = jstr(resp, "msg");
        set_state(rc == 403 ? "the computer doesn't know this device any more -- pair again"
                          : (m.length() ? m : (rc > 0 ? "the computer refused (" + String(rc) + ")"
                                                      : "couldn't reach the computer")));
        http.end();
        if (rc == 403) forget();
        logx::printf("Sync: failed: %s\n", state.c_str());
        return;
    }
    // The reply is the merged collection. If the page saved an edit while it
    // was on the way, that edit isn't in it: sync again shortly instead.
    if (storage::coll_rev() != rev0) {
        http.end();
        set_state("the collection changed during the sync -- will sync again");
        rev_changed_ms = millis() - AFTER_CHANGE_MS + 5000;
        return;
    }
    const String aw = http.header("X-WG-Added-Watches"), ar = http.header("X-WG-Added-Records");
    bool ok = storage::coll_replace([&](File& f) {
        int n = http.writeToStream(&f);
        return n > 2;
    });
    http.end();
    if (!ok) { set_state("the reply didn't arrive whole -- nothing changed"); return; }
    {
        Lock l;
        last_sync = iso_now();
        last_ok_ms = millis();
        synced_this_boot = true;
        synced_rev = storage::coll_rev();
        rev_seen = synced_rev;
        save_pairing();
    }
    set_state("synced with " + d.name + " -- the computer got " + aw + " new watch(es) and " + ar +
            " new record(s)");
    logx::printf("Sync: %s\n", state.c_str());
}

static void sync_task(void*) {
    do_sync();
    busy = false;
    vTaskDelete(nullptr);
}

bool sync_now(String& msg) {
    if (busy.exchange(true)) { msg = "A sync is already running."; return false; }
    set_state("syncing...");
    if (xTaskCreatePinnedToCore(sync_task, "sync", 8192, nullptr, 1, nullptr, 0) != pdPASS) {
        busy = false;
        msg = "Couldn't start the sync.";
        return false;
    }
    msg = "Syncing.";
    return true;
}

void loop() {
    static uint32_t last = 0;
    const uint32_t now = millis();
    if (now - last < 2000) return;
    last = now;
    const uint32_t rev = storage::coll_rev();
    if (rev != rev_seen) { rev_seen = rev; rev_changed_ms = now; }
    bool go = false;
    {
        Lock l;
        Desktop d;
        if (!auto_sync || paired_id.isEmpty() || busy || !find_seen(paired_id, d)) return;
        if (!synced_this_boot) go = true;                                     // just appeared
        else if (now - last_ok_ms >= AUTO_EVERY_MS) go = true;                // periodically
        else if (rev != synced_rev && now - rev_changed_ms >= AFTER_CHANGE_MS) go = true;   // after edits
    }
    if (go) {
        String m;
        sync_now(m);
    }
}

void status_json(std::string& o) {
    using namespace json;
    Lock l;
    o = "{";
    kv(o, "online", WiFi.status() == WL_CONNECTED);
    kv(o, "paired_id", paired_id.c_str());
    kv(o, "paired_name", paired_name.c_str());
    kv(o, "auto", auto_sync);
    kv(o, "busy", (bool)busy);
    kv(o, "state", state.c_str());
    kv(o, "last_sync", last_sync.c_str());
    o += "\"seen\":[";
    bool first = true;
    for (const auto& e : seen) {
        if (millis() - e.seen_ms >= SEEN_TTL_MS) continue;
        if (!first) o += ',';
        first = false;
        o += '{';
        kv(o, "id", e.id.c_str());
        kv(o, "name", e.name.c_str());
        kv(o, "ip", e.ip.toString().c_str());
        close_obj(o);
    }
    o += "],";
    close_obj(o);
}

void begin() {
    mtx = xSemaphoreCreateRecursiveMutex();
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    char b[20];
    snprintf(b, sizeof b, "wg-%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    device_id = b;
    load_pairing();
    xTaskCreatePinnedToCore(listen_task, "syncudp", 4096, nullptr, 1, nullptr, 0);
}

}  // namespace dsync
