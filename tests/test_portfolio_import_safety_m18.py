from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from personal_assistant.portfolio import PortfolioService
from personal_assistant.portfolio_import import (
    parse_dkb_portfolio_csv,
    parse_portfolio_performance_xml,
)
from personal_assistant.tool_settings import PortfolioToolSettings
from tests.test_portfolio_tool import CleanAntivirus, csv_fixture, xml_fixture


def _csv_with(field: str, value: str) -> bytes:
    reader = csv.DictReader(io.StringIO(csv_fixture().decode("utf-8-sig")), delimiter=";")
    rows = list(reader)
    rows[0][field] = value
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=reader.fieldnames or [], delimiter=";")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


@pytest.mark.parametrize("value", ("NaN", "sNaN", "Infinity", "+Infinity", "-Infinity"))
@pytest.mark.parametrize(
    "field", ("Stückzahl", "Einstiegskurs", "Bewertungskurs", "Absoluter Gewinn", "Relativer Gewinn")
)
def test_csv_rejects_nonfinite_required_and_optional_values(field: str, value: str) -> None:
    if field in {"Einstiegskurs", "Bewertungskurs", "Absoluter Gewinn"}:
        value += " EUR"
    elif field == "Relativer Gewinn":
        value += "%"
    with pytest.raises(ValueError, match="endliche Zahl"):
        parse_dkb_portfolio_csv(_csv_with(field, value))


@pytest.mark.parametrize("value", ("NaN", "sNaN", "Infinity", "+Infinity", "-Infinity"))
def test_xml_rejects_nonfinite_shares(value: str) -> None:
    payload = xml_fixture().replace(b'shares="12.5"', f'shares="{value}"'.encode())
    with pytest.raises(ValueError, match="endliche Zahlen"):
        parse_portfolio_performance_xml(payload)


@pytest.fixture
def service(tmp_path: Path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    instance = PortfolioService(
        PortfolioToolSettings(
            enabled=True, database=tmp_path / "portfolio.sqlite3", import_root=inbox,
        ),
        CleanAntivirus(),  # type: ignore[arg-type]
    )
    try:
        yield instance
    finally:
        instance.close()


@pytest.mark.parametrize("dry_run", (True, False))
@pytest.mark.parametrize("format_name", ("csv", "xml"))
def test_scan_and_parser_use_one_snapshot_even_if_path_changes(
    service: PortfolioService, dry_run: bool, format_name: str
) -> None:
    original = csv_fixture() if format_name == "csv" else xml_fixture()
    changed = _csv_with("Stückzahl", "999") if format_name == "csv" else xml_fixture().replace(
        b'shares="12.5"', b'shares="999"'
    )
    path = service.settings.import_root / f"depot.{format_name}"
    path.write_bytes(original)
    scanner = CleanAntivirus()

    def scan_and_replace(data: bytes, *, name: str, source_type: str):
        assert data == original
        path.write_bytes(changed)
        return scanner.scan_bytes(data, name=name, source_type=source_type)

    with patch.object(service.antivirus, "scan_bytes", side_effect=scan_and_replace) as scan:
        result = getattr(service, "import_csv" if format_name == "csv" else "import_pp")(
            path.name, dry_run=dry_run
        )
    assert scan.call_count == 1
    assert path.read_bytes() == changed
    assert result["sha256"] == result["antivirus"]["sha256"] == hashlib.sha256(original).hexdigest()
    positions = result["preview"]["positions"] if dry_run else service.holdings()["positions"]
    assert any(row["shares"] == "12.5" for row in positions)
    assert not any(row["shares"] == "999" for row in positions)
    if dry_run:
        assert service.holdings()["count"] == 0


@pytest.mark.parametrize("dry_run", (True, False))
@pytest.mark.parametrize("mismatch", ("digest", "size", "infected", "error"))
def test_invalid_scan_blocks_parser_and_any_import(
    service: PortfolioService, dry_run: bool, mismatch: str
) -> None:
    data = csv_fixture()
    path = service.settings.import_root / "depot.csv"
    path.write_bytes(data)
    scan = CleanAntivirus().scan_bytes(data, name=path.name, source_type="portfolio-import")
    if mismatch == "digest":
        scan = replace(scan, sha256="a" * 64)
    elif mismatch == "size":
        scan = replace(scan, size_bytes=len(data) - 1)
    else:
        scan = replace(scan, status=mismatch)
    parser = Mock(wraps=parse_dkb_portfolio_csv)
    with (
        patch.object(service.antivirus, "scan_bytes", return_value=scan),
        pytest.raises(PermissionError, match="fail-closed"),
    ):
        service._import_snapshot(path.name, parser=parser, dry_run=dry_run)
    parser.assert_not_called()
    assert service.holdings()["count"] == 0
    assert service.store.connection.execute("SELECT count(*) FROM imports").fetchone()[0] == 0


def test_import_byte_limit_is_checked_before_scan_or_parse(service: PortfolioService) -> None:
    path = service.settings.import_root / "depot.csv"
    path.write_bytes(csv_fixture())
    with (
        patch("personal_assistant.portfolio.MAX_IMPORT_BYTES", 10),
        patch.object(service.antivirus, "scan_bytes") as scanner,
        pytest.raises(ValueError, match="groesser"),
    ):
        service.import_csv(path.name, dry_run=False)
    scanner.assert_not_called()
    assert service.holdings()["count"] == 0


def test_invalid_numbers_cannot_replace_an_existing_snapshot(service: PortfolioService) -> None:
    path = service.settings.import_root / "depot.csv"
    path.write_bytes(csv_fixture())
    service.import_csv(path.name, dry_run=False)
    before = service.holdings()
    path.write_bytes(_csv_with("Absoluter Gewinn", "NaN EUR"))
    with pytest.raises(ValueError, match="endliche Zahl"):
        service.import_csv(path.name, dry_run=False)
    assert service.holdings() == before
    assert service.store.connection.execute("SELECT count(*) FROM imports").fetchone()[0] == 1
