from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class ComponentSelection:
    component: str
    uid: str
    recurring: bool
    has_recurrence_id: bool


def unfold_ical(raw: str) -> list[str]:
    """Return RFC 5545 content lines with folded continuations joined."""
    result: list[str] = []
    for line in str(raw or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith((" ", "\t")) and result:
            result[-1] += line[1:]
        else:
            result.append(line)
    while result and not result[-1]:
        result.pop()
    return result


def property_name(line: str) -> str:
    if ":" not in line:
        return ""
    return line.split(":", 1)[0].split(";", 1)[0].upper()


def property_value(line: str) -> str:
    return line.split(":", 1)[1] if ":" in line else ""


def unescape_ical(value: str) -> str:
    return (
        str(value or "")
        .replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _component_blocks(lines: Sequence[str], component: str) -> list[tuple[int, int]]:
    target = component.upper()
    stack: list[tuple[str, int]] = []
    result: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        upper = line.upper()
        if upper.startswith("BEGIN:"):
            stack.append((upper.split(":", 1)[1], index))
            continue
        if upper.startswith("END:") and stack:
            name = upper.split(":", 1)[1]
            opened, start = stack.pop()
            if opened != name:
                raise ValueError("Ungueltige iCalendar-Struktur: BEGIN/END stimmen nicht ueberein")
            if opened == target:
                result.append((start, index))
    if stack:
        raise ValueError("Ungueltige iCalendar-Struktur: Komponente ist nicht geschlossen")
    return result


def _direct_property_indices(lines: Sequence[str], start: int, end: int) -> list[int]:
    depth = 0
    result: list[int] = []
    for index in range(start + 1, end):
        upper = lines[index].upper()
        if upper.startswith("BEGIN:"):
            depth += 1
            continue
        if upper.startswith("END:"):
            depth = max(0, depth - 1)
            continue
        if depth == 0 and ":" in lines[index]:
            result.append(index)
    return result


def component_properties(raw: str, component: str, uid: str = "") -> dict[str, list[str]]:
    lines = unfold_ical(raw)
    blocks = _component_blocks(lines, component)
    wanted = str(uid or "").strip()
    for start, end in blocks:
        indices = _direct_property_indices(lines, start, end)
        values: dict[str, list[str]] = {}
        for index in indices:
            name = property_name(lines[index])
            values.setdefault(name, []).append(property_value(lines[index]))
        block_uid = unescape_ical((values.get("UID") or [""])[0]).strip()
        if not wanted or block_uid == wanted:
            return values
    return {}


def update_component(
    raw: str,
    component: str,
    uid: str,
    replacements: Mapping[str, Sequence[str] | None],
    *,
    allow_recurring: bool = False,
) -> tuple[str, ComponentSelection]:
    """Replace selected direct properties of one VEVENT/VTODO.

    Unknown properties, nested VALARM blocks, VTIMEZONE data and recurrence
    exceptions are preserved. Existing properties are removed by base name and
    replacement lines are inserted before the first nested component. The
    master component is selected when a recurring object has exceptions.
    """
    wanted_uid = str(uid or "").strip()
    if not wanted_uid:
        raise ValueError("iCalendar-UID fehlt")
    target = component.upper()
    if target not in {"VEVENT", "VTODO"}:
        raise ValueError("Nur VEVENT und VTODO koennen aktualisiert werden")

    lines = unfold_ical(raw)
    matches: list[tuple[int, int, dict[str, list[str]]]] = []
    for start, end in _component_blocks(lines, target):
        props: dict[str, list[str]] = {}
        for index in _direct_property_indices(lines, start, end):
            name = property_name(lines[index])
            props.setdefault(name, []).append(property_value(lines[index]))
        block_uid = unescape_ical((props.get("UID") or [""])[0]).strip()
        if block_uid == wanted_uid:
            matches.append((start, end, props))
    if not matches:
        raise ValueError(f"{target}-Objekt mit UID {wanted_uid!r} wurde nicht gefunden")

    masters = [item for item in matches if "RECURRENCE-ID" not in item[2]]
    if len(masters) == 1:
        start, end, props = masters[0]
    elif len(matches) == 1:
        start, end, props = matches[0]
    else:
        raise ValueError("Mehrere gleichartige iCalendar-Komponenten mit derselben UID sind nicht eindeutig")

    recurring = bool("RRULE" in props or len(matches) > 1 or "RECURRENCE-ID" in props)
    has_recurrence_id = "RECURRENCE-ID" in props
    if recurring and not allow_recurring:
        raise ValueError(
            "Wiederkehrender Eintrag erkannt; Aktualisierung nur mit ausdruecklicher Serienfreigabe"
        )

    normalized: dict[str, tuple[str, ...] | None] = {}
    for name, values in replacements.items():
        base = str(name or "").upper().strip()
        if not base:
            continue
        if values is None:
            normalized[base] = None
        else:
            rendered = tuple(str(value) for value in values if str(value))
            normalized[base] = rendered
    if not normalized:
        raise ValueError("Keine iCalendar-Aenderung angegeben")
    if "UID" in normalized:
        raise ValueError("Die UID eines bestehenden iCalendar-Objekts darf nicht geaendert werden")

    remove_indices = {
        index
        for index in _direct_property_indices(lines, start, end)
        if property_name(lines[index]) in normalized
    }

    insertion_index = end
    depth = 0
    for index in range(start + 1, end):
        upper = lines[index].upper()
        if upper.startswith("BEGIN:"):
            if depth == 0:
                insertion_index = index
                break
            depth += 1
        elif upper.startswith("END:"):
            depth = max(0, depth - 1)

    replacement_lines: list[str] = []
    for _, values in normalized.items():
        if values:
            replacement_lines.extend(values)

    output: list[str] = []
    for index, line in enumerate(lines):
        if index == insertion_index:
            output.extend(replacement_lines)
        if index in remove_indices:
            continue
        output.append(line)
    if insertion_index == len(lines):
        output.extend(replacement_lines)

    rendered = "\r\n".join(output) + "\r\n"
    verify = component_properties(rendered, target, wanted_uid)
    if unescape_ical((verify.get("UID") or [""])[0]).strip() != wanted_uid:
        raise RuntimeError("UID-Verifikation nach iCalendar-Aktualisierung ist fehlgeschlagen")
    return rendered, ComponentSelection(
        component=target,
        uid=wanted_uid,
        recurring=recurring,
        has_recurrence_id=has_recurrence_id,
    )


def first_value(properties: Mapping[str, Sequence[str]], name: str, default: str = "") -> str:
    values = properties.get(name.upper()) or ()
    return str(values[0]) if values else default


def property_lines(name: str, values: Iterable[str]) -> tuple[str, ...]:
    base = str(name or "").upper().strip()
    return tuple(f"{base}:{value}" for value in values)
