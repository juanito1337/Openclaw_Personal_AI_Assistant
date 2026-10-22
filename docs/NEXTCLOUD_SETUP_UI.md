# Nextcloud in OpenClaw einrichten

Die neue Einrichtungsseite verbindet ein Nextcloud-Konto mit dem vorhandenen
Personal Assistant. Sie startet mit der Serveradresse und einer Anmeldung bei
Nextcloud. Kalender, Aufgabenlisten, Adressbuecher, Dateibereiche und Talk-Raeume
werden anschliessend automatisch erkannt. Die Serveradresse allein erteilt
keinen Zugriff.

Diese Anleitung beschreibt den Entwicklungsstand. Tests und lokale
Browserpruefungen sind keine produktive Aktivierung. Auslieferung, Backup,
Containerwechsel und die Verbindung zu einer echten Nextcloud bleiben ein
gesonderter Betriebsauftrag; siehe [Docker-Deployment](DOCKER_DEPLOYMENT.md).

## Einrichtungsseite oeffnen

1. OpenClaw wie gewohnt im Browser oeffnen und als Administrator anmelden.
2. In der Erweiterungsoberflaeche **Nextcloud einrichten** oeffnen und dem Link
   **Einrichtung in OpenClaw oeffnen** folgen.
3. Im neuen Tab **Mit OpenClaw verbinden** waehlen. Die Seite verwendet dieselbe
   OpenClaw-Adresse und die vorhandene Browseranmeldung.

Der direkte Pfad auf derselben OpenClaw-Origin ist
`/plugins/personal-assistant-tools/nextcloud/setup`. Die Erweiterungsseite unter
`/plugins/personal-assistant-tools/nextcloud` enthaelt den Einstieg.
Beim gepinnten Core laeuft die angemeldete Einrichtung bewusst in einem eigenen
Tab: Sein Sandbox-Iframe stellt die benoetigte Browserauthentisierung nicht
zuverlaessig bereit. Sicherheitsheader werden dafuer nicht abgeschwaecht.

Eine oeffentlich ladbare HTML-Seite gewaehrt keinen administrativen Zugriff.
Kontodaten und Aenderungen gehen ausschliesslich ueber den authentisierten,
administrativ autorisierten Gatewayaufruf. Fehlende Anmeldung oder
Administratorrechte blockieren die Bedienung.

## Konto verbinden

1. Die HTTPS-Basisadresse eingeben, zum Beispiel `https://cloud.example.de` oder
   `https://cloud.example.de/nextcloud`. Ein eigener Port ist moeglich. Keine
   WebDAV-Unteradresse, Zugangsdaten oder Suchparameter in die URL eintragen.
2. **Bei Nextcloud anmelden** waehlen. Den angezeigten Nextcloud-Anmeldelink
   oeffnen und das Konto anmelden, dessen Ressourcen der Assistant verwenden
   soll. Vorhandene Zwei-Faktor-Anmeldung erfolgt auf Nextcloud.
3. Zum Einrichtungstab zurueckkehren und **Anmeldung pruefen** waehlen. Die
   Anmeldung kann noch ausstehen, ablaufen oder ueber **Abbrechen** verworfen
   werden.
4. Konto, Ressourcen und ermittelte Serverrechte in der Vorschau pruefen.
   Die Kontofreigabe bestaetigen und **Verbindung bestaetigen** waehlen.

Login Flow v2 liefert dem Backend ein App-Passwort. Das normale Nextcloud-Passwort
wird ausschliesslich auf Nextcloud eingegeben. Polltoken und App-Passwort bleiben
im Backend und erscheinen weder im Agentenchat noch in der Einstellungsseite.
Die Verbindung wird mit Versionswache atomar gespeichert und erneut gelesen.
Parallele Kontoaenderungen erfordern eine aktuelle Vorschau; sie werden nicht
ueberschrieben.

TLS, die freigegebene Origin und ihre DNS-Adressen werden geprueft. Erreichbare
private LAN-Adressen sind moeglich. Loopback, Link-local, fremde Weiterleitungen
und ein Wechsel auf ungepruefte DNS-Ziele sind gesperrt. Eine Zertifikatswarnung
ist kein Anlass, TLS zu deaktivieren.

## Ressourcen verwenden

Die Kontofreigabe umfasst die fuer dieses Konto verfuegbaren Ressourcen und
deren tatsaechliche Rechte. Es wird kein erster Kalender automatisch als
Schreibziel ausgewaehlt. Gleichnamige Sammlungen bleiben anhand ihrer stabilen,
an Server und Konto gebundenen Ressourcen-IDs unterscheidbar.

- **Ressourcen neu erkennen** aktualisiert Kalender, Aufgabenlisten,
  Adressbuecher und Talk-Raeume. Erkennungsfehler bleiben pro Bereich sichtbar.
- **Ordner durchsuchen** laedt Unterordner bei Bedarf. Das Konto ist nicht auf
  den historischen Ordner `Assistent/` beschraenkt. Diese Ansicht laedt keine
  Dateiinhalte herunter.
- Listen und Suchen koennen mehrere freigegebene Sammlungen umfassen. Jede Zeile
  behaelt ihre `resource_id`. `complete`, `resource_errors` und
  `results_may_be_truncated` zeigen Grenzen der Antwort.
- Bei mehreren Kalendern, Aufgabenlisten oder Adressbuechern benoetigt ein Write
  genau eine aktuelle Ressourcen-ID. Exakte UID, ETag, ActionPlan und die
  jeweilige Freigabe gelten weiterhin.

Registrierte CLI-Beispiele fuer Diagnose und Auswahl:

```bash
./scripts/assistant.sh resources status
./scripts/assistant.sh calendar list --limit 100
./scripts/assistant.sh tasks list --resource "<resource_id>" --include-completed --limit 100
./scripts/assistant.sh tasks update --resource "<resource_id>" --uid "<UID>" --expected-title "<Titel>" --status COMPLETED --yes
```

Im Agentenbetrieb werden diese Fachoperationen ueber die nativen typisierten
Tools ausgefuehrt. `--resource` ist fuer die bestehende Einzelressourcen-
Konfiguration optional; bei mehreren Schreibzielen ist es erforderlich. Ein
Kontowechsel stoppt laufende Fachablaeufe vor ihrem naechsten Request und macht
alte Ressourcen-IDs ungeeignet fuer neue Writes.

Die bisherigen `calendar configure`, `tasks configure` und `contacts configure`
bleiben der Legacy-Konfiguration vorbehalten. Bei einer GUI-verwalteten
Kontoverbindung verweisen sie auf die Einrichtungsoberflaeche. Automatische
Mail-Kalender-, Rechnungs- und Deck-Ziele werden durch einen Kontowechsel nicht
stillschweigend auf das neue Konto umgebogen.

## Talk verbinden

Das Konto muss Zugriff auf die gewuenschten Talk-Raeume besitzen. Falls noetig,
wird es zuerst in Nextcloud zu einem Raum hinzugefuegt; die Einrichtung legt
keinen Bot an und veraendert keine Raumrechte.

1. Die Ressourcen gegebenenfalls neu erkennen.
2. Im Talk-Bereich die genauen Raeume auswaehlen, in denen der Assistant
   antworten darf.
3. **Teilnehmer der ausgewaehlten Raeume laden** waehlen und die erlaubten
   Benutzer auswaehlen.
4. **Talk fuer diese Auswahl aktivieren** markieren und die Einstellung speichern.

Der Kontokanal fragt Nachrichten ausgehend bei Nextcloud ab. Er benoetigt weder
einen eingehenden Webhook noch Nextcloud-Administratorrechte fuer eine
Botinstallation. Talk muss die Capability `chat-reference-id` anbieten; nur
lesbare Raeume koennen nicht fuer Antworten aktiviert werden. Der bisherige
Bot-/Webhookkanal bleibt eine separate Variante und darf denselben Raum nicht
gleichzeitig bearbeiten.

Beim ersten Pairing wird vorhandene Chathistorie nicht als neue Anweisung
ausgefuehrt. Zugelassene neue Textnachrichten werden vor der Verarbeitung
dauerhaft angenommen. Eigene Antworten werden anhand ihrer Nachrichten-IDs
und Referenzen erkannt; damit kann auch das Handy dasselbe Konto verwenden.
Medien, Dateien und unzugelassene Sender werden nicht zu Agentenauftraegen.

Talk verwendet denselben Assistant und dieselben Fachwerkzeuge. Konkrete
Aenderungen behalten ihre Freigaben; der neue Textkanal ersetzt keine nativen
Allow-once-Dialoge durch ungebundene Chatkommandos. Die administrativen
Einstellungsoperationen sind keine Agentenwerkzeuge. Im GUI-faehigen
Produktionsprofil sind generische Shell- und Dateitools fuer den Agenten hart
gesperrt, damit er den administrativen Kontrollpfad nicht aufrufen kann.

## Status und Fehler

| Anzeige oder Zustand | Bedeutung und naechster Schritt |
| --- | --- |
| Keine Verbindung gespeichert | Anmeldung starten und die erkannte Kontoverbindung bestaetigen. |
| Anmeldung ausstehend/abgelaufen | Auf Nextcloud bestaetigen oder eine neue Anmeldung starten. |
| Verbindung gespeichert | Die letzte gepruefte Kontobindung liegt vor; Zeitpunkt und Erkennungsfehler beachten. |
| Erkennung unvollstaendig | Angezeigte Treffer sind nutzbar; das Fehlen anderer Ressourcen ist nicht bewiesen. Betroffenen Bereich pruefen und neu erkennen. |
| Talk aktiviert | Raum-/Senderauswahl ist gespeichert. Erst eine aktuelle Workerbeobachtung belegt die Verbindung. |
| Talk nicht verbunden/Workerbeleg veraltet | Diagnose zeigt keine aktuelle Zustellung. Ein gespeichertes Aktivierungsflag ist kein Gesundheitsnachweis. |
| Verarbeitung oder Zustellung ungewiss | Kein automatischer neuer Agentenlauf und kein automatischer Wiederholungsversand. Den belegten Zustand pruefen. |
| Konto-/Versionskonflikt | Aktuellen Status und Auswahl erneut laden; keinen alten Write wiederholen. |

Die Entwicklung prueft Anmeldung, Autorisierung, Ressourcenrechte, Unterpfade,
Kontowechsel, Geheimnisredaktion und Talk-Neustartverhalten hermetisch. Eine
erfolgreiche Testinstanz belegt keine Anmeldung an deiner produktiven Nextcloud.
Architektur und Speichergrenzen stehen in [ADR-0051](architecture/adr/0051-openclaw-einrichtung-und-nextcloud-konto.md),
im [Datenkatalog](architecture/DATA_CATALOG.md) und in den
[Vertrauensgrenzen](architecture/TRUST_BOUNDARIES.md).
