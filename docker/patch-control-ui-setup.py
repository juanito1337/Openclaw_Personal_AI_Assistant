#!/usr/bin/env python3
"""Apply/check the two integrity-pinned Control UI setup integration changes.

Build-time only: no runtime configuration, iframe permissions or authentication
changes. Unknown upstream bytes fail before either output is written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

RENDERER = "assets/plugin-page-okkFqbN_.js"
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
REPLACEMENTS = {
    RENDERER: ((RENDER_NEEDLE, RENDER_REPLACEMENT),),
    "sw.js": (
        (SW_NEEDLE, SW_REPLACEMENT),
        ('const EMBEDDED_CACHE_VERSION = "2026.7.1";',
         'const EMBEDDED_CACHE_VERSION = "2026.7.1-pa-setup-launch-v1";'),
    ),
}


def patch(control_ui: Path, lock_path: Path, *, check: bool = False) -> None:
    contract = json.loads(lock_path.read_text(encoding="utf-8"))["control_ui_setup_patch"]
    files = contract["files"]
    if set(files) != set(REPLACEMENTS):
        raise ValueError("Unexpected Control UI patch file set")
    outputs = {}
    for name, replacements in REPLACEMENTS.items():
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
        for needle, replacement in replacements:
            if text.count(needle) != 1:
                raise ValueError(f"Control UI patch location is not unique: {name}")
            text = text.replace(needle, replacement, 1)
        output = text.encode("utf-8")
        if hashlib.sha256(output).hexdigest() != files[name]["patched_sha256"]:
            raise ValueError(f"Control UI patch output mismatch: {name}")
        outputs[path] = output
    # Validate every input/output first. This runs only while building an image;
    # a failed write aborts that build, never modifying a productive filesystem.
    for path, output in outputs.items():
        path.write_bytes(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("control_ui", type=Path)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    patch(args.control_ui, args.lock, check=args.check)
    print("Control UI setup integration verified" if args.check else "Control UI setup integration applied")


if __name__ == "__main__":
    main()
