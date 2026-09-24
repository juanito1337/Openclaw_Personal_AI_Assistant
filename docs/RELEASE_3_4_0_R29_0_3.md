# Release 3.4.0-r29.0.3

Dieses Patch-Release stellt den vollstaendigen Inhalt von
Terminfreigabemails wieder her und korrigiert die CalDAV-Inkompatibilitaet bei
der anschliessenden Nextcloud-Erstellung. Die speichersicheren Agent-CLI- und
Worker-Startkorrekturen aus r29.0.1 und r29.0.2 bleiben unveraendert enthalten.

## Ursache und Korrektur

Der Freigabepfad versandte bislang nur den erzeugten Entscheidungstext. Anders
als die normale Weiterleitung verwendete er nicht den kontrollierten ZIP-Pfad
fuer die vollstaendige Originalmail. Terminfreigaben uebergeben nun dieselbe
bereits gepruefte Rohmail und haengen sie unveraendert als
`original-message.eml.zip` an. Dieser Versand unterdrueckt weiterhin bewusst
die IMAP-Kopie im Gesendet-Ordner.

Die lokal erzeugten VCALENDAR-Objekte enthielten ausserdem
`METHOD:PUBLISH`. Nextcloud lehnt eine `METHOD`-Eigenschaft auf einem
gespeicherten CalDAV-Kalenderobjekt mit HTTP 415 ab. Ablageobjekte werden daher
ohne `METHOD` erzeugt; UID, Zeit, Titel, Ort, Beschreibung, Status und
Teilnehmer bleiben erhalten.

## Verifikation und Installation

- Verhaltenstests pruefen ZIP-MIME-Teil, Empfaenger, Reply-To und Versand ohne
  Sent-Kopie.
- Der Freigabe-und-JA-Test prueft, dass das an Nextcloud uebergebene Objekt
  keine `METHOD`-Eigenschaft besitzt.
- Die fehlgeschlagene alte Kalenderaktion wird nicht automatisch wiederholt;
  eine neue Freigabe bleibt eine getrennte, explizite Aktion.
- Veroeffentlichung und Installation verwenden den signierten Tag `r29.0.3`
  sowie drei unveraenderliche, attestierte Rollenimage-Digests.

Die produktive Abnahme ist erst erfolgreich, wenn Version, Status, Tools,
Capabilities, Deep-Jobcheck, Antivirus und Agent-Tools aus dem installierten
Stack erfolgreich zurueckgelesen wurden.
