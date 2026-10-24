#!/usr/bin/env python3
"""Isolated real-browser launcher regression; Python stdlib, no production access.

Root runs this with an available Firefox/geckodriver. The normal assertion is
that an actual click opens the real setup document outside the iframe sandbox.
Use --expect blocked to record the known failure of the original launcher.
No synthetic click, popup preference override, copied browser profile or auth
token is used. The sandbox values are read from the extracted pinned core.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "/plugins/personal-assistant-tools/nextcloud"
ELEMENT = "element-6066-11e4-a52e-4f735466cecf"
MARKDOWN_RUNTIME = "markdown-runtime-Y4RdJ3Nc.js"


def single_renderer(control_ui: Path) -> Path:
    pages = list((control_ui / "assets").glob("plugin-page-*.js"))
    if len(pages) != 1:
        raise RuntimeError("Expected exactly one public plugin-page asset")
    return pages[0]


def sandbox_from_core(control_ui: Path, mode: str) -> str:
    page = single_renderer(control_ui).read_text()
    dependency = re.search(r'from"\./(tool-display-[A-Za-z0-9_-]+\.js)"', page)
    if dependency is None:
        raise RuntimeError("Cannot resolve pinned sandbox helper")
    helper = (control_ui / "assets" / dependency[1]).read_text()
    if "sandbox=${d(e.config.current.embedSandboxMode)}" not in page:
        raise RuntimeError("Pinned plugin-page sandbox contract changed; inspect it first")
    match = re.search(
        r"function (\w+)\(e\)\{switch\(e\)\{case`strict`:return`([^`]*)`;"
        r"case`trusted`:return`([^`]*)`;default:return`([^`]*)`\}\}",
        helper,
    )
    if match is None or f"{match.group(1)} as r" not in helper:
        raise RuntimeError("Cannot verify pinned sandbox helper export")
    return {"strict": match[2], "trusted": match[3], "scripts": match[4]}[mode]


class WebDriver:
    def __init__(self, base: str):
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
            raise ValueError("WebDriver endpoint must be an explicit loopback HTTP port")
        self.base = base.rstrip("/")
        self.session = ""
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method: str, path: str, value: object = None):
        body = None if value is None else json.dumps(value).encode()
        request = urllib.request.Request(
            self.base + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        protocol_error = False
        try:
            with self.opener.open(request, timeout=25) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            protocol_error = True
            result = json.loads(error.read(65536))
        value = result.get("value")
        # execute/sync may legitimately return an object containing an "error"
        # field. Only a non-success HTTP response is a WebDriver protocol error.
        if protocol_error:
            details = value if isinstance(value, dict) else {}
            raise RuntimeError(
                f"WebDriver {details.get('error', 'HTTP error')}: {details.get('message', '')}"
            )
        return value

    def call(self, method: str, path: str, value: object = None):
        return self.request(method, f"/session/{self.session}{path}", value)

    def element(self, selector: str):
        return self.call("POST", "/element", {"using": "css selector", "value": selector})

    def evaluate(self, script: str):
        return self.call("POST", "/execute/sync", {"script": script, "args": []})

    def evaluate_async(self, script: str):
        result = self.call(
            "POST",
            "/execute/async",
            {
                "script": (
                    "const done = arguments[arguments.length - 1];\n"
                    "(async () => {\n" + script + "\n})().then(done, error => done({error: String(error)}));"
                ),
                "args": [],
            },
        )
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError(result["error"])
        return result


def fixture_handler(assets: Path, current: dict[str, Path], mode: str, launcher: Path):
    allowed = {
        BASE: (launcher, "text/html; charset=utf-8"),
        BASE + "/setup": (assets / "setup-ui.html", "text/html; charset=utf-8"),
        **{
            BASE + "/" + name: (assets / name, mime)
            for name, mime in (
                ("setup-ui.css", "text/css; charset=utf-8"),
                ("setup-ui-browser.js", "text/javascript; charset=utf-8"),
                ("setup-ui-auth.js", "text/javascript; charset=utf-8"),
                ("setup-launch.js", "text/javascript; charset=utf-8"),
            )
        },
    }

    bootstrap = (
        'sessionStorage.setItem("setup-fixture-loads", '
        'String(Number(sessionStorage.getItem("setup-fixture-loads") || 0) + 1));\n'
        "try {\n"
        'window.fixtureStage = "import-renderer";\n'
        "await import(" + json.dumps("/assets/" + current["plugin_page"].name) + ");\n"
        'window.fixtureStage = "create-renderer";\n'
        'const page = document.createElement("openclaw-plugin-page");\n'
        'page.pluginId = "personal-assistant-tools";\n'
        'page.tabId = "personal-assistant-nextcloud";\n'
        "page.context = { config: { current: { embedSandboxMode: " + json.dumps(mode) + " } },\n"
        " gateway: { snapshot: { hello: { controlUiTabs: [{\n"
        ' pluginId: page.pluginId, id: page.tabId, label: "Nextcloud einrichten",\n'
        ' description: "Isolated test descriptor", path: ' + json.dumps(BASE) + ",\n"
        ' requiredScopes: ["operator.admin"] }] } }, subscribe: () => () => {} } };\n'
        "document.body.append(page);\n"
        'window.fixtureStage = "await-render";\n'
        "await page.updateComplete;\n"
        'window.fixtureStage = "ready";\n'
        "window.fixtureReady = true;\n"
        "} catch(error) { window.fixtureError = String(error); }\n"
    ).encode()

    class Handler(BaseHTTPRequestHandler):
        requests: list[dict[str, str | int]] = []

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path in ("/", "/cache-fixture"):
                body = b'<!doctype html><meta charset="utf-8"><title>Isolated cache fixture</title>'
                mime = "text/html; charset=utf-8"
                csp = "default-src 'none'; script-src 'self'; worker-src 'self'; connect-src 'self'"
            elif path == "/sw.js":
                body = (current["control_ui"] / "sw.js").read_bytes()
                mime = "text/javascript; charset=utf-8"
                csp = "default-src 'self'"
            elif path == "/fixture":
                body = (
                    b'<!doctype html><meta charset="utf-8"><title>Isolated plugin host</title>'
                    b"<style>iframe{width:100%;height:850px;border:0}</style>"
                    b'<body><script src="/fixture-diagnostics.js"></script>'
                    b'<script type="module" src="/fixture-bootstrap.js"></script>'
                )
                mime = "text/html; charset=utf-8"
                csp = (
                    "default-src 'none'; script-src 'self'; frame-src 'self'; "
                    "style-src 'self' 'unsafe-inline'"
                )
            elif path == "/fixture-diagnostics.js":
                body = b"""
                    window.fixtureDiagnostics = [];
                    const record = entry => {
                      if (window.fixtureDiagnostics.length < 12) window.fixtureDiagnostics.push(entry);
                    };
                    window.addEventListener('error', event => record({type: 'error',
                      message: String(event.message || 'resource load failed').slice(0, 500),
                      source: String(event.filename || event.target?.src || '').slice(0, 250)}), true);
                    window.addEventListener('unhandledrejection', event => record({type: 'rejection',
                      message: String(event.reason).slice(0, 500)}));
                    window.addEventListener('securitypolicyviolation', event => record({type: 'csp',
                      directive: event.violatedDirective, uri: String(event.blockedURI).slice(0, 250)}));
                """
                mime = "text/javascript; charset=utf-8"
                csp = "default-src 'none'"
            elif path == "/fixture-bootstrap.js":
                body = bootstrap
                mime = "text/javascript; charset=utf-8"
                csp = "default-src 'none'"
            elif path.startswith("/assets/") and re.fullmatch(r"[A-Za-z0-9_.-]+", path[8:]):
                source = (
                    current["plugin_page"]
                    if path == "/assets/" + current["plugin_page"].name
                    else current["control_ui"] / path[1:]
                )
                if not source.is_file():
                    self.send_error(404)
                    return
                body = source.read_bytes()
                mime = (
                    "text/javascript; charset=utf-8" if source.suffix == ".js" else "text/css; charset=utf-8"
                )
                csp = "default-src 'none'"
            elif path in allowed and allowed[path][0].is_file():
                source, mime = allowed[path]
                body = source.read_bytes().replace(b"__CONTROL_UI_BASE_PATH__", b"")
                csp = (
                    "default-src 'none'; script-src 'self'; style-src 'self'; "
                    "connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; "
                    "frame-ancestors " + ("'self'" if path == BASE else "'none'")
                )
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", csp)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

        def log_request(self, code="-", size="-"):
            self.requests.append({"path": urllib.parse.urlsplit(self.path).path, "status": code})
            del self.requests[:-80]

    return Handler


def check_cache_upgrade(
    driver: WebDriver, origin: str, current: dict[str, Path], patched: Path, *, dompurify: bool = False
):
    original = current["control_ui"]
    before = single_renderer(original)
    after = single_renderer(patched)
    assert before.name == after.name, "Regression requires the same renderer URL before and after update"
    old_sha = hashlib.sha256(before.read_bytes()).hexdigest()
    new_sha = hashlib.sha256(after.read_bytes()).hexdigest()
    assert old_sha != new_sha, "Renderer fixture bytes must differ"
    assert (original / "sw.js").read_bytes() != (patched / "sw.js").read_bytes(), "SW must change"
    renderer_url = "/assets/" + before.name
    js_url = json.dumps(renderer_url)
    hash_function = """
        async function sha(response) {
          const digest = await crypto.subtle.digest('SHA-256', await response.arrayBuffer());
          return Array.from(new Uint8Array(digest), n => n.toString(16).padStart(2, '0')).join('');
        }
        const delay = () => new Promise(resolve => setTimeout(resolve, 50));
    """
    driver.call("POST", "/timeouts", {"script": 20000, "pageLoad": 15000})
    driver.call("POST", "/url", {"url": origin + "/cache-fixture"})
    initial = driver.evaluate_async(
        hash_function
        + """
        await navigator.serviceWorker.register('/sw.js?v=2026.7.1', {scope: '/', updateViaCache: 'none'});
        await navigator.serviceWorker.ready;
        const deadline = performance.now() + 10000;
        while (!navigator.serviceWorker.controller) {
          if (performance.now() > deadline) throw Error('Original SW did not claim fixture');
          await delay();
        }
        const fetched = await sha(await fetch(URL));
        let cached;
        while (!(cached = await caches.match(URL))) {
          if (performance.now() > deadline) throw Error('Original renderer was not cached');
          await delay();
        }
        return {fetched, cached: await sha(cached)};
    """.replace("URL", js_url)
    )
    assert initial == {"fetched": old_sha, "cached": old_sha}, initial
    markdown_url = "/assets/" + MARKDOWN_RUNTIME
    markdown_old_sha = None
    markdown_new_sha = None
    if dompurify:
        markdown_old_sha = hashlib.sha256((original / "assets" / MARKDOWN_RUNTIME).read_bytes()).hexdigest()
        markdown_new_sha = hashlib.sha256((patched / "assets" / MARKDOWN_RUNTIME).read_bytes()).hexdigest()
        assert markdown_old_sha != markdown_new_sha, "DOMPurify runtime fixture bytes must differ"
        markdown_initial = driver.evaluate_async(
            hash_function
            + "const url = "
            + json.dumps(markdown_url)
            + ";"
            + """
            const fetched = await sha(await fetch(url));
            const deadline = performance.now() + 5000;
            let cached;
            while (!(cached = await caches.match(url))) {
              if (performance.now() > deadline) throw Error('Original markdown runtime was not cached');
              await delay();
            }
            return {fetched, cached: await sha(cached)};
            """
        )
        assert markdown_initial == {"fetched": markdown_old_sha, "cached": markdown_old_sha}, markdown_initial
    current.update(control_ui=patched, plugin_page=after)
    stale = driver.evaluate_async(hash_function + f"return await sha(await fetch({js_url}));")
    assert stale == old_sha, "Negative control: old active SW must still return its old cached renderer"
    if dompurify:
        markdown_stale = driver.evaluate_async(
            hash_function + f"return await sha(await fetch({json.dumps(markdown_url)}));"
        )
        assert markdown_stale == markdown_old_sha, "Old SW must reproduce the stale vulnerable markdown chunk"
    updated = driver.evaluate_async(
        hash_function
        + """
        const oldController = navigator.serviceWorker.controller;
        const registration = await navigator.serviceWorker.getRegistration();
        await registration.update();
        const deadline = performance.now() + 12000;
        while (navigator.serviceWorker.controller === oldController ||
               registration.active?.state !== 'activated') {
          if (performance.now() > deadline) throw Error('Patched SW did not activate and take control');
          await delay();
        }
        return {fetched: await sha(await fetch(URL)), state: registration.active.state};
    """.replace("URL", js_url)
    )
    assert updated == {"fetched": new_sha, "state": "activated"}, updated
    result = {
        "original_cached_sha256": old_sha,
        "pre_update_stale_sha256": stale,
        "after_update_fetched_sha256": updated["fetched"],
        "patched_sw_activated": True,
    }
    if dompurify:
        markdown_updated = driver.evaluate_async(
            hash_function + f"return await sha(await fetch({json.dumps(markdown_url)}));"
        )
        assert markdown_updated == markdown_new_sha, "Patched SW still serves the vulnerable markdown chunk"
        result["markdown_runtime"] = {
            "original_cached_sha256": markdown_old_sha,
            "pre_update_stale_sha256": markdown_stale,
            "after_update_fetched_sha256": markdown_updated,
        }
    return result


def check_dompurify(driver: WebDriver):
    """Exercise the actual browser sanitizer and consumers, never evaluate a payload.

    Shadow DOM case: https://github.com/cure53/DOMPurify/security/advisories/GHSA-cg2g-xh64-v4q7
    Inert template fragments are separated by a forbidden object element, which
    sanitization removes. SAFE_FOR_TEMPLATES must scrub their merged expression.
    """
    driver.call("POST", "/timeouts", {"script": 20000})
    return driver.evaluate_async(r"""
        const require = (condition, label) => { if (!condition) throw Error(label); };
        const runtime = await import('/assets/markdown-runtime-Y4RdJ3Nc.js');
        const purify = runtime.v;
        require(purify?.version === '3.4.17', `Unexpected DOMPurify version: ${purify?.version}`);
        require(purify.isSupported === true && typeof purify.sanitize === 'function',
                'Real browser DOMPurify instance is unavailable');

        function fragment(value) {
          const template = document.createElement('template');
          template.innerHTML = value;
          return template.content;
        }
        function safeTree(root) {
          require(!root.querySelector('script,object,iframe'), 'Forbidden executable element survived');
          for (const element of root.querySelectorAll('*')) {
            for (const attribute of element.attributes) {
              require(!/^on/i.test(attribute.name), 'Executable event attribute survived');
              if (['href', 'src', 'xlink:href'].includes(attribute.name)) {
                require(!/^\s*javascript:/i.test(attribute.value), 'Executable URL survived');
              }
            }
          }
        }
        const dirty = '<p title="fixture">Hello <strong>safe</strong>' +
          '<img src="fixture-image" onerror="void 0">' +
          '<a href="javascript:void 0" onclick="void 0">link</a>' +
          '<script>void 0</script></p>';
        const clean = fragment(purify.sanitize(dirty));
        safeTree(clean);
        require(clean.querySelector('strong')?.textContent === 'safe', 'Safe HTML formatting was lost');
        require(clean.querySelector('p')?.getAttribute('title') === 'fixture',
                'Safe HTML attribute was lost');

        const markdown = await import('/assets/markdown-DnCs7f3J.js');
        require(typeof markdown.a === 'function', 'Actual markdown renderer export is unavailable');
        const rendered = fragment(markdown.a('**bold**\n\n[safe](https://example.invalid/path)\n\n' +
          '<img src="fixture-image" onerror="void 0">'));
        safeTree(rendered);
        require(rendered.querySelector('strong')?.textContent === 'bold',
                'Safe Markdown formatting was lost');
        require(rendered.querySelector('a')?.getAttribute('href') === 'https://example.invalid/path',
                'Safe Markdown link was lost');

        const dialects = [
          ['template-literal', ['$', '{fixture}']],
          ['mustache', ['{', '{fixture}', '}']],
          ['erb', ['<', '%fixture%', '>']],
        ];
        const shadowChecks = [];
        for (const [dialect, parts] of dialects) {
          for (const nested of [false, true]) {
            for (const safe of [false, true]) {
              const host = document.createElement('div');
              let shadow = host.attachShadow({mode: 'open'});
              if (nested) {
                const nestedHost = document.createElement('div');
                shadow.append(nestedHost);
                shadow = nestedHost.attachShadow({mode: 'open'});
              }
              const retained = document.createElement('span');
              retained.textContent = 'retained shadow fixture';
              shadow.append(retained);
              parts.forEach((part, index) => {
                if (index) shadow.append(document.createElement('object'));
                shadow.append(document.createTextNode(part));
              });
              const expression = parts.join('');
              require(shadow.querySelector('object') && shadow.textContent.includes(expression),
                      'Split-shadow regression fixture did not exercise the advisory');
              const sanitized = purify.sanitize(host, {IN_PLACE: true, SAFE_FOR_TEMPLATES: safe});
              require(sanitized === host && host.shadowRoot, 'In-place shadow DOM mode was not preserved');
              require(!shadow.querySelector('object'), 'Forbidden shadow separator survived sanitization');
              require(shadow.querySelector('span')?.textContent === 'retained shadow fixture',
                      'Benign shadow DOM content was discarded');
              shadow.normalize();
              require(shadow.textContent.includes(expression) === !safe,
                      `Shadow template ${dialect} nested=${nested} safe=${safe} failed`);
            }
            shadowChecks.push({dialect, nested, safe_for_templates_scrubbed: true,
                               unsafe_mode_negative_control_preserved: true});
          }
        }
        await import('/assets/chat-page-k-xExnnV.js');
        await import('/assets/agents-page-CIVPaqZe.js');
        require(customElements.get('openclaw-chat-page'), 'Chat consumer did not register');
        require(customElements.get('openclaw-agents-page'), 'Agents consumer did not register');
        return {version: purify.version, html_sanitization: true, markdown_sanitization: true,
                shadow_checks: shadowChecks,
                consumer_imports: ['markdown-DnCs7f3J.js', 'chat-page-k-xExnnV.js',
                                   'agents-page-CIVPaqZe.js']};
    """)


def check(driver: WebDriver, origin: str, selector: str, expected: str, timeout: float, sandbox: str):
    driver.call("POST", "/timeouts", {"implicit": 5000, "pageLoad": 15000, "script": 5000})
    driver.call("POST", "/url", {"url": origin + "/fixture"})
    # The real index module installs the real sw-updated listener. A patched
    # worker may cause its one expected reload, but must not create a reload loop.
    deadline = time.monotonic() + 20
    stable_since = time.monotonic()
    native_loads = None
    readiness_history = []
    while True:
        try:
            readiness = driver.evaluate("""
                return {ready: window.fixtureReady === true, error: window.fixtureError || '',
                        loads: Number(sessionStorage.getItem('setup-fixture-loads') || 0)};
            """)
        except RuntimeError:
            readiness = {"ready": False, "error": "", "loads": None}
        if not readiness_history or readiness != readiness_history[-1]:
            readiness_history.append(readiness)
            del readiness_history[:-12]
        if readiness["error"]:
            raise RuntimeError(f"Native renderer failed: {readiness['error']}")
        if not readiness["ready"] or readiness["loads"] != native_loads:
            native_loads = readiness["loads"]
            stable_since = time.monotonic()
        elif time.monotonic() - stable_since >= 2:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Native renderer did not stabilize: " + json.dumps(readiness_history))
        time.sleep(0.1)
    driver.evaluate("localStorage.setItem('setup-browser-fixture', 'synthetic-only')")
    handles = driver.call("GET", "/window/handles")
    frame_count = driver.evaluate("return document.querySelectorAll('iframe').length")
    if frame_count:
        assert frame_count == 1, "Unexpected iframe topology"
        actual_sandbox = driver.evaluate("return document.querySelector('iframe').getAttribute('sandbox')")
        assert actual_sandbox == sandbox, (actual_sandbox, sandbox)
        driver.call("POST", "/frame", {"id": driver.element("iframe")})
    link = driver.element(selector)
    target = driver.call("GET", f"/element/{link[ELEMENT]}/attribute/target")
    # WebDriver Element Click, never executeScript('.click()'): user activation
    # and the engine's sandbox restrictions must remain part of this test.
    driver.call("POST", f"/element/{link[ELEMENT]}/click", {})
    driver.call("POST", "/frame", {"id": None})
    deadline = time.monotonic() + timeout
    opened = False
    new_handles = []
    while time.monotonic() < deadline:
        current = driver.call("GET", "/window/handles")
        new_handles = [handle for handle in current if handle not in handles]
        if new_handles:
            if len(new_handles) != 1:
                raise AssertionError(f"Expected one new tab, got {len(new_handles)}")
            driver.call("POST", "/window", {"handle": new_handles[0]})
        if driver.call("GET", "/url") == origin + BASE + "/setup":
            opened = True
            break
        time.sleep(0.1)
    result = {
        "target": target,
        "opened": opened,
        "new_tabs": len(new_handles),
        "host_iframes": frame_count,
        "native_renderer_loads": native_loads,
        "native_renderer_stable_seconds": 2,
    }
    if expected == "blocked":
        assert not opened and not new_handles, "Expected original sandbox failure was not reproduced"
        assert driver.call("GET", "/url") == origin + "/fixture"
        result["known_failure_reproduced"] = True
        return result
    assert opened, "Real user click did not open the setup page outside the sandbox"
    assert len(new_handles) == 1, "Setup must open in exactly one new tab"
    driver.element("#connect")  # Real setup UI, not a navigation-only dummy page.
    proof = driver.evaluate("""
        let storage = false;
        try { storage = localStorage.getItem('setup-browser-fixture') === 'synthetic-only'; }
        catch (_) {}
        return { topLevel: window.top === window.self, storage,
                 openerNull: window.opener === null, origin: location.origin };
    """)
    assert proof == {"topLevel": True, "storage": True, "openerNull": True, "origin": origin}, proof
    result["setup_context"] = proof
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, default=ROOT / "docker/openclaw-personal-assistant-plugin")
    parser.add_argument("--launcher", type=Path)
    parser.add_argument(
        "--control-ui",
        type=Path,
        required=True,
        help="Extracted public dist/control-ui directory, original or patched",
    )
    parser.add_argument("--plugin-page", type=Path, help="Optional original/patched renderer file override")
    parser.add_argument(
        "--check-cache-upgrade",
        type=Path,
        metavar="ORIGINAL_CONTROL_UI",
        help="Warm the original SW cache, update to --control-ui, verify new renderer bytes",
    )
    parser.add_argument("--sandbox-mode", choices=("scripts", "strict", "trusted"), default="scripts")
    parser.add_argument(
        "--check-dompurify",
        action="store_true",
        help="Test actual DOMPurify 3.4.17, Markdown consumers and ShadowRoot template defense",
    )
    parser.add_argument("--expect", choices=("opened", "blocked"), default="opened")
    parser.add_argument("--selector", default=f'a[href="{BASE}/setup"][target="_blank"]')
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--webdriver-url", help="Existing loopback geckodriver; otherwise launch a fresh one")
    parser.add_argument("--geckodriver", default=shutil.which("geckodriver") or "/snap/bin/geckodriver")
    args = parser.parse_args()
    launcher = args.launcher or args.assets / "setup-launch.html"
    sandbox = sandbox_from_core(args.control_ui, args.sandbox_mode)
    plugin_page = args.plugin_page or single_renderer(args.control_ui)
    if args.check_cache_upgrade and (args.plugin_page or args.expect != "opened"):
        parser.error("--check-cache-upgrade requires --expect opened and an unmodified renderer filename")
    initial_ui = args.check_cache_upgrade or args.control_ui
    current = {
        "control_ui": initial_ui,
        "plugin_page": single_renderer(initial_ui) if args.check_cache_upgrade else plugin_page,
    }
    (ROOT / "build").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="setup-launch-browser-", dir=ROOT / "build") as folder:
        temporary = Path(folder)
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            fixture_handler(
                args.assets,
                current,
                args.sandbox_mode,
                launcher,
            ),
        )
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        process = None
        driver = None
        cache_result = None
        try:
            with (temporary / "geckodriver.log").open("w") as logfile:
                endpoint = args.webdriver_url
                if not endpoint:
                    with socket.socket() as probe:
                        probe.bind(("127.0.0.1", 0))
                        port = probe.getsockname()[1]
                    process = subprocess.Popen(
                        [
                            args.geckodriver,
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(port),
                            "--profile-root",
                            str(temporary),
                        ],
                        stdout=logfile,
                        stderr=subprocess.STDOUT,
                    )
                    endpoint = f"http://127.0.0.1:{port}"
                driver = WebDriver(endpoint)
                deadline = time.monotonic() + 15
                while True:
                    try:
                        driver.request("GET", "/status")
                        break
                    except (OSError, RuntimeError):
                        if time.monotonic() >= deadline or process and process.poll() is not None:
                            raise RuntimeError("geckodriver did not become ready") from None
                        time.sleep(0.1)
                created = driver.request(
                    "POST",
                    "/session",
                    {
                        "capabilities": {
                            "alwaysMatch": {
                                "browserName": "firefox",
                                "moz:firefoxOptions": {"args": ["-headless"]},
                            }
                        }
                    },
                )
                driver.session = created["sessionId"]
                origin = f"http://127.0.0.1:{server.server_port}"
                if args.check_cache_upgrade:
                    cache_result = check_cache_upgrade(
                        driver, origin, current, args.control_ui, dompurify=args.check_dompurify
                    )
                result = check(driver, origin, args.selector, args.expect, args.timeout, sandbox)
                if args.check_dompurify:
                    result["dompurify"] = check_dompurify(driver)
                if cache_result:
                    result["cache_upgrade"] = cache_result
                result.update(
                    {
                        "sandbox_mode": args.sandbox_mode,
                        "sandbox": sandbox,
                        "launcher_sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
                        "native_renderer_sha256": hashlib.sha256(plugin_page.read_bytes()).hexdigest(),
                        "browser_version": created["capabilities"].get("browserVersion"),
                    }
                )
                print(json.dumps(result, indent=2))
        except Exception:
            diagnostics = {
                "fixture_requests": server.RequestHandlerClass.requests,
                "cache_upgrade": cache_result,
            }
            if driver and driver.session:
                with contextlib.suppress(OSError, RuntimeError):
                    diagnostics["browser"] = driver.evaluate("""
                        return {url: location.href, title: document.title,
                          ready: window.fixtureReady === true, stage: window.fixtureStage || '',
                          error: window.fixtureError || '', documentState: document.readyState,
                          loads: Number(sessionStorage.getItem('setup-fixture-loads') || 0),
                          scripts: Array.from(document.scripts, item => item.src).slice(0, 20),
                          errors: window.fixtureDiagnostics || [],
                          resources: performance.getEntriesByType('resource').slice(-20)
                            .map(item => item.name)};
                    """)
            print(json.dumps({"failure_diagnostics": diagnostics}, indent=2), file=sys.stderr)
            raise
        finally:
            if driver and driver.session:
                with contextlib.suppress(OSError, RuntimeError):
                    driver.request("DELETE", f"/session/{driver.session}")
            if process:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
