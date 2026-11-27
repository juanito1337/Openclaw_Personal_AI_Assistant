# Herstellerbestaetigte ClamAV-Signaturen

Jan ersetzt ausdruecklich die bisherige 48-Stunden-Grenze durch den aktuellen
Herstellerstand: auch alte Signaturen sind zulaessig, wenn keine neueren angeboten
werden. Erkannte neue Signaturen werden sofort aktualisiert und erst nach
nachgewiesener Uebernahme durch den Scanner freigegeben. Der bestehende Auftrag
umfasst Tests, main-Publikation, PC-Deployment und Mail/Index/Sync-Abnahme.

## Entscheidung und Alternativen

Die bestehende einzelne Updater-Rolle fragt alle 300 Sekunden ausschliesslich
den offiziellen DNS-TXT-Eintrag `current.cvd.clamav.net` ab. Nur bei neueren
Versionen oder nicht verifiziertem Bestand startet sie FreshClam. Periodische
Wartung bestaetigt unveraenderte Inhalte per SHA-256 ohne Scannerunterbrechung.
Downloads bleiben bei FreshClam; vor der ersten Freigabe und nach Aenderungen
authentifiziert zusaetzlich `libclamav.cl_cvdverify` die CVD-Archive beziehungsweise
die signierten CLD-Inhaltsmanifeste samt Datei-Hashes in einem begrenzten
Kindprozess. Ein FreshClam-Lauf alle fuenf Minuten wuerde bei
DNS-Problemen unnoetige HTTP-Last erzeugen; eine Altersausnahme ohne positive
Herstellerbeobachtung erfuellt Jans Auftrag nicht.

Die folgenden technischen Werte sind Implementierungsentscheidungen: DNS-Probe
hoechstens 10 Sekunden, Pollintervall 300 Sekunden, Nachweis-TTL 660 Sekunden.
Die DNS-Erzeugungszeit darf nicht in der Zukunft und hoechstens 12 Stunden alt
sein (Plausibilitaetsgrenze der gepinnten FreshClam-Version). DNS ist der
offizielle Versionshinweis, kein kryptografisch authentifizierter Latest-Beweis.
Eine gescheiterte Probe wird sichtbar und kann keine Freigabe erneuern.

## Daten und Freigabe

Ein atomarer, groessenbegrenzter Herstellerbeleg im vorhandenen `clamav-db`-Volume
enthaelt alle drei Versionsnummern (`main`, `daily`, `bytecode`), Abrufzeit,
DNS-Erzeugungszeit und die genaue lokale Datenbankgeneration. Nur der Updater
schreibt ihn, Modus 0640; bestehende Verbraucher lesen ueber ihre RO-Mounts.
Die Generationsidentitaet bindet SHA-256 aller drei Datenbanken; lokale Leser
pruefen zusaetzlich unveraenderte Dateimetadaten und Header. Mehrdeutige
gleichzeitige CVD/CLD-Dateien, Symlinks, Teilbelege, Zukunftszeiten und lokale
Versionen oberhalb des Herstellerstands werden geschlossen diagnostiziert.

Sobald ein neuer Herstellerstand bekannt ist, wird die alte Freigabe vor dem
Download ungueltig. Ein misslungener Download, CDN-Rueckstand oder fehlende
Scanneruebernahme erhaelt diese Sperre. Das Baualter bleibt informativ.

Der netzlose Scanner ueberwacht die publizierte Generation. Bei Aenderung startet
sein eigener Wrapper den Daemon aus genau diesem stabilen Bestand neu und
publiziert erst nach PING/VERSION einen instanzgebundenen geladenen Beleg im
vorhandenen Socket-Volume. Ein RELOAD-ACK oder die Daily-Nummer allein kann
Main-/Bytecode-Aenderungen nicht beweisen. Clean-Cache und laufende Scans sind
an dieselbe Generation gebunden. Unbekannte Legacy-Konstellationen ohne
vollstaendigen Nachweis werden explizit unready, nicht still per Alter freigegeben.

## Deployment und Diagnose

Eine schreibfreie Kandidatenvorpruefung darf den Hersteller direkt abfragen und
den bestehenden Bestand vergleichen, auch wenn noch kein neuer Beleg existiert.
Vor einem einmaligen Kandidaten-Update wird der alte Updater beendet und sein
Stillstand verifiziert; Fehler stellen seinen vorherigen Betriebszustand wieder
her. Der Kandidaten-Updater publiziert den ersten Beleg vor Scannerstart.
Backup, signierte Images, lokale Wiederherstellung und Produkt-Smoke bleiben
verbindlich. Kein produktiver Quellcode wird im laufenden Container geaendert.

Der erste Policy-Wechsel benoetigt eine separat aufgezeichnete kompatible
Recovery-Maintenance-Rolle: ein alter Scanner mit Altersgrenze ist bei alten,
herstelleraktuellen Signaturen nicht zuverlaessig neu startbar. Das exakte
signierte Recovery-Image wird vor dem Stoppen auf einer privaten Datenbankkopie
mit echtem Scannerstart geprueft. Originalreferenzen bleiben im Backup erhalten;
Rollback restauriert alte Anwendung/Proxy/Zustand und verwendet die belegte
Scanner-Infrastruktur. Der alte Anwendungsindex kann wegen seiner eigenen
historischen Alterspruefung weiterhin gesperrt sein und wird so berichtet.
Automatischer Rollback stoppt fehlgeschlagene Kandidatenwriter vor allen
netzabhaengigen Wiederherstellungspruefungen.

`security antivirus doctor`, Indexdiagnose und Docker-Health zeigen Angebot,
installierten und geladenen Stand, Belegalter und den konkreten Blocker getrennt.
Die praktische Reaktionszeit ist Hersteller-/DNS-Sichtbarkeit plus hoechstens
fuenf Minuten bis zur Erkennung und anschliessende Download-/Ladezeit.
