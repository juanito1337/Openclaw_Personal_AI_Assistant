# Release 3.4.0-r29.0.4

Dieses Patch-Release ergaenzt die sichere manuelle Weiterleitung einer exakt
ausgewaehlten vorhandenen Mail. Der Empfaenger erhaelt die vollstaendige
Originalnachricht mitsamt allen urspruenglichen Anhaengen als
`original-message.eml.zip`.

## Ursache und Korrektur

Der bisherige native Werkzeugkatalog konnte neue Mails und Antworten entwerfen,
aber keine vorhandene Nachricht mitsamt ihrer binaeren Anhaenge manuell
weiterleiten. Dadurch erklaerte der Agent bei solchen Auftraegen entweder nur den
Text senden zu koennen oder stellte einen technisch nicht vorhandenen Download-
und-Neuversandpfad in Aussicht.

`mail.forward-draft` bindet nun genau eine live gelesene Quellmail anhand von
Ordner, stabiler Mail-ID, erwartetem Betreff und SHA-256 an einen vollstaendig
angezeigten Entwurf. Die Rohmail und jeder physische Anhang passieren ClamAV
fail-closed. `mail.forward-send` liest die Quelle vor SMTP erneut, verwirft einen
geanderten Digest und versendet nur den unveraenderten Entwurf nach nativer
Einmalfreigabe.

Das ZIP entsteht atomar in einer temporaeren mode-0600-Datei. Rohmail und
ZIP-Inhalt werden nicht gleichzeitig vollstaendig im Arbeitsspeicher gehalten;
die bestehenden Runtime-Limits bleiben unveraendert.

## Verifikation und Installation

- Verhaltenstests pruefen Quellauswahl, Betreffwache, vollstaendige Rohmail,
  physische Anhaenge, ClamAV-Fehler, Digestdrift und den Versand ohne Sent-Kopie.
- Der generierte native Werkzeugvertrag enthaelt getrennte Forward-Entwurfs- und
  Forward-Versandoperationen mit unveraenderter Allow-once-Grenze.
- Das Rollbackziel `r29.0.3` ist mit drei signierten, attestierten Rollenimages
  und einem verifizierten produktiven Releasebackup belegt.
- Veroeffentlichung und Installation verwenden den signierten Tag `r29.0.4`
  sowie drei unveraenderliche, attestierte Rollenimage-Digests.

Die produktive Abnahme ist erst erfolgreich, wenn Version, Quellrevision,
Status, Tools, Capabilities, Deep-Jobcheck, Antivirus und Agent-Tools aus dem
installierten Stack erfolgreich zurueckgelesen wurden. Bereits vor dem Update
vorhandene Ressourcenwarnungen werden dabei separat und unveraendert ausgewiesen.
