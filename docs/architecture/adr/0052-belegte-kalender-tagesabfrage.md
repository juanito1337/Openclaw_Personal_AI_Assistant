# ADR 0052: Belegte Kalenderabfrage für heute

Status: akzeptiert am 23. November 2026, ergänzt am 26. November 2026.

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
prüft die zur lokalen Tagesabfrage passende Zeitzone. Die optionale Eigenschaft
fehlte beim ausgewählten Nextcloud-Kalender am 26. November 2026. Nur eine
leere `calendar-timezone` mit Status 404 im DAV-`propstat` der exakten
angefragten Sammlung aktiviert den folgenden Nachweis. Eine unpassende Zeitzone,
HTTP-Fehler, unklare Metadaten und fehlende Rechte ergeben einen unvollständigen
Tagesbefund.

Der Connector liest mit dem kanonischen, auf diese Sammlung beschränkten
VEVENT-REPORT ohne Zeitfilter die Originalobjekte. Er fordert danach eine
frische serverseitige Expansion für das gepufferte Tagesfenster an und liest
die Originalobjekte erneut. Jede dieser Antworten ist auf 4 MiB und 1.000
Objekte begrenzt. Objektpfade, starke ETags, UIDs und Originaldaten müssen
zwischen den vollständigen Originalabfragen übereinstimmen. Die expandierten
Objekte müssen an genau diese Pfade, ETags und UIDs gebunden sein.
Fehlerhafte, zu große, umgeleitete oder widersprüchliche Antworten sowie
Änderungen zwischen den Abfragen lassen den Tagesbefund unvollständig.

Die Tagesagenda wird in einem lokalen Durchlauf aus diesen Belegen aufgebaut.
Einzeltermine werden aus ihren Originalzeitangaben auf den konfigurierten
lokalen Tag projiziert. Dabei werden Floating-Zeiten als lokale Uhrzeiten in
jener Zeitzone interpretiert. Unterstützte Serien müssen unabhängig von der
Kalenderzeitzone interpretierbar sein. Zulässig sind Ganztagsdaten, UTC-Zeiten
und Zeiten mit einer bekannten IANA-Zeitzone. Eine eingebettete Zeitzonendefinition
muss für das gepufferte Tagesfenster mit den Regeln dieser IANA-Zeitzone
übereinstimmen. Der Connector begrenzt expandierte Ganztags- oder UTC-Instanzen
auf das lokale Tagesfenster. Floating-Serien, unbekannte oder nicht überprüfbare
Zeitzonen und mehrdeutige Zeitangaben verhindern Vollständigkeit. Der enge
DAV-Vertrag bindet alle drei Leseabfragen und die Metadatenabfrage an die
autorisierte Sammlung.

Eine im Original vorhandene, in der Expansion fehlende Serie gilt nur mit
lokal nachweisbaren Zeitgrenzen als außerhalb des Tages. Andernfalls bleibt
`calendar-fallback-series-omitted` sichtbar: Nextcloud kann aufwändige oder
ungültige Serien bei der Filterung ohne eigene Fehlerzeile auslassen.

Für den Tagesbeleg dürfen Originalobjekte vorher ausgeschlossen werden, wenn
konservative Zeitgrenzen eine Überschneidung sicher ausschließen. Bei
Einzelterminen werden beide Endpunkte und zulässige eingebettete UTC-Offsets
geprüft; unbekannte Dauern bleiben unvollständig. Bei fehlenden Jahresserien
prüft eine begrenzte Obermenge möglicher Startdaten auch Dauern, zusätzliche
Wiederholungsdaten und verschobene Instanzen. Das erzeugt keine lokalen
Serieninstanzen. Nicht belegbare Kombinationen der Serverexpansion, etwa
RRULE zusammen mit RDATE, bleiben unvollständig.

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
DAV-Teilantworten, den exakten Eigenschaftsstatus 404, abweichende Objektstände,
Antwortlimits, Projektion und Trunkierung sowie fremde oder alte Evidenz ab. Echte
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
