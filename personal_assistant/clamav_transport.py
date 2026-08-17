"""Bounded TLS transport preflight for the ClamAV maintenance image."""

from __future__ import annotations

import ctypes
import json
import sys
from collections.abc import Callable
from typing import Any

LIBCURL = "libcurl.so.4"
PROBE_URL = "https://database.clamav.net/"

# Stable libcurl easy-option ABI values from curl/curl.h. Keeping the probe on
# libcurl exercises the same TLS implementation that freshclam loads.
CURLOPT_URL = 10002
CURLOPT_USERAGENT = 10018
CURLOPT_NOBODY = 44
CURLOPT_TIMEOUT = 13
CURLOPT_CONNECTTIMEOUT = 78
CURLOPT_NOSIGNAL = 99
CURLOPT_IPRESOLVE = 113
CURL_IPRESOLVE_V4 = 1
CURL_GLOBAL_DEFAULT = 3


def probe_tls_transport(
    *,
    loader: Callable[[str], Any] = ctypes.CDLL,
    url: str = PROBE_URL,
    timeout_seconds: int = 15,
) -> dict[str, object]:
    """Load libcurl and complete one certificate-verified HTTPS HEAD request."""

    if not url.startswith("https://"):
        raise ValueError("ClamAV-Transportprobe verlangt eine HTTPS-URL")
    if timeout_seconds < 1 or timeout_seconds > 60:
        raise ValueError("ClamAV-Transportprobe braucht ein Timeout von 1 bis 60 Sekunden")

    try:
        curl = loader(LIBCURL)
    except OSError as exc:
        raise RuntimeError(f"ClamAV-libcurl konnte nicht geladen werden: {exc}") from exc

    curl.curl_global_init.argtypes = [ctypes.c_long]
    curl.curl_global_init.restype = ctypes.c_int
    curl.curl_global_cleanup.argtypes = []
    curl.curl_global_cleanup.restype = None
    curl.curl_easy_init.argtypes = []
    curl.curl_easy_init.restype = ctypes.c_void_p
    curl.curl_easy_perform.argtypes = [ctypes.c_void_p]
    curl.curl_easy_perform.restype = ctypes.c_int
    curl.curl_easy_cleanup.argtypes = [ctypes.c_void_p]
    curl.curl_easy_cleanup.restype = None
    curl.curl_easy_setopt.restype = ctypes.c_int

    global_ready = False
    handle: ctypes.c_void_p | None = None
    retained_strings: list[bytes] = []
    try:
        code = int(curl.curl_global_init(CURL_GLOBAL_DEFAULT))
        if code != 0:
            raise RuntimeError(f"ClamAV-libcurl Initialisierung fehlgeschlagen: Code {code}")
        global_ready = True
        raw_handle = curl.curl_easy_init()
        if not raw_handle:
            raise RuntimeError("ClamAV-libcurl lieferte keinen Easy-Handle")
        handle = ctypes.c_void_p(raw_handle)

        def set_string(option: int, value: str) -> None:
            encoded = value.encode("ascii")
            retained_strings.append(encoded)
            result = curl.curl_easy_setopt(handle, ctypes.c_int(option), ctypes.c_char_p(encoded))
            if result != 0:
                raise RuntimeError(f"ClamAV-libcurl Option {option} fehlgeschlagen: Code {result}")

        def set_long(option: int, value: int) -> None:
            result = curl.curl_easy_setopt(handle, ctypes.c_int(option), ctypes.c_long(value))
            if result != 0:
                raise RuntimeError(f"ClamAV-libcurl Option {option} fehlgeschlagen: Code {result}")

        set_string(CURLOPT_URL, url)
        set_string(CURLOPT_USERAGENT, "openclaw-maintenance-preflight/1")
        set_long(CURLOPT_NOBODY, 1)
        set_long(CURLOPT_CONNECTTIMEOUT, min(timeout_seconds, 10))
        set_long(CURLOPT_TIMEOUT, timeout_seconds)
        set_long(CURLOPT_NOSIGNAL, 1)
        set_long(CURLOPT_IPRESOLVE, CURL_IPRESOLVE_V4)
        code = int(curl.curl_easy_perform(handle))
        if code != 0:
            raise RuntimeError(f"ClamAV-libcurl TLS-Handshake fehlgeschlagen: Code {code}")
    except OSError as exc:
        raise RuntimeError(f"ClamAV-libcurl Laufzeitfehler: {exc}") from exc
    finally:
        if handle is not None:
            curl.curl_easy_cleanup(handle)
        if global_ready:
            curl.curl_global_cleanup()

    return {
        "ok": True,
        "library": LIBCURL,
        "transport": "https",
        "endpoint": "database.clamav.net",
        "timeout_seconds": timeout_seconds,
    }


def main() -> int:
    try:
        report = probe_tls_transport()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ClamAV-Transport nicht bereit: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
