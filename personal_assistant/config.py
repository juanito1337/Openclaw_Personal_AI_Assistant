from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(os.environ.get("OPENCLAW_WORKSPACE") or Path(__file__).resolve().parents[1]).expanduser().resolve()
DEFAULT_CONFIG = WORKSPACE_ROOT / "personal_assistant/config.toml"
DEFAULT_RESOURCES = WORKSPACE_ROOT / "personal_assistant/resources.toml"
DEFAULT_POLICIES = WORKSPACE_ROOT / "personal_assistant/policies.toml"
DEFAULT_SECRETS = Path("~/.config/personal-assistant/secrets.env").expanduser()
CONTAINER_WORKER_ROLES = frozenset(
    {"sync-worker", "supervisor-worker", "portfolio-worker", "monitor-worker"}
)


def _resolve(value: str | Path, base: Path = WORKSPACE_ROOT) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    return value if isinstance(value, dict) else {}


@dataclass(slots=True)
class RuntimeConfig:
    database: Path = field(default_factory=lambda: WORKSPACE_ROOT / "personal_assistant/data/assistant.sqlite3")
    log_file: Path = field(default_factory=lambda: WORKSPACE_ROOT / "personal_assistant/data/assistant.log")
    resources_file: Path = field(default_factory=lambda: DEFAULT_RESOURCES)
    policies_file: Path = field(default_factory=lambda: DEFAULT_POLICIES)
    secrets_file: Path = field(default_factory=lambda: DEFAULT_SECRETS)
    command_timeout_seconds: int = 120


@dataclass(slots=True)
class SearchConfig:
    enabled: bool = True
    chunk_chars: int = 3000
    chunk_overlap_chars: int = 300
    max_file_bytes: int = 25_000_000
    max_text_chars: int = 500_000
    default_limit: int = 20
    nextcloud_max_depth: int = 6
    nextcloud_max_items: int = 2000
    mail_snapshot_dir: Path = field(
        default_factory=lambda: WORKSPACE_ROOT / "mail_agent/data/search_documents"
    )
    semantic_provider: str = "disabled"


@dataclass(slots=True)
class NextcloudConfig:
    enabled: bool = False
    resource_id: str = "nextcloud-main"
    base_url_env: str = "NEXTCLOUD_URL"
    username_env: str = "NEXTCLOUD_USER"
    token_env: str = "NEXTCLOUD_TOKEN"
    request_timeout_seconds: int = 45
    allowed_file_roots: tuple[str, ...] = ("Assistent",)
    calendar_horizon_days_back: int = 365
    calendar_horizon_days_forward: int = 730


@dataclass(slots=True)
class SelfManagementConfig:
    enabled: bool = True
    allow_safe_setting_changes: bool = True
    allow_resource_discovery: bool = True
    allow_secret_changes: bool = False
    allow_plugin_install: bool = False
    allow_code_changes: bool = False
    allow_permission_expansion: bool = False


@dataclass(slots=True)
class AssistantConfig:
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    nextcloud: NextcloudConfig = field(default_factory=NextcloudConfig)
    self_management: SelfManagementConfig = field(default_factory=SelfManagementConfig)
    path: Path = field(default_factory=lambda: DEFAULT_CONFIG)

    def ensure_dirs(self) -> None:
        if (
            os.environ.get("OPENCLAW_RUNTIME") == "container"
            and os.environ.get("OPENCLAW_ROLE") in CONTAINER_WORKER_ROLES
        ):
            # Layout initialization owns container directories. Worker roles
            # must not probe or create directories outside their mounts.
            return
        self.runtime.database.parent.mkdir(parents=True, exist_ok=True)
        self.runtime.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.search.mail_snapshot_dir.mkdir(parents=True, exist_ok=True)
        # Container secrets are externally managed read-only inputs.  Creating a
        # fallback directory below $HOME would violate the immutable rootfs
        # contract and is not needed after the entrypoint loaded /run secrets.
        if os.environ.get("OPENCLAW_RUNTIME") != "container":
            self.runtime.secrets_file.parent.mkdir(parents=True, exist_ok=True)


def _validate(config: AssistantConfig) -> None:
    errors: list[str] = []
    if not 500 <= config.search.chunk_chars <= 20_000:
        errors.append("search.chunk_chars muss zwischen 500 und 20000 liegen")
    if not 0 <= config.search.chunk_overlap_chars < config.search.chunk_chars:
        errors.append("search.chunk_overlap_chars muss kleiner als chunk_chars sein")
    if config.search.max_file_bytes < 1024:
        errors.append("search.max_file_bytes muss mindestens 1024 sein")
    if config.search.default_limit < 1 or config.search.default_limit > 200:
        errors.append("search.default_limit muss zwischen 1 und 200 liegen")
    if config.search.semantic_provider not in {"disabled", "ollama"}:
        errors.append("search.semantic_provider muss disabled oder ollama sein")
    for name in (
        config.nextcloud.base_url_env,
        config.nextcloud.username_env,
        config.nextcloud.token_env,
    ):
        if not name or not name.replace("_", "").isalnum() or name.upper() != name:
            errors.append(f"Ungueltiger Umgebungsvariablenname: {name!r}")
    if config.nextcloud.request_timeout_seconds < 5:
        errors.append("nextcloud.request_timeout_seconds muss mindestens 5 sein")
    if any(".." in root.replace("\\", "/").split("/") for root in config.nextcloud.allowed_file_roots):
        errors.append("nextcloud.allowed_file_roots darf kein '..' enthalten")
    if errors:
        raise ValueError("Ungueltige Personal-Assistant-Konfiguration:\n- " + "\n- ".join(errors))


def load_config(path: str | Path | None = None) -> AssistantConfig:
    config_path = Path(path or os.environ.get("PERSONAL_ASSISTANT_CONFIG", DEFAULT_CONFIG)).expanduser().resolve()
    if not config_path.exists():
        raise FileNotFoundError(
            f"Personal-Assistant-Konfiguration fehlt: {config_path}. "
            "Zuerst ./scripts/assistant.sh setup init ausfuehren"
        )
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    runtime_data = _section(data, "runtime").copy()
    for key in ("database", "log_file", "resources_file", "policies_file", "secrets_file"):
        if key in runtime_data:
            runtime_data[key] = _resolve(runtime_data[key])
    runtime = RuntimeConfig(**runtime_data)
    core_data = os.environ.get("OPENCLAW_CORE_DATA_DIR", "").strip()
    if core_data:
        core_root = Path(core_data).expanduser().resolve()
        runtime.database = core_root / "assistant.sqlite3"
        runtime.log_file = core_root / "assistant.log"
        runtime.resources_file = core_root / "resources.toml"
    role = os.environ.get("OPENCLAW_ROLE", "").strip()
    worker_log_data = os.environ.get("OPENCLAW_LOG_DIR", "").strip()
    if (
        os.environ.get("OPENCLAW_RUNTIME") == "container"
        and role in CONTAINER_WORKER_ROLES
        and worker_log_data
    ):
        worker_log_root = Path(worker_log_data).expanduser().resolve()
        runtime.log_file = worker_log_root / f"assistant-{role}.log"

    search_data = _section(data, "search").copy()
    if "mail_snapshot_dir" in search_data:
        search_data["mail_snapshot_dir"] = _resolve(search_data["mail_snapshot_dir"])
    search = SearchConfig(**search_data)
    mail_data = os.environ.get("OPENCLAW_MAIL_DATA_DIR", "").strip()
    if mail_data:
        search.mail_snapshot_dir = (
            Path(mail_data).expanduser().resolve() / "search_documents"
        )

    nextcloud_data = _section(data, "nextcloud").copy()
    if "allowed_file_roots" in nextcloud_data:
        nextcloud_data["allowed_file_roots"] = tuple(str(v) for v in nextcloud_data["allowed_file_roots"])
    nextcloud = NextcloudConfig(**nextcloud_data)
    self_management = SelfManagementConfig(**_section(data, "self_management"))
    config = AssistantConfig(
        runtime=runtime,
        search=search,
        nextcloud=nextcloud,
        self_management=self_management,
        path=config_path,
    )
    _validate(config)
    config.ensure_dirs()
    return config
