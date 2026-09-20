from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_assistant.connectors.nextcloud.discovery import DiscoveredCollection
from personal_assistant.registry import ResourceRegistry
from personal_assistant.resource_identity import (
    CalendarIdentityMigration,
    ResourceIdentityError,
    ResourceReference,
    inventory_report,
    resolve_reference,
    select_exact_discovered,
)
from personal_assistant.tool_registry import tool_definitions
from personal_assistant.tool_settings import load_tool_settings


def _registry(path: Path, *, duplicate: bool = False) -> ResourceRegistry:
    repeated = ""
    if duplicate:
        repeated = """
[[resources]]
id = "calendar-main"
kind = "calendar"
connector = "nextcloud"
enabled = true
remote_id = "/remote/duplicate/"
permissions = ["read", "create", "update"]
components = ["VEVENT"]
name = "Duplicate"
"""
    path.write_text(
        """
[[resources]]
id = "calendar-main"
kind = "calendar"
connector = "nextcloud"
enabled = true
remote_id = "/remote/calendar/"
permissions = ["read", "create", "update"]
components = ["VEVENT"]
name = "Arbeit"
"""
        + repeated,
        encoding="utf-8",
    )
    return ResourceRegistry(path)


def _settings(path: Path, *, mail_resource: str = "legacy-calendar") -> None:
    path.write_text(
        f"""
[mail.calendar_mail]
enabled = true
subject_prefix = "[ASSISTENT TERMIN]"
sender_addresses = ["owner@example.invalid"]
calendar_resource_id = "{mail_resource}"

[nextcloud.calendar]
enabled = true
resource_id = "calendar-main"
allow_create = true
allow_list = true
allow_update = true
timezone = "Europe/Berlin"
default_duration_minutes = 60
max_duration_hours = 168
max_future_days = 730
""",
        encoding="utf-8",
    )


def test_calendar_and_mail_calendar_share_identity_and_permissions(tmp_path: Path) -> None:
    settings_path = tmp_path / "tools.toml"
    _settings(settings_path, mail_resource="calendar-main")
    settings = load_tool_settings(settings_path)
    registry = _registry(tmp_path / "resources.toml")

    report = inventory_report(settings, registry)
    calendar = next(item for item in report["items"] if item["domain"] == "calendar")
    mail = next(item for item in report["items"] if item["domain"] == "mail-calendar")

    assert report["calendar_identity"]["same_resource"] is True
    assert calendar["resource_id"] == mail["resource_id"] == "calendar-main"
    assert calendar["resource_permissions"] == mail["resource_permissions"]
    assert calendar["business_name"] == mail["business_name"] == "Arbeit"
    assert calendar["remote_identifier"] == mail["remote_identifier"]


@pytest.mark.parametrize(
    ("resource_id", "expected"),
    (("", "resource-id-missing"), ("old-calendar", "resource-id-stale")),
)
def test_missing_and_stale_ids_have_distinct_codes(
    tmp_path: Path,
    resource_id: str,
    expected: str,
) -> None:
    registry = _registry(tmp_path / "resources.toml")
    reference = ResourceReference(
        "calendar",
        "nextcloud.calendar.resource_id",
        True,
        resource_id,
        "calendar",
        "VEVENT",
        ("read",),
    )
    with pytest.raises(ResourceIdentityError) as error:
        resolve_reference(registry, reference)
    assert error.value.code == expected


def test_duplicate_registry_and_ambiguous_discovery_fail_closed(tmp_path: Path) -> None:
    registry = _registry(tmp_path / "resources.toml", duplicate=True)
    reference = ResourceReference(
        "calendar",
        "nextcloud.calendar.resource_id",
        True,
        "calendar-main",
        "calendar",
        "VEVENT",
        ("read",),
    )
    with pytest.raises(ResourceIdentityError) as duplicate:
        resolve_reference(registry, reference)
    assert duplicate.value.code == "resource-id-duplicate"

    collections = [
        SimpleNamespace(resource_id="calendar-main"),
        SimpleNamespace(resource_id="calendar-main"),
    ]
    with pytest.raises(ResourceIdentityError) as ambiguous:
        select_exact_discovered(collections, "calendar-main", label="Kalender")
    assert ambiguous.value.code == "resource-discovery-ambiguous"


def test_component_permission_credentials_and_config_drift_are_separate(tmp_path: Path) -> None:
    registry_path = tmp_path / "resources.toml"
    registry_path.write_text(
        """
[[resources]]
id = "calendar-main"
kind = "calendar"
connector = "nextcloud"
enabled = true
remote_id = "/remote/calendar/"
permissions = ["read"]
components = ["VTODO"]
""",
        encoding="utf-8",
    )
    registry = ResourceRegistry(registry_path)
    component = ResourceReference(
        "calendar",
        "nextcloud.calendar.resource_id",
        True,
        "calendar-main",
        "calendar",
        "VEVENT",
        ("read",),
    )
    with pytest.raises(ResourceIdentityError) as error:
        resolve_reference(registry, component)
    assert error.value.code == "resource-component-missing"

    permission = ResourceReference(
        "tasks",
        "nextcloud.tasks.resource_id",
        True,
        "calendar-main",
        "calendar",
        "VTODO",
        ("read", "update"),
    )
    with pytest.raises(ResourceIdentityError) as error:
        resolve_reference(registry, permission)
    assert error.value.code == "resource-permission-missing"

    settings_path = tmp_path / "tools.toml"
    _settings(settings_path)
    report = inventory_report(
        load_tool_settings(settings_path),
        registry,
        missing_credentials=["NEXTCLOUD_TOKEN"],
    )
    assert report["credentials"]["error_code"] == "resource-credentials-missing"
    assert report["calendar_identity"]["error_code"] == "resource-configuration-drift"


def test_calendar_migration_is_preview_bound_atomic_and_rollbackable(tmp_path: Path) -> None:
    settings_path = tmp_path / "tools.toml"
    _settings(settings_path)
    registry = _registry(tmp_path / "resources.toml")
    registry_before = registry.path.read_bytes()
    permissions_before = registry.resources["calendar-main"].permissions
    migration = CalendarIdentityMigration(settings_path, registry)

    preview = migration.preview()
    assert preview["read_only"] is True
    assert preview["external_writes"] is False
    assert preview["permission_expansion"] is False
    assert preview["from_resource_id"] == "legacy-calendar"
    assert preview["to_resource_id"] == "calendar-main"

    with pytest.raises(ResourceIdentityError) as stale:
        migration.apply(expected_preview_sha256="0" * 64, approved=True)
    assert stale.value.code == "resource-migration-preview-stale"

    result = migration.apply(
        expected_preview_sha256=preview["preview_sha256"],
        approved=True,
    )
    assert result["applied"] is True
    assert result["atomic_publish"] is True
    assert result["validated"] is True
    assert registry.path.read_bytes() == registry_before
    assert registry.resources["calendar-main"].permissions == permissions_before
    migrated = load_tool_settings(settings_path)
    assert migrated.mail.calendar_mail.calendar_resource_id == "calendar-main"
    assert migrated.nextcloud.calendar.resource_id == "calendar-main"

    rollback = migration.rollback(Path(result["backup"]), approved=True)
    assert rollback["rolled_back"] is True
    restored = load_tool_settings(settings_path)
    assert restored.mail.calendar_mail.calendar_resource_id == "legacy-calendar"
    assert registry.path.read_bytes() == registry_before


def test_exact_discovery_never_selects_name_or_first_item() -> None:
    values = [
        DiscoveredCollection("calendar", "/one", "Arbeit", "calendar-one"),
        DiscoveredCollection("calendar", "/two", "Arbeit", "calendar-two"),
    ]
    with pytest.raises(ResourceIdentityError) as stale:
        select_exact_discovered(values, "Arbeit", label="Kalender")
    assert stale.value.code == "resource-id-stale"
    assert select_exact_discovered(values, "calendar-two", label="Kalender") is values[1]


def test_tool_catalog_exposes_only_registered_identity_commands() -> None:
    tools = {item.id: item for item in tool_definitions()}
    assert tools["assistant.resources.status"].mode == "read"
    apply = tools["assistant.resources.calendar-migration-apply"]
    assert apply.mode == "local-write"
    assert apply.writes_external_data is False
    assert apply.approval == "explicit-user-calendar-resource-identity-migration"
    rollback = tools["assistant.resources.calendar-migration-rollback"]
    assert "--yes" in rollback.command
