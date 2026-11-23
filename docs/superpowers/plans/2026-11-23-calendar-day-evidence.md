# Kalender-Tagesabfrage und Antwortbelege

Ausgangsstand: `55e5003`. Zweig: `codex/fix-calendar-day-evidence-20261123`.
Entscheidung: [ADR 0052](../../architecture/adr/0052-belegte-kalender-tagesabfrage.md).

1. Connector: `list_agenda(calendar, *, start, end, timezone_name)` als
   begrenzter lesender REPORT mit Expansion. Ergebnis: Ereignis-Dictionaries
   mit den bisherigen Anzeigefeldern plus `recurrence_id`, `complete`, `errors`.
   Datumseingaben sind bewusste aware datetime-Werte, Ende exklusiv. Tests zuerst.
2. Service/CLI/Katalog: `calendar_today(limit=100)` und `calendar today` mit
   optionaler Ressource; serverseitige Tagesgrenzen, Auswahl, Vollständigkeit,
   Zeit-/Ressourcenbelege und abgesicherte Negativauskunft. Tests zuerst.
3. Plugin: heutige Leseanfragen zum neuen Werkzeug führen, den Tagesbeleg bewahren
   und passende Kalender-Negativaussagen erlauben. Teilbefunde/fremde Belege
   weiterhin sperren; Zugriffsfehler nicht als Terminabwesenheit behandeln.
4. Integration: generierte Verträge, Dokumentation, Inventar und Manifest
   aktualisieren; gezielte Regressionen und unabhängiger Review, dann Repository-,
   Wheel- und betroffene Imageprüfungen. Keine produktive Aktivierung.

Review-Fokus: alle ausgewählten Kalender, 23/25-Stunden-Tage, Serien-Ausnahmen,
keine still verworfenen Antwortfehler, keine Fremddomänen-/Altbelege, vollständige
Tagesabfrage vor einem negativen Ergebnis und unveränderte Schreibrechte.

## Nachweise

- Live: Paketidentität geprüft; Kalenderstatus erfolgreich; allgemeines Listing
  100 Zeilen, `complete=false`, `results_may_be_truncated=true`.
- Synthetisch: sowohl vollständig leere Kalenderliste als auch die Formulierung
  „Ich kann keine Termine abrufen“ lösen den bisherigen Negativschutz aus.

- Umsetzung und unabhängiger Review abgeschlossen; alle gemeldeten Blocker mit
  Regressionen behoben. Die tatsächlichen Plugin-Hooks prüfen Tagesrouting,
  Teilfehler, kanonische Leerantwort, einmalige Korrektur und fremde Laufbelege.
- `check-repo.sh`: Exit 0, 2.440 bestanden, zwei optionale Image-Skips,
  74,37 % kombinierte Coverage, keine neuen Ruff-/mypy-Befunde.
- `check-wheel.sh`: Exit 0, 2.440 bestanden, 302 Subtests, zwei optionale Skips;
  geprüftes Wheel 796.596 Bytes. Drei synthetische M8-Recovery-Szenarien bestehen.
- Die vollständigen Logs liegen lokal unter `build/calendar-day/`. Ein erster
  eingeschränkter Lauf zeigte Socketverbote der Sandbox; der abschließende Lauf
  nutzte isolierte lokale Testserver außerhalb dieser Beschränkung. Die neue
  Werkzeugzahl ist im überprüften Referenzvertrag ausdrücklich ergänzt.
