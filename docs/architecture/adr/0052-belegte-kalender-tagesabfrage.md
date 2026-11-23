# ADR 0052: Belegte Kalenderabfrage für heute

Status: akzeptiert für die lokale Fehlerkorrektur vom 23. November 2026.

## Anlass

Die Frage „Welche Termine habe ich heute?“ endet mit
`negative-claim-not-authorized`. Die aktuelle Liveprüfung zeigt funktionierenden
Kalenderzugriff, aber 100 Ergebnisse und `complete=false` beim allgemeinen
Listing. Dessen historische Sortierung, breites Zeitfenster und Serienmaster
sind keine vollständige Tagesagenda. Der Antwortschutz vergibt außerdem keine
Negativberechtigung für Kalender und verwechselt bestimmte Zugriffsfehler mit
einer Behauptung, dass Termine fehlen.

## Entscheidung

Ein zusätzliches registriertes Lesewerkzeug `nextcloud.calendar.today` führt
`calendar today --limit 100 --resource <resource-optional>` aus. Der Service
bestimmt heute und die beiden lokalen Tagesgrenzen in der bereits konfigurierten
Kalenderzeitzone. Der bestehende Connector sendet einen CalDAV-REPORT mit
serverseitiger Expansion und je 24 Stunden Puffer um dieses UTC-Zeitfenster;
die Ergebnisprojektion filtert wieder exakt auf den lokalen Tag. Sabre verwendet
bei Floating-/DATE-Filtern UTC, während die Expansion die Kalenderzeitzone nutzt.
Eine zusätzliche, ressourcengebundene Depth-0-Abfrage von `calendar-timezone`
muss die passende Zeitzone bestätigen. Ohne diesen Nachweis bleibt das Ergebnis
unvollständig. Der enge Wire-Vertrag erlaubt ausschließlich diese kanonische
Tagesabfrage mit Puffer und diese Metadatenabfrage. Es gibt
keine neuen Zugangsdaten, Ressourcenfreigaben oder Schreiboperationen.

Der Connector liefert `events`, `complete` und inhaltsfreie `errors`. Instanzen
behalten UID, Wiederholungsidentität und Ressource; ihre Darstellung autorisiert
keine Instanz- oder Serienänderung. Ungültige, teilweise oder nicht expandierte
Antworten bleiben unvollständig. Ganztag, Tageswechsel und Sommerzeit müssen
korrekt behandelt werden. Bei nicht sicher interpretierbaren Zeitangaben wird
kein vollständiges Ergebnis behauptet. Die bestehende allgemeine Liste und
der Index-Sync werden nicht in eine Tagesagenda umgedeutet.

Der Service ergänzt `observed_at`, `scope` mit `kind=calendar-day`,
`relative=today`, `date`, `timezone`, `start`, `end` und `resource_ids`, außerdem
`resource_errors`, `results_may_be_truncated` und `absence_proven`. Nur eine
vollständige, erfolgreiche, leere Abfrage aller ausgewählten Ressourcen darf
`absence_proven=true` liefern. Die Auswahl und Veröffentlichung laufen durch
die bestehende Kontobindung und Richtlinie. Ein Tageswechsel während der Abfrage
verhindert eine uneingeschränkte Aussage über „heute“.

Die native Evidenz übernimmt den geprüften Tagesumfang. Kalender-Negativaussagen
benötigen passende Kalender-Tagesbelege für einen heutigen Leseauftrag; andere
Domänen, ein allgemeines Listing, ein Suchwort oder ein Fehlerbeleg reichen nicht.
Belegte Zugriffsfehler bleiben aussprechbar, ohne eine Abwesenheit zu behaupten.
Die freigegebene deutsche Leerantwort lautet exakt „Heute hast du keine Termine.“;
der generierte Vertrag enthält entsprechende englische und spanische Varianten.
Diese begrenzte Ausgabe verhindert, dass ein Tagesbeleg zusätzliche Aussagen zu
anderen Zeiträumen oder Domänen autorisiert. Positive Terminlisten und belegte
Fehlerberichte bleiben frei formulierbar.

## Verifikation und Grenzen

Regressionen decken das aktuelle Limitproblem, leere/positive Tagesergebnisse,
mehrere Ressourcen, Serien mit Ausnahmen, Ganztag und DST, fehlgeschlagene
DAV-Teilantworten, Projektion/Trunkierung sowie fremde/alte Evidenz ab. Echte
Plugin-Hooks prüfen Routing, Werkzeugevidenz und die ausgelieferte Antwort.
Lokale Tests und Images sind getrennt von Veröffentlichung und Deployment.

Der Tagesbeleg ist an die aktuelle Frage, alle ausgewählten Ressourcen und eine
höchstens 15 Minuten alte Beobachtung gebunden. Ein Tageswechsel oder eine spätere
Tagesabfrage widerruft einen älteren Abwesenheitsbeleg. Eine explizit einzelne
Ressource kann vollständig gelesen sein, erhält aber kein `absence_proven=true`.

Die Transportgrenzen folgen dem [CalDAV-Standard](https://www.rfc-editor.org/rfc/rfc4791.html),
der [Sabre-Expansion](https://github.com/sabre-io/vobject/blob/master/lib/Component/VCalendar.php)
und dem [Nextcloud-Kalenderbackend](https://github.com/nextcloud/server/blob/master/apps/dav/lib/CalDav/CalDavBackend.php).
Eine erfolgreiche Protokollantwort kann vom Server stillschweigend ausgelassene,
beschädigte Objekte nicht belegen; der Client behauptet keine Integritätsprüfung
der serverinternen Datenbank.

Alternativen: Das Listlimit zu erhöhen behebt weder Serien noch vollständige
Tagesabdeckung. Kalender-Negativaussagen pauschal freizugeben würde den belegten
Teilbefund zur falschen Vollständigkeitsbehauptung machen.
