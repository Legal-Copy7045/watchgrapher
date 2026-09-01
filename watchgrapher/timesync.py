"""
Minimal SNTP client for the watch-synchronisation tab.

No dependency: one UDP packet to port 123, read the transmit timestamp back,
correct for the round trip. Accurate to a handful of milliseconds over a
normal connection, which is well inside what you can set a watch to by hand.
"""

from __future__ import annotations

import socket
import struct
import time

# NTP counts seconds from 1900; Unix from 1970.
NTP_EPOCH_OFFSET = 2_208_988_800

# A spread of public stratum-1/2 pools. All speak plain SNTPv3.
NTP_SERVERS = [
    ("pool.ntp.org", "NTP pool (global)"),
    ("time.cloudflare.com", "Cloudflare"),
    ("time.google.com", "Google"),
    ("time.apple.com", "Apple"),
    ("uk.pool.ntp.org", "NTP pool (UK)"),
    ("north-america.pool.ntp.org", "NTP pool (North America)"),
    ("europe.pool.ntp.org", "NTP pool (Europe)"),
]


def query_ntp(host: str = "pool.ntp.org", port: int = 123, timeout: float = 2.5):
    """
    Return (offset_seconds, roundtrip_seconds).

    `offset` is how far the server's clock is ahead of this machine's:
    add it to time.time() to get true time. `roundtrip` is the network
    delay, a rough confidence bound on the offset.
    """
    packet = b"\x1b" + 47 * b"\x00"          # LI=0, VN=3, Mode=3 (client)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        t1 = time.time()
        sock.sendto(packet, (host, port))
        data, _ = sock.recvfrom(48)
        t4 = time.time()
    finally:
        sock.close()

    if len(data) < 48:
        raise OSError("short NTP reply")

    rx_s, rx_f = struct.unpack("!II", data[32:40])   # server receive time (t2)
    tx_s, tx_f = struct.unpack("!II", data[40:48])   # server transmit time (t3)
    if tx_s == 0:
        raise OSError("NTP server returned a zero timestamp")
    t2 = (rx_s - NTP_EPOCH_OFFSET) + rx_f / 2 ** 32
    t3 = (tx_s - NTP_EPOCH_OFFSET) + tx_f / 2 ** 32

    offset = ((t2 - t1) + (t3 - t4)) / 2.0
    roundtrip = (t4 - t1) - (t3 - t2)
    return offset, max(0.0, roundtrip)
