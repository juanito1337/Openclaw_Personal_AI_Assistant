#!/usr/bin/env python3
"""Apply/check integrity-pinned Control UI setup and sanitizer changes.

Build-time only: no runtime configuration, iframe permissions or authentication
changes. Unknown upstream or vendor bytes fail before any output is written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

RENDERER = "assets/plugin-page-okkFqbN_.js"
MARKDOWN = "assets/markdown-runtime-Y4RdJ3Nc.js"
VENDOR = "assets/dompurify-3.4.17.js"
MARKDOWN_START = "function Le(){"
MARKDOWN_END = "var Re=Le(),ze="
RENDER_NEEDLE = "return t?.path?i`"
RENDER_REPLACEMENT = '''return t?.pluginId===`personal-assistant-tools`&&
    t.id===`personal-assistant-nextcloud`&&t.path===`/plugins/personal-assistant-tools/nextcloud`?i`
      <section class="card" data-personal-assistant-setup-launch>
        <h2>Nextcloud einrichten</h2>
        <p>Verbinde dein Nextcloud-Konto und wähle Kalender, Aufgabenlisten,
          Kontakte, Ordner und Talk-Räume aus.</p>
        <p><a class="btn primary" href="/plugins/personal-assistant-tools/nextcloud/setup"
          target="_blank" rel="noopener noreferrer">Einrichtung in OpenClaw öffnen ↗</a></p>
        <p>Die Einrichtung öffnet sich in einem eigenen Tab und verwendet deine bestehende
          OpenClaw-Administratoranmeldung.</p>
      </section>
    `:t?.path?i`'''
SW_NEEDLE = 'if (url.pathname.includes("/assets/")) {'
SW_REPLACEMENT = '''if (
    url.pathname.includes("/assets/") &&
    url.pathname !== new URL("./assets/plugin-page-okkFqbN_.js", self.registration.scope).pathname
  ) {'''
SW_SECURITY_REPLACEMENT = '''if (url.pathname === new URL(
    "./assets/markdown-runtime-Y4RdJ3Nc.js", self.registration.scope
  ).pathname) {
    // This filename predates the sanitizer update. Never reuse a vulnerable
    // copy from a prior build, including when the network is unavailable.
    event.respondWith(
      fetch(event.request).then(async (response) => {
        if (response.ok) {
          const cache = await caches.open(CACHE_NAME);
          await cache.put(event.request, response.clone());
        }
        return response;
      }).catch(async () => (await caches.open(CACHE_NAME)).match(event.request)),
    );
    return;
  }

  // Cache-first for hashed assets; network-first for HTML/other.'''
REPLACEMENTS = {
    RENDERER: ((RENDER_NEEDLE, RENDER_REPLACEMENT),),
    "sw.js": (
        (SW_NEEDLE, SW_REPLACEMENT),
        ('const EMBEDDED_CACHE_VERSION = "2026.7.1";',
         'const EMBEDDED_CACHE_VERSION = "2026.7.1-pa-setup-launch-v2";'),
        ('// Cache-first for hashed assets; network-first for HTML/other.',
         SW_SECURITY_REPLACEMENT),
    ),
}


def replace_sanitizer(text: str) -> str:
    """Remove the old factory and bind its existing export to the vendor module."""
    if text.count(MARKDOWN_START) != 1 or text.count(MARKDOWN_END) != 1:
        raise ValueError("Control UI sanitizer location is not unique")
    start, end = text.index(MARKDOWN_START), text.index(MARKDOWN_END)
    if end <= start:
        raise ValueError("Control UI sanitizer boundaries are reversed")
    return (text[:start] + 'import Re from "./dompurify-3.4.17.js";'
            + "var ze=" + text[end + len(MARKDOWN_END):])


def patch(control_ui: Path, lock_path: Path, *, check: bool = False,
          dompurify_module: Path | None = None) -> None:
    contract = json.loads(lock_path.read_text(encoding="utf-8"))["control_ui_setup_patch"]
    files = contract["files"]
    if set(files) != {*REPLACEMENTS, MARKDOWN}:
        raise ValueError("Unexpected Control UI patch file set")
    outputs = {}
    for name in files:
        path = control_ui / name
        if path.is_symlink():
            raise ValueError(f"Control UI patch target is a symlink: {name}")
        data = path.read_bytes()
        expected = files[name]["patched_sha256" if check else "upstream_sha256"]
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"Control UI integrity mismatch: {name}")
        if check:
            continue
        text = data.decode("utf-8")
        for needle, replacement in REPLACEMENTS.get(name, ()):
            if text.count(needle) != 1:
                raise ValueError(f"Control UI patch location is not unique: {name}")
            text = text.replace(needle, replacement, 1)
        if name == MARKDOWN:
            text = replace_sanitizer(text)
        output = text.encode("utf-8")
        if hashlib.sha256(output).hexdigest() != files[name]["patched_sha256"]:
            raise ValueError(f"Control UI patch output mismatch: {name}")
        outputs[path] = output
    vendor = contract["vendor_module"]
    if vendor["path"] != VENDOR:
        raise ValueError("Unexpected Control UI vendor module")
    vendor_target = control_ui / VENDOR
    if vendor_target.is_symlink():
        raise ValueError("Control UI vendor module is a symlink")
    vendor_source = vendor_target if check else dompurify_module
    if vendor_source is None:
        raise ValueError("Control UI sanitizer module is required")
    vendor_data = vendor_source.read_bytes()
    if hashlib.sha256(vendor_data).hexdigest() != vendor["sha256"]:
        raise ValueError("Control UI vendor integrity mismatch")
    if not check:
        if vendor_target.exists():
            raise ValueError("Control UI vendor target already exists")
        outputs[vendor_target] = vendor_data
    # Validate every input/output first. This runs only while building an image;
    # a failed write aborts that build, never modifying a productive filesystem.
    for path, output in outputs.items():
        path.write_bytes(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("control_ui", type=Path)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--dompurify-module", type=Path)
    args = parser.parse_args()
    patch(args.control_ui, args.lock, check=args.check, dompurify_module=args.dompurify_module)
    print("Control UI setup integration verified" if args.check else "Control UI setup integration applied")


if __name__ == "__main__":
    main()
