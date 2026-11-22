// Minimal JSON writing into a std::string -- enough for the device's replies,
// without pulling in a library. Every kv() appends `"key":value,`;
// close_obj() swaps a trailing comma for the closing brace. Non-finite numbers
// are written as null (JSON has no NaN).
#pragma once

#include <cmath>
#include <cstdio>
#include <string>

namespace json {

inline void str(std::string& o, const char* s) {
    o += '"';
    for (const unsigned char* p = (const unsigned char*)s; p && *p; ++p) {
        unsigned char c = *p;
        if (c == '"' || c == '\\') { o += '\\'; o += (char)c; }
        else if (c == '\n') o += "\\n";
        else if (c == '\r') o += "\\r";
        else if (c == '\t') o += "\\t";
        else if (c < 0x20) {
            char b[8];
            snprintf(b, sizeof b, "\\u%04x", c);
            o += b;
        } else o += (char)c;
    }
    o += '"';
}

inline void num(std::string& o, double v, int prec) {
    if (!std::isfinite(v)) { o += "null"; return; }
    char b[32];
    snprintf(b, sizeof b, "%.*f", prec, v);
    o += b;
}

inline void key(std::string& o, const char* k) { str(o, k); o += ':'; }
inline void kv(std::string& o, const char* k, const char* v) { key(o, k); str(o, v); o += ','; }
inline void kv(std::string& o, const char* k, bool v) { key(o, k); o += v ? "true" : "false"; o += ','; }
inline void kv(std::string& o, const char* k, int v) { key(o, k); o += std::to_string(v); o += ','; }
inline void kv(std::string& o, const char* k, long long v) { key(o, k); o += std::to_string(v); o += ','; }
inline void kv(std::string& o, const char* k, double v, int prec) { key(o, k); num(o, v, prec); o += ','; }

inline void close_obj(std::string& o) {
    if (!o.empty() && o.back() == ',') o.back() = '}';
    else o += '}';
}

}  // namespace json
