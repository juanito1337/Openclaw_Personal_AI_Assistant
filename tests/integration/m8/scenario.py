#!/usr/bin/env python3
from __future__ import annotations

import imaplib
import json
import smtplib
import socket
import struct
import urllib.error
import urllib.request

HOST = "fake-services"
HTTP = f"http://{HOST}:8080"


def request(
    method: str, path: str, data: bytes | None = None, **headers: str
) -> tuple[int, bytes, dict[str, str]]:
    value = urllib.request.Request(HTTP + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(value, timeout=5) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def scan(payload: bytes) -> bytes:
    with socket.create_connection((HOST, 3310), timeout=5) as client:
        client.sendall(b"zINSTREAM\0" + struct.pack("!I", len(payload)) + payload + struct.pack("!I", 0))
        return client.recv(4096)


def main() -> None:
    imap = imaplib.IMAP4(HOST, 1143)
    assert imap.login("fixture", "fixture")[0] == "OK"
    assert imap.select("INBOX")[0] == "OK"
    status, ids = imap.search(None, "ALL")
    assert status == "OK" and ids == [b"1"]
    status, message = imap.fetch(b"1", "(RFC822)")
    assert status == "OK" and b"M8 fixture" in repr(message).encode()
    imap.logout()

    with smtplib.SMTP(HOST, 1025, timeout=5) as smtp:
        smtp.sendmail(
            "fixture-sender@example.invalid",
            ["fixture-recipient@example.invalid"],
            b"Subject: M8 outbound fixture\r\n\r\nNo production delivery.\r\n",
        )
    status, body, _ = request("GET", "/__state")
    assert status == 200 and json.loads(body)["smtp_messages"] == 1

    for collection in ("files", "addressbooks", "calendars"):
        status, body, _ = request("PROPFIND", f"/dav/{collection}/", b"<propfind/>", Depth="1")
        assert status == 207 and b"multistatus" in body

    status, event, headers = request("GET", "/dav/calendars/m8-event.ics")
    assert status == 200 and b"UID:m8-event" in event
    etag = headers["ETag"]
    updated = event.replace(b"SUMMARY:M8 Fixture", b"SUMMARY:M8 Updated")
    status, _, headers = request("PUT", "/dav/calendars/m8-event.ics", updated, **{"If-Match": etag})
    assert status == 204 and headers["ETag"] != etag
    status, _, _ = request("PUT", "/dav/calendars/m8-event.ics", event, **{"If-Match": etag})
    assert status == 412, "stale ETag must never overwrite a concurrent DAV update"

    payload = json.dumps({"model": "fixture", "prompt": "m8", "stream": False}).encode()
    status, body, _ = request("POST", "/api/generate", payload, **{"Content-Type": "application/json"})
    assert status == 200 and json.loads(body) == {"model": "fixture", "response": "m8-hermetic", "done": True}
    status, body, _ = request("GET", "/market/real-time/M8.XETRA")
    assert status == 200 and json.loads(body)["close"] == 123.45

    assert scan(b"plain fixture") == b"stream: OK\0"
    eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    assert scan(eicar) == b"stream: Eicar-Signature FOUND\0"
    print("M8 hermetic protocol scenario: OK")


if __name__ == "__main__":
    main()
