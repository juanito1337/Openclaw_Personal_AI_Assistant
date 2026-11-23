from __future__ import annotations

import shlex
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest
from test_nextcloud_access import grant, managed, measured, scope, update_policy
from test_nextcloud_foreground_access import account  # noqa: F401
from test_nextcloud_onboarding import multistatus

from personal_assistant import nextcloud_connection as nc
from personal_assistant import service
from personal_assistant.cli import parser
from personal_assistant.cli_handlers.calendar import handle
from personal_assistant.connectors.nextcloud.xmlutil import CALDAV, DAV, q
from personal_assistant.models import Resource
from personal_assistant.nextcloud_onboarding import HttpResponse
from personal_assistant.policy import PolicyEngine
from personal_assistant.registry import ResourceRegistry
from personal_assistant.service import PersonalAssistant
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import DirectCalendarToolSettings, NextcloudToolSettings, ToolSettings


def freeze_clock(monkeypatch, *instants):
    moments = iter(datetime.fromisoformat(instant) for instant in instants)
    current = None

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            nonlocal current
            current = next(moments, current)
            return current.astimezone(tz)

    monkeypatch.setattr(service, "datetime", Clock)


class AgendaSource:
    def __init__(self, replies):
        self.replies = replies
        self.windows = []

    def list_agenda(self, calendar, *, start, end, timezone_name):
        self.windows.append((calendar.resource_id, start, end, timezone_name))
        response = self.replies[calendar.resource_id]
        if isinstance(response, Exception):
            raise response
        return response


def agenda(*events, errors=(), complete=True):
    return {"events": list(events), "complete": complete, "errors": list(errors)}


def event(uid, start, *, title="Synthetischer Termin"):
    return {
        "uid": uid,
        "title": title,
        "start": start,
        "end": "",
        "location": "",
        "description": "",
        "status": "CONFIRMED",
        "recurring": False,
        "all_day": False,
        "recurrence_id": "",
    }


def assistant(tmp_path, calendar_settings):
    pa = PersonalAssistant.__new__(PersonalAssistant)
    pa.tool_settings = ToolSettings(
        path=tmp_path / "tools.toml", nextcloud=NextcloudToolSettings(calendar=calendar_settings)
    )
    pa.registry = ResourceRegistry(tmp_path / "resources.toml")
    pa.policy = PolicyEngine(tmp_path / "policy.toml", pa.registry)
    return pa


@pytest.fixture
def legacy(tmp_path):
    pa = assistant(tmp_path, DirectCalendarToolSettings(enabled=True, resource_id="first"))
    for name in ("first", "second"):
        pa.registry.resources[name] = Resource(
            id=name, kind="calendar", connector="nextcloud", remote_id=f"/cal/{name}/",
            permissions=("read",), metadata={"components": ["VEVENT"]},
        )
    pa.nextcloud_calendar = AgendaSource({"first": agenda(), "second": agenda()})
    return pa


@pytest.mark.parametrize(
    "zone,instant,start,end,utc_start,utc_end,hours",
    [
        (
            "Europe/Berlin", "2026-03-28T23:30:00+00:00",
            "2026-03-29T00:00:00+01:00", "2026-03-30T00:00:00+02:00",
            "2026-03-28T23:00:00+00:00", "2026-03-29T22:00:00+00:00", 23,
        ),
        (
            "Europe/Berlin", "2026-10-25T13:30:00+00:00",
            "2026-10-25T00:00:00+02:00", "2026-10-26T00:00:00+01:00",
            "2026-10-24T22:00:00+00:00", "2026-10-25T23:00:00+00:00", 25,
        ),
        (
            "Asia/Kathmandu", "2026-11-22T20:00:00+00:00",
            "2026-11-23T00:00:00+05:45", "2026-11-24T00:00:00+05:45",
            "2026-11-22T18:15:00+00:00", "2026-11-23T18:15:00+00:00", 24,
        ),
    ],
)
def test_empty_agenda_proves_the_entire_local_day(
    monkeypatch, legacy, zone, instant, start, end, utc_start, utc_end, hours,
):
    freeze_clock(monkeypatch, instant)
    legacy.tool_settings.nextcloud.calendar = replace(legacy.tool_settings.nextcloud.calendar, timezone=zone)

    result = legacy.calendar_today()

    assert result == {
        "ok": True, "count": 0, "events": [], "complete": True, "results_may_be_truncated": False,
        "resource_errors": [], "observed_at": instant, "absence_proven": True,
        "scope": {
            "kind": "calendar-day", "relative": "today", "date": start[:10], "timezone": zone,
            "start": start, "end": end, "resource_ids": ["first"], "selection": "all-selected",
        },
    }
    (resource_id, requested_start, requested_end, requested_zone), = legacy.nextcloud_calendar.windows
    assert (resource_id, requested_zone) == ("first", zone)
    assert requested_start.astimezone(UTC).isoformat() == utc_start
    assert requested_end.astimezone(UTC).isoformat() == utc_end
    assert (requested_end.astimezone(UTC) - requested_start.astimezone(UTC)).total_seconds() == hours * 3600


def test_rollover_cannot_prove_absence_for_today(monkeypatch, legacy):
    freeze_clock(monkeypatch, "2026-11-23T22:59:59+00:00", "2026-11-23T23:00:01+00:00")

    result = legacy.calendar_today()

    assert result["scope"]["date"] == "2026-11-23"
    assert result["observed_at"] == "2026-11-23T23:00:01+00:00"
    assert result["complete"] is False
    assert result["absence_proven"] is False
    assert {error["code"] for error in result["resource_errors"]} == {"calendar-day-changed"}


def test_partial_report_preserves_findings_and_sanitizes_failed_resource(monkeypatch, legacy):
    freeze_clock(monkeypatch, "2026-11-23T10:00:00+00:00")
    legacy.tool_settings.nextcloud.calendar = replace(
        legacy.tool_settings.nextcloud.calendar, resource_id="", resource_ids=("first", "second")
    )
    finding = event("visit", "2026-11-23T10:00:00+01:00")
    legacy.nextcloud_calendar.replies = {
        "first": agenda(
            finding, errors=({"code": "incomplete-calendar-response", "detail": "<private response body>"},),
            complete=False,
        ),
        "second": RuntimeError("<private response body>"),
    }

    result = legacy.calendar_today()

    assert result["ok"] is False
    assert result["complete"] is False
    assert result["results_may_be_truncated"] is True
    assert result["absence_proven"] is False
    assert result["scope"]["resource_ids"] == ["first", "second"]
    assert result["events"] == [{**finding, "resource_id": "first"}]
    assert result["count"] == 1
    assert result["resource_errors"] == [
        {"resource_id": "first", "code": "incomplete-calendar-response"},
        {"resource_id": "second", "code": "resource-read-failed"},
    ]
    assert "private response body" not in repr(result)


def test_limit_applies_after_merging_and_ordering_by_instant(monkeypatch, legacy):
    freeze_clock(monkeypatch, "2026-11-23T10:00:00+00:00")
    legacy.tool_settings.nextcloud.calendar = replace(
        legacy.tool_settings.nextcloud.calendar, resource_id="", resource_ids=("first", "second")
    )
    late = event("late", "2026-11-23T08:00:00+00:00")
    early = event("early", "2026-11-23T08:30:00+01:00")
    legacy.nextcloud_calendar.replies = {"first": agenda(late), "second": agenda(early)}

    result = legacy.calendar_today(limit=1)

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["events"] == [{**early, "resource_id": "second"}]
    assert result["complete"] is False
    assert result["results_may_be_truncated"] is True
    assert result["absence_proven"] is False


def test_empty_selection_and_policy_denial_are_never_empty_day_evidence(legacy):
    legacy.tool_settings.nextcloud.calendar = replace(legacy.tool_settings.nextcloud.calendar, resource_id="")
    unselected = legacy.calendar_today()
    assert unselected["scope"]["resource_ids"] == []
    assert unselected["resource_errors"] == [{"code": "resource-id-missing"}]
    legacy.tool_settings.nextcloud.calendar = replace(
        legacy.tool_settings.nextcloud.calendar, resource_id="first"
    )
    legacy.policy.data = {"deny": {"actions": ["calendar.read"]}}
    denied = legacy.calendar_today()
    assert denied["resource_errors"] == [{"resource_id": "first", "code": "permission-denied"}]
    for result in (unselected, denied):
        assert result["ok"] is False
        assert result["complete"] is False
        assert result["absence_proven"] is False
    assert legacy.nextcloud_calendar.windows == []


@pytest.fixture
def active(tmp_path, monkeypatch):
    store = managed(tmp_path)
    conn = measured()
    store.save(conn, expected_revision="")
    first = scope(conn)
    second = {
        **first,
        "resource_id": nc.collection_resource_id(conn, conn["discovery_snapshot"]["calendars"][1]["href"]),
    }
    grants = sorted(
        (grant(item, ("list", "read", "search")) for item in (first, second)),
        key=lambda item: item["scope"]["resource_id"],
    )
    snapshot = update_policy(store, grants)
    modified = snapshot.connection
    modified["discovery_snapshot"]["calendars"][1]["checked_at"] = "2000-01-01T00:00:00+00:00"
    store.save(modified, expected_revision=modified["revision"])
    monkeypatch.setenv("OPENCLAW_NEXTCLOUD_CONNECTION_DIR", str(store.directory))
    pa = assistant(tmp_path, DirectCalendarToolSettings())
    pa.tool_settings = nc.apply_tool_connection(pa.tool_settings, store.snapshot())
    pa.nextcloud_calendar = AgendaSource({first["resource_id"]: agenda()})
    return SimpleNamespace(pa=pa, store=store, first=first["resource_id"], second=second["resource_id"])


def test_stale_selected_calendar_is_visible_in_unqualified_query(active):
    result = active.pa.calendar_today()

    assert set(result["scope"]["resource_ids"]) == {active.first, active.second}
    assert result["resource_errors"] == [{"resource_id": active.second, "code": "rights-stale"}]
    assert result["absence_proven"] is False
    assert [window[0] for window in active.pa.nextcloud_calendar.windows] == [active.first]


def test_resource_id_outside_bound_inventory_cannot_be_read(active):
    result = active.pa.calendar_today(resource_id="not-selected")

    assert result["resource_errors"] == [{"resource_id": "not-selected", "code": "scope-unbound"}]
    assert result["absence_proven"] is False
    assert active.pa.nextcloud_calendar.windows == []


def test_policy_change_during_read_prevents_publication(active, monkeypatch):
    def read_then_change(*args, **kwargs):
        update_policy(active.store, [])
        return agenda(event("lost-scope", "2026-11-23T10:00:00+01:00"))

    monkeypatch.setattr(active.pa.nextcloud_calendar, "list_agenda", read_then_change)

    with pytest.raises(PermissionError, match="policy-changed"):
        active.pa.calendar_today(resource_id=active.first)


def test_registered_cli_dispatches_today_for_one_exact_resource(active):
    tools = {entry.id: entry for entry in build_tool_registry(active.pa.tool_settings)}
    tool = tools["nextcloud.calendar.today"]
    assert (tool.mode, tool.approval, tool.writes_external_data) == ("read", "none", False)
    args = parser().parse_args(shlex.split(tool.command.replace("<resource-optional>", active.first))[1:])
    output = []

    assert handle(args, active.pa, output.append) == 0
    assert output[0]["scope"]["resource_ids"] == [active.first]
    assert output[0]["scope"]["selection"] == "single-resource"
    assert output[0]["complete"] is True
    assert output[0]["absence_proven"] is False


def test_today_report_finds_event_hidden_by_historical_list_limit(account, monkeypatch):  # noqa: F811
    freeze_clock(monkeypatch, "2026-11-23T10:00:00+00:00")

    def dav_event(uid, start):
        ical = "\r\n".join((
            "BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VEVENT", f"UID:{uid}",
            "DTSTAMP:20261001T120000Z", f"DTSTART:{start}", "SUMMARY:Synthetischer Termin",
            "END:VEVENT", "END:VCALENDAR", "",
        ))
        return account.href + uid + ".ics", {
            q(DAV, "getetag"): '"synthetic"', q(CALDAV, "calendar-data"): ical,
        }

    today = dav_event("today", "20261123T090000Z")
    historical = [dav_event(f"past-{index}", "20261101T090000Z") for index in range(101)]
    account.replies["REPORT"] = HttpResponse(207, {}, multistatus([*historical, today]))
    broad = account.pa.calendar_list(limit=100)
    assert broad["complete"] is False
    assert all(row["uid"] != "today" for row in broad["events"])
    account.replies["REPORT"] = HttpResponse(207, {}, multistatus([today]))
    timezone = "\r\n".join((
        "BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VTIMEZONE", "TZID:Europe/Berlin",
        "BEGIN:STANDARD", "DTSTART:20261025T030000", "TZOFFSETFROM:+0200", "TZOFFSETTO:+0100",
        "END:STANDARD", "END:VTIMEZONE", "END:VCALENDAR",
    ))
    account.replies["PROPFIND"] = HttpResponse(207, {}, multistatus([
        (account.href, {q(CALDAV, "calendar-timezone"): timezone}),
    ]))

    result = account.pa.calendar_today()

    assert result["complete"] is True
    assert result["absence_proven"] is False
    assert [(row["uid"], row["resource_id"]) for row in result["events"]] == [("today", account.id)]
    last_method, last_href, kwargs = account.calls[-2]
    assert (last_method, last_href) == ("REPORT", account.href)
    query = ET.fromstring(kwargs["data"])
    bounds = {"start": "20261121T230000Z", "end": "20261124T230000Z"}
    assert query.find(".//" + q(CALDAV, "time-range")).attrib == bounds
    assert query.find(".//" + q(CALDAV, "expand")).attrib == bounds
