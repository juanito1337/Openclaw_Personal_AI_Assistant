from __future__ import annotations

import argparse
from collections.abc import Callable
from decimal import Decimal
from typing import Any


def _approval(args: argparse.Namespace, name: str) -> None:
    if args.dry_run and args.yes:
        raise ValueError("--dry-run und --yes koennen nicht gemeinsam verwendet werden")
    if not args.dry_run and not args.yes:
        raise PermissionError(f"{name} benoetigt --dry-run oder --yes")


def handle(args: argparse.Namespace, assistant: Any, emit: Callable[[Any], None]) -> int:
    command = args.portfolio_command
    if command == "status":
        result = assistant.portfolio.status()
    elif command == "doctor":
        result = assistant.portfolio.doctor()
        emit(result)
        return 0 if result.get("ok") else 1
    elif command == "import-pp":
        _approval(args, "Portfolio-Import")
        result = assistant.portfolio.import_pp(args.file, dry_run=not args.yes)
    elif command == "import-csv":
        _approval(args, "Portfolio-CSV-Import")
        result = assistant.portfolio_import_csv(
            local_file=args.file or "",
            nextcloud_path=args.nextcloud_path or "",
            dry_run=not args.yes,
        )
    elif command == "holdings":
        result = assistant.portfolio.holdings()
    elif command == "valuation":
        result = assistant.portfolio.valuation()
        emit(result)
        return 0 if result.get("ok") else 1
    elif command == "watchlist":
        if args.watchlist_command == "list":
            result = assistant.portfolio.watchlist()
        else:
            if not args.yes:
                raise PermissionError("Watchlist-Aenderung benoetigt --yes")
            result = (
                assistant.portfolio.watchlist_add(
                    isin=args.isin,
                    name=args.name,
                    symbol=args.symbol,
                    mic=args.mic,
                    currency=args.currency,
                )
                if args.watchlist_command == "add"
                else assistant.portfolio.watchlist_disable(args.isin)
            )
    elif command == "mapping":
        if args.mapping_command != "suggest":
            raise ValueError(f"Unbekannter Mapping-Befehl: {args.mapping_command}")
        result = assistant.portfolio.mapping_suggest(args.isin or "", query=args.query or "")
        emit(result)
        return 0 if result.get("ok") else 1
    elif command == "quotes":
        if args.quotes_command == "status":
            result = assistant.portfolio.health()
        elif args.quotes_command == "get":
            result = assistant.portfolio.latest_quote(args.isin)
            emit(result)
            return 0 if result.get("ok") else 1
        else:
            result = assistant.portfolio.refresh_quotes(force=bool(args.force))
            emit(result)
            if result.get("status") == "degraded":
                return 1
            return 0 if result.get("ok") else 2
    elif command == "analyze":
        result = assistant.portfolio.analyze(args.isin, limit=args.limit)
        emit(result)
        return 0 if result.get("ok") else 1
    elif command == "research":
        if args.research_command == "status":
            result = assistant.portfolio.research_status()
        elif args.research_command == "models":
            result = assistant.portfolio.research_models()
        elif args.research_command == "history":
            result = assistant.portfolio.research_history(limit=args.limit)
        elif args.research_command == "screen":
            result = assistant.portfolio.research_screen(
                strategy=args.strategy,
                exchange=args.exchange,
                sector=args.sector,
                limit=args.limit,
            )
        else:
            result = assistant.portfolio.research_analyze(
                args.isin,
                strategy=args.strategy,
            )
        emit(result)
        return 0 if result.get("ok") else 1
    elif command == "philosophy":
        if args.philosophy_command == "show":
            result = assistant.portfolio.philosophy_show()
        elif args.philosophy_command == "history":
            result = assistant.portfolio.philosophy_history(limit=args.limit)
        elif args.philosophy_command == "review":
            result = assistant.portfolio.philosophy_review()
        elif args.philosophy_command == "set":
            if not args.yes:
                raise PermissionError("Investmentprofil-Aenderung benoetigt --yes")
            result = assistant.portfolio.philosophy_set(
                risk_tolerance=args.risk_tolerance,
                horizon_years=args.horizon_years,
                strategy=args.strategy,
                max_position_pct=Decimal(args.max_position_pct),
                max_sector_pct=Decimal(args.max_sector_pct),
                preferred_sectors=args.preferred_sectors.split(","),
                excluded_sectors=args.excluded_sectors.split(","),
                notes=args.notes,
            )
        else:
            if not args.yes:
                raise PermissionError("Investment-Rueckmeldung benoetigt --yes")
            result = assistant.portfolio.philosophy_feedback(
                candidate_id=args.candidate_id,
                decision=args.decision,
                reason=args.reason,
            )
    elif command == "alerts":
        if args.portfolio_alerts_command == "list":
            result = assistant.portfolio.alerts()
        else:
            if not args.yes:
                raise PermissionError("Kursalarm-Aenderung benoetigt --yes")
            result = (
                assistant.portfolio.alert_add(
                    isin=args.isin,
                    direction=args.direction,
                    threshold=Decimal(args.threshold),
                    currency=args.currency,
                    hysteresis_bps=args.hysteresis_bps,
                    cooldown_minutes=args.cooldown_minutes,
                )
                if args.portfolio_alerts_command == "add"
                else assistant.portfolio_alert_disable(args.id)
            )
    elif command == "performance":
        result = assistant.portfolio.signal_performance()
    else:
        raise ValueError(f"Unbekannter Portfolio-Befehl: {command}")
    emit(result)
    return 0
