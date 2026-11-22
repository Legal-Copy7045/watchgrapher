#include "net.h"

#include <ESPmDNS.h>
#include <WiFi.h>
#include <esp_mac.h>

#include <atomic>

#include "logx.h"
#include "settings.h"

namespace net {

static std::atomic<bool> ready_{false};
static std::atomic<bool> ap_up{false};
static String ssid_;
static SemaphoreHandle_t mtx = nullptr;
static uint32_t state_since = 0;
static bool last_conn = false;

// Hotspot policy once a home network is set.
static constexpr uint32_t AP_OFF_AFTER_MS = 20000;   // joined and stable this long
static constexpr uint32_t AP_ON_AFTER_MS = 30000;    // not joined for this long

struct Lock {
    Lock() { xSemaphoreTakeRecursive(mtx, portMAX_DELAY); }
    ~Lock() { xSemaphoreGiveRecursive(mtx); }
};

static String ap_pass() {
    SettingsLock l;
    return g_settings.ap_pass.length() >= 8 ? g_settings.ap_pass : String("watchgrapher");
}

static void start_ap() {
    if (ap_up) return;
    WiFi.mode((WiFi.getMode() & WIFI_STA) ? WIFI_AP_STA : WIFI_AP);
    WiFi.softAP(ssid_.c_str(), ap_pass().c_str());
    ap_up = true;
    logx::printf("Hotspot \"%s\" on -> http://%s\n", ssid_.c_str(), WiFi.softAPIP().toString().c_str());
}

static void stop_ap() {
    if (!ap_up) return;
    WiFi.softAPdisconnect(true);
    WiFi.mode(WIFI_STA);
    ap_up = false;
    logx::printf("On %s as http://%s -- hotspot off\n", WiFi.SSID().c_str(),
                  WiFi.localIP().toString().c_str());
}

void begin() {
    if (!mtx) mtx = xSemaphoreCreateRecursiveMutex();
    Lock l;
    String sta_ssid, sta_pass, host;
    {
        SettingsLock sl;
        ssid_ = g_settings.ap_ssid;
        sta_ssid = g_settings.sta_ssid;
        sta_pass = g_settings.sta_pass;
        host = g_settings.hostname;
    }
    if (ssid_.isEmpty()) {
        uint8_t mac[6];
        esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP);
        char b[24];
        snprintf(b, sizeof b, "WatchGrapher-%02X%02X", mac[4], mac[5]);
        ssid_ = b;
    }
    WiFi.persistent(false);
    WiFi.setAutoReconnect(true);
    WiFi.setHostname(host.c_str());
    // Always start with the hotspot up: the home network may be out of reach,
    // and the hotspot is how the device is set up in the first place.
    WiFi.mode(sta_ssid.length() ? WIFI_AP_STA : WIFI_AP);
    WiFi.softAP(ssid_.c_str(), ap_pass().c_str());
    ap_up = true;
    if (sta_ssid.length()) {
        WiFi.begin(sta_ssid.c_str(), sta_pass.c_str());
        configTime(0, 0, "pool.ntp.org", "time.cloudflare.com");
    }
    if (MDNS.begin(host.c_str())) MDNS.addService("http", "tcp", 80);
    logx::printf("Hotspot \"%s\" -> http://%s\n", ssid_.c_str(), WiFi.softAPIP().toString().c_str());
    state_since = millis();
    ready_ = true;
}

bool ready() { return ready_; }

void loop() {
    if (!ready_) return;
    static uint32_t last = 0;
    const uint32_t now = millis();
    if (now - last < 1000) return;
    last = now;
    Lock l;
    bool have_sta, fallback;
    {
        SettingsLock sl;
        have_sta = g_settings.sta_ssid.length() > 0;
        fallback = g_settings.ap_fallback;
    }
    const bool conn = WiFi.status() == WL_CONNECTED;
    if (conn != last_conn) {
        last_conn = conn;
        state_since = now;
        if (conn) logx::printf("Joined %s: http://%s\n", WiFi.SSID().c_str(), WiFi.localIP().toString().c_str());
    }
    if (!have_sta || !fallback) {
        start_ap();
        return;
    }
    if (conn && ap_up && now - state_since >= AP_OFF_AFTER_MS) stop_ap();
    if (!conn && !ap_up && now - state_since >= AP_ON_AFTER_MS) start_ap();
}

bool join(const String& ssid, const String& pass, uint32_t timeout_ms, String& url) {
    // Wait for begin() -- a first boot spends a while formatting storage.
    for (uint32_t t0 = millis(); !ready_ && millis() - t0 < 60000;) delay(100);
    if (!ready_) return false;
    Lock l;
    if (!(WiFi.getMode() & WIFI_STA)) WiFi.mode(ap_up ? WIFI_AP_STA : WIFI_STA);
    WiFi.disconnect(false, false);
    WiFi.begin(ssid.c_str(), pass.c_str());
    for (uint32_t t0 = millis(); WiFi.status() != WL_CONNECTED && millis() - t0 < timeout_ms;) delay(100);
    if (WiFi.status() != WL_CONNECTED) {
        // Back to whatever network was saved before, if any.
        String s, p;
        {
            SettingsLock sl;
            s = g_settings.sta_ssid;
            p = g_settings.sta_pass;
        }
        WiFi.disconnect(false, false);
        if (s.length()) WiFi.begin(s.c_str(), p.c_str());
        return false;
    }
    {
        SettingsLock sl;
        g_settings.sta_ssid = ssid;
        g_settings.sta_pass = pass;
    }
    settings_save();
    configTime(0, 0, "pool.ntp.org", "time.cloudflare.com");
    last_conn = true;
    state_since = millis();
    url = "http://" + WiFi.localIP().toString() + "/";
    return true;
}

bool sta_connected() { return WiFi.status() == WL_CONNECTED; }
String sta_ip() { return sta_connected() ? WiFi.localIP().toString() : String(); }
String ap_ssid() { return ssid_; }
bool ap_on() { return ap_up; }

String status() {
    String s;
    bool have_sta;
    String want;
    {
        SettingsLock sl;
        want = g_settings.sta_ssid;
        have_sta = want.length() > 0;
    }
    if (sta_connected()) s = "On " + WiFi.SSID() + " at " + WiFi.localIP().toString();
    else if (have_sta) s = "Not connected to " + want;
    else s = "No home network set";
    s += ap_up ? String(" \xC2\xB7 hotspot ") + ssid_ + " on" : String(" \xC2\xB7 hotspot off");
    return s;
}

}  // namespace net
