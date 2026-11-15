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
2. In der Erweiterungsoberflaeche **Nextcloud einrichten** oeffnen und auf der
   angezeigten Karte **Einrichtung in OpenClaw oeffnen** anklicken.
3. Im neuen Tab **Mit OpenClaw verbinden** waehlen. Die Seite verwendet dieselbe
   OpenClaw-Adresse und die vorhandene Browseranmeldung.

Der direkte Pfad auf derselben OpenClaw-Origin ist
`/plugins/personal-assistant-tools/nextcloud/setup`. Die Erweiterungsseite unter
`/plugins/personal-assistant-tools/nextcloud` enthaelt den Einstieg.
Die Karte liegt direkt in der OpenClaw-Oberflaeche. Ein Klick oeffnet die
Einrichtung in einem eigenen Tab unter derselben Adresse. Nach dem Update laedt
OpenClaw eine bereits geoeffnete Oberflaeche neu, sobald die aktualisierten
Browserassets aktiviert sind. Wird noch die alte Karte angezeigt, OpenClaw
online neu laden. Der direkte Pfad bleibt als Alternative verfuegbar.

Der gepinnte Core sperrt neue Tabs aus eingebetteten Pluginseiten. Deshalb
enthaelt das Runtime-Image eine eng begrenzte Buildanpassung fuer genau diese
Einstiegskarte; siehe [ADR-0051](architecture/adr/0051-openclaw-einrichtung-und-nextcloud-konto.md).
Andere Pluginseiten behalten ihre Sandbox. Die Einrichtung selbst verwendet
die bestehende OpenClaw-Geraeteanmeldung im neuen Tab. Sandbox und
Sicherheitsheader werden dafuer nicht abgeschwaecht.

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

Alternativ **Agentenkonto mit App-Passwort anmelden** aufklappen. Dieselbe
Serveradresse oben wird mit dem Nextcloud-Anmeldenamen und einem eigens fuer
diesen Assistant erstellten App-Passwort verwendet. Ein eigenes Agentenkonto
kann so ohne Browserwechsel verbunden werden. Das App-Passwort wird in Nextcloud
unter **Persoenliche Einstellungen > Sicherheit** erstellt; siehe die
[Nextcloud-Anmeldedokumentation](https://docs.nextcloud.com/server/stable/developer_manual/client_apis/LoginFlow/index.html).
Das normale Kontopasswort bleibt auf Nextcloud. Zugangsdaten nicht im Agentenchat
eingeben: Die Anmeldung ist ausschliesslich in der Administratoroberflaeche
verfuegbar.

**Anmeldedaten pruefen** leert das Passwortfeld sofort und prueft Benutzer und
Ressourcen ueber dieselbe abgesicherte HTTPS-Verbindung. Die Eingabe wird weder
im Browserstorage noch in Antworten oder Diagnosen gespeichert. Bis zur
bestaetigten Vorschau haelt nur der Einrichtungsdienst die Zugangsdaten
voruebergehend. Ein Abbruch, eine abgelaufene Vorschau oder eine fehlgeschlagene
Benutzerpruefung verwirft sie. Eine fehlgeschlagene Anmeldung ersetzt keine
vorhandene Verbindung; nach der Korrektur ist eine neue Anmeldung erforderlich.
Ressourcenfehler nach bestaetigter Benutzeridentitaet erscheinen dagegen einzeln
in der Vorschau.

Login Flow v2 liefert dem Backend ebenfalls ein App-Passwort. Das normale
Nextcloud-Passwort wird ausschliesslich auf Nextcloud eingegeben. Polltoken und
zurueckgeliefertes App-Passwort erscheinen weder im Agentenchat noch in den
Antworten der Einstellungsseite.
Die Verbindung wird mit Versionswache atomar gespeichert und erneut gelesen.
Parallele Kontoaenderungen erfordern eine aktuelle Vorschau; sie werden nicht
ueberschrieben.

TLS, die freigegebene Origin und ihre DNS-Adressen werden geprueft. Erreichbare
private LAN-Adressen sind moeglich. Loopback, Link-local, fremde Weiterleitungen
und ein Wechsel auf ungepruefte DNS-Ziele sind gesperrt. Eine Zertifikatswarnung
ist kein Anlass, TLS zu deaktivieren.

## Anmeldung und Zugriff pruefen

Die Einrichtung zeigt getrennte Ergebnisse fuer **Benutzer**, **Kalender**,
**Aufgabenlisten**, **Ordner** und **Talk**, einschliesslich Pruefzeitpunkt.
Ein gespeicherter Stand ist ausdruecklich als gespeichertes Pruefergebnis
gekennzeichnet. **Status neu laden** liest diesen Stand und die vorhandene
Talk-Workerbeobachtung; **Ressourcen neu erkennen** fuehrt neue Serverabfragen aus.
Die Workerbeobachtung behaelt ihren eigenen Zeitpunkt.

- **Benutzer:** Nur die erfolgreiche authentisierte Benutzerabfrage bestaetigt
  Identitaet und Anzeigenamen. Schlaegt sie fehl, bleiben nachfolgende Bereiche
  ungeprueft und die Verbindung kann nicht gespeichert werden.
- **Kalender und Aufgabenlisten:** Die Vollstaendigkeit der Sammlungsliste und
  der einzelne Lesezugriff sind getrennte Ergebnisse. Die Vorschau zeigt die
  Anzahl gefundener, lesbarer, fehlgeschlagener und noch ungepruefter Sammlungen.
  Eine eigene DAV-Abfrage prueft die Metadaten jeder geprueften Sammlung.
  Termin- und Aufgabeninhalte werden dabei nicht geladen. Fehlende oder unklare
  Rechte werden nicht als Leseerfolg gewertet. Prueflimits bleiben sichtbar;
  ein abgebrochener oder begrenzter Lauf belegt keine vollstaendige Erreichbarkeit.
- **Ordner:** Die erste Auflistung prueft den Kontohauptordner und erfasst seine
  unmittelbaren Unterordner. Sie belegt keinen rekursiven Zugriff auf alle
  Unterordner. **Ordner durchsuchen** prueft den konkret geoeffneten Pfad und
  zeigt dessen Serverrechte. Dateiinhalte werden nicht heruntergeladen.
- **Talk:** Capability-Antwort, authentisierter Zugriff auf die Raum-API und
  sichtbare Raeume mit ihren angekuendigten Senderechten werden getrennt
  ausgewertet. Der Check liest Raummetadaten, keine Chatnachrichten.
  Die fuer den Kontokanal erforderliche Capability `chat-reference-id` muss
  ausdruecklich vorliegen. Eine fehlgeschlagene oder unvollstaendige Abfrage
  beweist nicht, dass Talk oder weitere Raeume fehlen. Unbekannte Rechte gelten
  nicht als bestaetigte Sendeberechtigung. Zeitpunkt, Umfang, Fehler und der
  naechste sichere Schritt bleiben sichtbar.

Serverrechte fuer Lesen, Erstellen und Aendern werden pro Ressource getrennt
von der Lesepruefung angezeigt. Die Diagnose erzeugt keine Probeobjekte und
veraendert keine Serverrechte. Eine leere, vollstaendig gelesene Sammlungsliste
ist von einer fehlgeschlagenen oder unvollstaendigen Erkennung unterscheidbar.
Ein erfolgreicher Talk-Zugriffscheck aktiviert keinen Kanal und prueft keine
Nachrichtenzustellung. Die Entwicklungsregeln fuer diese getrennten Nachweise
gelten auch fuer kuenftige Integrationen; siehe
[Integrationsdiagnosen](INTEGRATION_DIAGNOSTICS.md).

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

Die Diagnose unterscheidet die gespeicherte Aktivierung, Raum- und Senderauswahl
von der tatsaechlichen Workerbeobachtung. Eine gespeicherte Senderauswahl ist
kein neuer Nachweis der aktuellen Raumteilnahme. Die Teilnehmer werden beim
Laden beziehungsweise Validieren der konkreten Auswahl geprueft.

Eine aktuelle Pollverbindung setzt einen wirklich erfolgreichen Nachrichtenpoll
fuer das passende Konto und die aktuelle Konfiguration voraus. Die Beobachtung
darf hoechstens 90 Sekunden alt sein. Auch die erfolgreiche erste Chatabfrage
zum Setzen des Startpunkts ohne Historienwiederholung zaehlt dazu; Prozessstart,
Rauminventur und gespeicherte Aktivierung reichen nicht. Die Beobachtung ist
keine neue Zugriffspruefung jedes ausgewaehlten Raums. **Status neu laden**
startet keinen Worker und sendet keine Testnachricht. Auch ein aktueller
erfolgreicher Poll belegt weder die Zustellung einer einzelnen Antwort noch
eine Benachrichtigung auf dem Telefon. Ein Ende-zu-Ende-Test bleibt ein
gesonderter ausdruecklicher Auftrag.

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
| Talk-Zugriff geprueft | Capability- und Raumabfragen belegen nur den angezeigten Umfang zum Pruefzeitpunkt. Gespeicherte Auswahl und Workerzustand getrennt pruefen. |
| Talk ausgeschaltet | Der gespeicherte Sollzustand ist AUS; dies ist kein Verbindungsfehler. Bei gewuenschter Nutzung die konkrete Raum-/Senderauswahl pruefen und aktivieren. |
| Talk aktiviert | Raum-/Senderauswahl ist gespeichert. Fuer die aktuelle Pollverbindung den unabhaengigen Workerbeleg pruefen. |
| Talk-Worker noch nicht beobachtet oder Konfiguration abweichend | Fuer die aktuelle Auswahl liegt noch kein passender Pollbeleg vor. Status erneut laden; bleibt der Zustand bestehen, die registrierte Talk-Diagnose verwenden. |
| Talk-Workerbeleg veraltet | Die Beobachtung ist aelter als 90 Sekunden und belegt weder laufendes Polling noch einen Ausfall. Status erneut laden und bei anhaltendem Zustand die Talk-Diagnose verwenden. |
| Talk-Poll fehlgeschlagen | Der aktuelle Versuch meldet einen Fehler. Fehlercode und sicheren naechsten Schritt beachten; nicht automatisch neu starten oder Rechte aendern. |
| Verarbeitung oder Zustellung ungewiss | Kein automatischer neuer Agentenlauf und kein automatischer Wiederholungsversand. Den belegten Zustand pruefen. |
| Konto-/Versionskonflikt | Aktuellen Status und Auswahl erneut laden; keinen alten Write wiederholen. |

Die Entwicklung prueft Anmeldung, Autorisierung, Ressourcenrechte, Unterpfade,
Kontowechsel, Geheimnisredaktion und Talk-Neustartverhalten hermetisch. Eine
erfolgreiche Testinstanz belegt keine Anmeldung an deiner produktiven Nextcloud.
Zur Abnahme des Einstiegs gehoert ein echter Browserklick auf die Hostkarte:
Er muss den eigenen Tab mit dem festen Einrichtungsziel oeffnen. Eine erfolgreiche
HTTP-Abfrage der HTML-Seite allein belegt diesen Ablauf nicht. Zusaetzlich muss
eine unveraenderte fremde Pluginseite weiterhin im bisherigen Sandbox-Iframe
laufen und ein Browser mit bereits gecachten Core-Assets die korrigierte Karte
nach dem Update laden koennen.
Architektur und Speichergrenzen stehen in [ADR-0051](architecture/adr/0051-openclaw-einrichtung-und-nextcloud-konto.md),
im [Datenkatalog](architecture/DATA_CATALOG.md) und in den
[Vertrauensgrenzen](architecture/TRUST_BOUNDARIES.md).
