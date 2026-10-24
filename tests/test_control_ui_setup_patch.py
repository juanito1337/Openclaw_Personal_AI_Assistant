"""Hermetic build-patch integrity and rendered behavior; no real UI or network."""

from __future__ import annotations

import hashlib
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = ROOT / "docker/patch-control-ui-setup.py"
PATCH = runpy.run_path(str(PATCH_SCRIPT))
RENDERER = PATCH["RENDERER"]

RENDER_SOURCE = '''// unrelated bundle prefix
function render(t) {
  return t?.path?i`<section class="plugin-tab-embed"><iframe
    src=${t.path} title=${t.label} sandbox=${sandboxMode}
  ></iframe></section>`:i`<section role="status">Unavailable</section>`;
}
// unrelated bundle suffix
'''

# Synthetic worker with the pinned worker's existing fetch/cache behavior. The
# patch changes only its asset-branch condition; no browser or fetch is needed.
SW_SOURCE = '''const EMBEDDED_CACHE_VERSION = "2026.7.1";
const CACHE_NAME = "synthetic-fixture-" + EMBEDDED_CACHE_VERSION;
self.addEventListener("install", () => "unchanged-install");
self.addEventListener("activate", event => {
  event.waitUntil(self.clients.matchAll().then(clients => {
    for (const client of clients) client.postMessage({type:"sw-updated", version:EMBEDDED_CACHE_VERSION});
  }));
});
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (event.request.mode === "navigate") return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/rpc") ||
      url.pathname.startsWith("/plugins/")) return;
  if (url.pathname.includes("/assets/")) {
    event.respondWith(caches.match(event.request).then(cached => cached ||
      fetch(event.request).then(response => {
        if (response.ok) {
          const clone = response.clone();
          void caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      })));
  } else {
    event.respondWith(fetch(event.request).then(response => {
      if (response.ok) {
        const clone = response.clone();
        void caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
      }
      return response;
    }).catch(() => caches.match(event.request)));
  }
});
self.addEventListener("push", () => "unchanged-push");
'''


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, bytes], dict]:
    directory = tmp_path / "control-ui"
    original = {RENDERER: RENDER_SOURCE.encode(), "sw.js": SW_SOURCE.encode()}
    files = {}
    for name, data in original.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        output = data
        for needle, replacement in PATCH["REPLACEMENTS"][name]:
            output = output.replace(needle.encode(), replacement.encode(), 1)
        files[name] = {"upstream_sha256": _sha(data), "patched_sha256": _sha(output)}
    lock = {"control_ui_setup_patch": {"files": files}}
    lock_path = tmp_path / "synthetic-lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    return directory, lock_path, original, lock


def _bytes(directory: Path, names: dict) -> dict[str, bytes]:
    return {name: (directory / name).read_bytes() for name in names}


def _cli(directory: Path, lock: Path, *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PATCH_SCRIPT), str(directory), "--lock", str(lock),
         *(["--check"] if check else [])],
        capture_output=True, text=True, check=False, timeout=10,
    )


def _node(script: str, payload: dict) -> dict:
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script], input=json.dumps(payload),
        capture_output=True, text=True, check=False, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_cli_check_requires_exact_patched_bytes_and_preserves_the_rest(tmp_path: Path) -> None:
    directory, lock, original, _ = _fixture(tmp_path)
    assert _cli(directory, lock, check=True).returncode != 0
    assert _bytes(directory, original) == original
    applied = _cli(directory, lock)
    assert applied.returncode == 0, applied.stderr
    patched = _bytes(directory, original)
    for name, data in patched.items():
        restored = data
        for needle, replacement in reversed(PATCH["REPLACEMENTS"][name]):
            assert restored.count(replacement.encode()) == 1
            restored = restored.replace(replacement.encode(), needle.encode(), 1)
        assert restored == original[name]
    checked = _cli(directory, lock, check=True)
    assert checked.returncode == 0, checked.stderr
    assert _bytes(directory, original) == patched
    # Applying twice is rejected rather than silently accepting unknown input.
    assert _cli(directory, lock).returncode != 0
    assert _bytes(directory, original) == patched


@pytest.mark.parametrize("name", [RENDERER, "sw.js"])
@pytest.mark.parametrize("corruption", ["input-bytes", "input-hash", "output-hash"])
def test_every_file_is_validated_before_either_file_is_written(
    tmp_path: Path, name: str, corruption: str
) -> None:
    directory, lock_path, original, lock = _fixture(tmp_path)
    metadata = lock["control_ui_setup_patch"]["files"][name]
    target = directory / name
    if corruption == "input-bytes":
        target.write_bytes(target.read_bytes() + b"// tampered input\n")
    elif corruption == "input-hash":
        metadata["upstream_sha256"] = "0" * 64
    else:
        metadata["patched_sha256"] = "0" * 64
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    before = _bytes(directory, original)
    failed = _cli(directory, lock_path)
    assert failed.returncode != 0
    assert "Control UI" in failed.stderr
    assert _bytes(directory, original) == before


@pytest.mark.parametrize(
    "name,index",
    [(name, index) for name, replacements in PATCH["REPLACEMENTS"].items()
     for index in range(len(replacements))],
)
@pytest.mark.parametrize("duplicate", [False, True])
def test_every_insertion_requires_exactly_one_location_before_any_write(
    tmp_path: Path, name: str, index: int, duplicate: bool
) -> None:
    directory, lock_path, original, lock = _fixture(tmp_path)
    target = directory / name
    needle = PATCH["REPLACEMENTS"][name][index][0].encode()
    data = target.read_bytes()
    changed = data + needle if duplicate else data.replace(needle, b"unmatched-location", 1)
    target.write_bytes(changed)
    lock["control_ui_setup_patch"]["files"][name]["upstream_sha256"] = _sha(changed)
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    before = _bytes(directory, original)
    failed = _cli(directory, lock_path)
    assert failed.returncode != 0
    assert "location is not unique" in failed.stderr
    assert _bytes(directory, original) == before


@pytest.mark.parametrize("name", [RENDERER, "sw.js"])
def test_check_rejects_modified_output_without_repairing_either_file(tmp_path: Path, name: str) -> None:
    directory, lock, original, _ = _fixture(tmp_path)
    PATCH["patch"](directory, lock)
    target = directory / name
    target.write_bytes(target.read_bytes() + b"\n// changed after patch\n")
    before = _bytes(directory, original)
    assert _cli(directory, lock, check=True).returncode != 0
    assert _bytes(directory, original) == before


def test_unknown_file_contract_is_rejected_before_writes(tmp_path: Path) -> None:
    directory, lock_path, original, lock = _fixture(tmp_path)
    lock["control_ui_setup_patch"]["files"]["unknown.js"] = {}
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    assert _cli(directory, lock_path).returncode != 0
    assert _bytes(directory, original) == original


def test_only_exact_first_party_descriptor_receives_a_fixed_native_link(tmp_path: Path) -> None:
    directory, lock, _, _ = _fixture(tmp_path)
    PATCH["patch"](directory, lock)
    script = r'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const lit = (strings, ...values) => ({
  text: strings.reduce((text, part, i) => text + part + (values[i] ?? ''), ''), values,
});
const compile = source => new Function('i', 'sandboxMode', source + '\nreturn render;')(
  lit, 'allow-scripts',
);
const before = compile(input.original), after = compile(input.patched);
const valid = {pluginId:'personal-assistant-tools', id:'personal-assistant-nextcloud',
  path:'/plugins/personal-assistant-tools/nextcloud', label:'untrusted <script>label</script>',
  href:'https://attacker.invalid/', description:'untrusted descriptor description'};
const launch = after(valid);
assert.match(launch.text, /data-personal-assistant-setup-launch/);
assert.match(launch.text, /href="\/plugins\/personal-assistant-tools\/nextcloud\/setup"/);
assert.match(launch.text, /target="_blank"/);
assert.match(launch.text, /rel="noopener noreferrer"/);
assert.ok(!launch.text.includes('<iframe'));
assert.ok(!launch.text.includes('untrusted'));
assert.ok(!launch.text.includes('attacker.invalid'));
assert.deepEqual(launch.values, [], 'the native link contains no descriptor interpolation');
const invalid = [undefined, null, {}];
for (const key of ['pluginId', 'id', 'path']) {
  const missing = {...valid}; delete missing[key]; invalid.push(missing);
  for (const value of [undefined, null, '', 'wrong', valid[key] + '/']) {
    invalid.push({...valid, [key]:value});
  }
}
for (const descriptor of invalid) {
  const actual = after(descriptor);
  assert.deepEqual(actual, before(descriptor), 'all other descriptors retain the original renderer');
  assert.ok(!actual.text.includes('data-personal-assistant-setup-launch'));
  assert.match(actual.text, descriptor?.path ? /sandbox=allow-scripts/ : /Unavailable/);
}
console.log(JSON.stringify({rejectedDescriptors:invalid.length}));
'''
    result = _node(script, {"original": RENDER_SOURCE, "patched": (directory / RENDERER).read_text()})
    assert result["rejectedDescriptors"] == 21


def test_worker_refreshes_only_its_scoped_renderer_and_preserves_other_cache_behavior(tmp_path: Path) -> None:
    directory, lock, _, _ = _fixture(tmp_path)
    PATCH["patch"](directory, lock)
    script = r'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
async function run(source, scope, requestPath, options = {}) {
  const listeners = new Map(), calls = [], writes = [], notifications = [];
  const expectedVersion = source === input.patched ? '2026.7.1-pa-setup-launch-v1' : '2026.7.1';
  const response = (value, ok=true) => ({value, ok, clone() {return response(value,ok);}});
  const self = {location:{origin:'https://openclaw.example'}, registration:{scope},
    clients:{matchAll:async()=>[{postMessage:value=>notifications.push(value)}]},
    addEventListener:(name, callback)=>listeners.set(name,callback)};
  const caches = {
    match:async () => {calls.push('cache'); return options.miss ? undefined : response('cached');},
    open:async name => {
      assert.equal(name, 'synthetic-fixture-' + expectedVersion);
      return {put:async (_request, value)=>writes.push(value.value)};
    },
  };
  const fetch = async () => {
    calls.push('network'); if (options.offline) throw new Error('offline');
    return response('fresh', !options.badResponse);
  };
  vm.runInNewContext(source, {self,caches,fetch,URL});
  let activation;
  listeners.get('activate')({waitUntil:promise=>{activation=promise;}});
  await activation;
  assert.equal(notifications.length, 1);
  assert.equal(notifications[0].type, 'sw-updated');
  assert.equal(notifications[0].version, expectedVersion, 'existing update notice receives the new build ID');
  let pending;
  listeners.get('fetch')({request:{url:new URL(requestPath,scope).href,
    method:options.method ?? 'GET', mode:options.mode ?? 'cors'}, respondWith:p=>{pending=p;}});
  const intercepted = pending !== undefined;
  const result = await pending;
  await Promise.resolve();
  assert.equal(listeners.get('install')(), 'unchanged-install');
  assert.equal(listeners.get('push')(), 'unchanged-push');
  return {intercepted, value:result?.value, calls, writes};
}
let cases = 0;
for (const base of ['/', '/dashboard/']) {
  const scope = 'https://openclaw.example' + base;
  const renderer = base + 'assets/plugin-page-okkFqbN_.js';
  for (const path of [renderer, renderer + '?v=previous']) {
    const fresh = await run(input.patched,scope,path);
    assert.deepEqual(fresh,{intercepted:true,value:'fresh',calls:['network'],writes:['fresh']});
    assert.equal((await run(input.original,scope,path)).value,'cached');
    assert.deepEqual(await run(input.patched,scope,path,{offline:true}),
      {intercepted:true,value:'cached',calls:['network','cache'],writes:[]});
    assert.deepEqual(await run(input.patched,scope,path,{badResponse:true}),
      {intercepted:true,value:'fresh',calls:['network'],writes:[]});
    cases++;
  }
  for (const [path, options] of [
    [base+'assets/other.js',{}], [base+'assets/other.js',{miss:true}],
    ['/foreign/assets/plugin-page-okkFqbN_.js',{}], [base+'index.html',{}],
    [base+'index.html',{offline:true}], [renderer,{method:'POST'}],
    [renderer,{mode:'navigate'}], ['https://other.example'+renderer,{}],
    ['/api/status',{}], ['/rpc',{}], ['/plugins/test',{}],
  ]) {
    assert.deepEqual(await run(input.patched,scope,path,options),
      await run(input.original,scope,path,options));
    cases++;
  }
}
console.log(JSON.stringify({cases}));
'''
    result = _node(script, {"original": SW_SOURCE, "patched": (directory / "sw.js").read_text()})
    assert result["cases"] == 26
