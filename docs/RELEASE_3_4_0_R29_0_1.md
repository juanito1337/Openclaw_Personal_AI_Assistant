# Release 3.4.0-r29.0.1

Dieses Patch-Release behebt den bei der produktiven r29-Abnahme festgestellten
Speicherueberlauf des Agent-CLI-Statuspfads. Die fachlichen Funktionen und die
rollenbezogenen Runtime-Limits bleiben unveraendert.

## Ursache und Korrektur

Bei einer fehlgeschlagenen Workerpruefung las `_journal` das vollstaendige,
append-only Containerlog in einen Unicode-String und schnitt erst danach die
letzten 8000 Zeichen ab. Das produktive Supervisor-Log war rund 357 MB gross;
die dabei entstehenden Byte-, Unicode- und Slice-Kopien ueberschritten das
2-GiB-Cgroup-Limit des Agent-CLI-Prozesses.

Der Statuspfad sucht nun binaer vom Dateiende aus zurueck, liest hoechstens
16 KiB und dekodiert nur dieses Fenster. Die bestehende Ausgabegrenze von 8000
Zeichen bleibt erhalten; bei gekuerzten Logs zeigt ein Marker die Anzahl der
ausgelassenen Bytes.

## Verifikation und Installation

- Ein Regressionstest verwendet ein 64-MiB-Sparse-Log und prueft, dass nur der
  begrenzte Tail erscheint.
- Eine lokale Probe gegen das produktionsgrosse Supervisor-Log benoetigte
  17 124 KiB Spitzen-RSS und 0,13 Sekunden.
- Das Runtime-Speicherlimit wird nicht erhoeht.
- Veroeffentlichung, Main-Promotion und produktive Installation verwenden den
  signierten Tag `r29.0.1`, drei unveraenderliche Image-Digests sowie den
  verifizierten r28-Rollbacksatz.

Die produktive Abnahme ist erst erfolgreich, wenn Versions-, Status-, Tool-,
Capability-, Deep-Job-, Antivirus- und Agent-Tool-Pruefungen aus dem installierten
Stack erfolgreich zurueckgelesen wurden.
