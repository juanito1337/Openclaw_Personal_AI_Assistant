# M17-Roadmap: Mobile Agentenkommunikation ueber Nextcloud Talk

Status: geplant. Diese Roadmap beschreibt Entwicklung, Abnahme und den
kontrollierten produktiven Rollout. Sie installiert kein Plugin, legt keinen
Nextcloud-Bot an, aendert keine Secrets und aktiviert keinen produktiven Kanal.

## Ziel

Jan soll den Personal Assistant aus der nativen Nextcloud-Talk-App auf Android
oder iOS erreichen koennen. Das Telefon greift dabei ueber WireGuard auf die
bereits selbst betriebene Nextcloud zu. Nextcloud Talk stellt dem Assistant
signierte Webhook-Ereignisse zu und zeigt dessen Antworten im selben Chat an.

Die Loesung verwendet den offiziellen OpenClaw-Kanal
`@openclaw/nextcloud-talk`. Es wird keine eigene Mobile-App und kein WebView
entwickelt. Das OpenClaw-Gateway bleibt fuer das Telefon unsichtbar und wird
nicht als allgemeine LAN-, WireGuard- oder Internet-API freigegeben.

Der erste produktive Umfang ist bewusst klein:

- ein dediziertes Botkonto beziehungsweise ein Talk-Webhook-Bot;
- genau Jans Nextcloud-Benutzer als zugelassener Direktchat-Partner;
- Textnachrichten, Markdown, Antwortbezug und Statusreaktionen;
- bestehende typisierte Agentenwerkzeuge und deren unveraenderte
  Sicherheitsregeln;
- belegte Zustellung, Diagnose, Audit und kontrollierter Rollback;
- native Talk-App auf dem von Jan verwendeten Telefon.

## Nicht-Ziele

- keine neu entwickelte Android-, iOS- oder Flutter-App;
- keine direkte Verbindung der App zum Gateway-Port `18789`;
- kein oeffentliches OpenClaw-Gateway und kein ungefilterter Reverse Proxy;
- keine Abschwaechung von ActionPlan, ETag, Single-Writer, Antivirus,
  Freigabe- oder Evidenzregeln;
- kein automatisches Installieren oder Aktualisieren der Nextcloud-Talk-App;
- keine automatische Nextcloud-, Talk- oder OpenClaw-Core-Aktualisierung;
- keine Gruppen, Gastzugriffe, Federation oder mehrere Benutzer im ersten
  produktiven Schnitt;
- keine Sprachsteuerung und keine Verarbeitung eingehender Dateien im MVP;
- kein Versand beliebiger lokaler Dateien ueber Talk;
- kein produktives Deployment als Nebenwirkung einer Entwicklungsabnahme.

## Verifizierter Ist-Stand

- Der produktive Gateway-Port wird im Compose-Vertrag standardmaessig nur an
  Host-Loopback publiziert. Diese Voreinstellung bleibt erhalten.
- Das Runtime-Image enthaelt derzeit die exakt gepinnten externen Plugins Brave
  und Signal. `@openclaw/nextcloud-talk` ist noch kein Buildinput und darf nicht
  zur Laufzeit in den beschreibbaren Gateway-State installiert werden.
- Gateway-Konfiguration und Pluginpfade unterliegen dem unveraenderlichen
  Image-, Migrations- und Lieferkettenvertrag.
- Der offizielle Nextcloud-Talk-Kanal verwendet einen signierten Webhook. Die
  zum eingebetteten Core passende Version `2026.7.1` startet noch einen eigenen
  Listener auf Port `8788`; die aktuelle Upstream-Dokumentation beschreibt
  bereits eine neuere, hier nicht verfuegbare Gatewayroute.
- Die Talk-Integration unterstuetzt Direktnachrichten, Raeume, Reaktionen und
  Markdown. Ausgehende Medien werden nur als URL dargestellt; native
  Kanalbefehle und interaktive Approval-Schaltflaechen sind nicht als
  vorausgesetzte Funktion belegt.
- Der aktuelle Freigabevertrag akzeptiert ein kurzes `JA` oder `YES` nur fuer
  genau eine unveraenderte, noch gueltige Aktion der Risikostufe 2 in derselben
  Sitzung. Risikostufe 3 behaelt die gebundene native Allow-once-Freigabe oder
  den exakten technischen Fallback `/approve <ID> allow-once`.

Vor M17.2 muss die Kompatibilitaet der zum eingebetteten OpenClaw-Core passenden
Pluginversion reproduzierbar belegt werden. Die aktuelle Upstream-Dokumentation
allein autorisiert weder ein Core-Upgrade noch die Installation eines
ungepinnten `latest`-Pakets.

## Zielarchitektur

```text
Native Nextcloud-Talk-App
          |
          | HTTPS ueber WireGuard
          v
Selbst betriebene Nextcloud + Talk
          |
          | signierter Bot-Webhook, serverseitiger Privatpfad
          v
HTTPS-Reverse-Proxy: exakt /nextcloud-talk-webhook
          |
          | Host-Loopback 127.0.0.1:8788
          v
separater Listener im Gatewaycontainer + gepinntes Talk-Plugin
          |
          +--> bestehender Agent, Toolkatalog und Approval-Guard
          +--> Antwort ueber die Talk-API
```

WireGuard schuetzt den Zugriff des Telefons auf Nextcloud. Der Rueckkanal von
Nextcloud zum Bot ist eine getrennte Server-zu-Server-Verbindung. Er muss nicht
ueber das Telefon und darf nicht versehentlich das gesamte Gateway exponieren.

Der Listener bindet im Gatewaycontainer auf `0.0.0.0:8788`, wird von Docker aber
nur auf `127.0.0.1:8788` des Hosts publiziert. Ein hostlokaler Reverse Proxy
uebernimmt TLS und exponiert ausschliesslich den exakten Webhookpfad fuer den
Nextcloud-Server. Firewall und Proxy begrenzen Quelle, Methode, Pfad, Groesse
und Rate. `OPENCLAW_GATEWAY_BIND_ADDRESS` und Port `18789` bleiben unveraendert.
Die Entscheidung ist in ADR-0050 dokumentiert.

## Verbindliche Sicherheits- und UX-Grenzen

- Der Bot besitzt eine eigene zufaellige HMAC-Identitaet. Botsecret und
  optionales Nextcloud-App-Passwort sind getrennt, dateibasiert, nur fuer das
  Gateway lesbar und erscheinen nie in Git, Logs, Chat oder Testfixtures.
- `dmPolicy=pairing` ist die Ausgangseinstellung. Nach belegter Paarung darf nur
  Jans normalisierte Nextcloud-Benutzer-ID Nachrichten ausloesen.
- `groupPolicy=disabled` bleibt bis zu einer spaeteren, eigenen Freigabe aktiv.
- Displayname, Raumname und Nachrichtentext sind unvertrauenswuerdige Daten und
  nie Identitaets- oder Autorisierungsbelege.
- Webhook-HMAC, Replay-/Deduplizierungszustand, Senderpolicy und Toolfreigabe
  sind voneinander unabhaengige Schranken. Ein HTTP 200 bestaetigt nur die
  dauerhafte Ereignisannahme, nicht die Ausfuehrung eines Agententurns.
- Der Reverse Proxy exponiert weder `/api/channels`, Gateway-RPC, Healthdaten
  noch andere Gatewaypfade. Fehlerantworten enthalten keine Secrets oder
  Nachrichteninhalte.
- Ein eingehender Talk-Text ist Benutzerinhalt, aber keine pauschale
  Schreibfreigabe. Der bestehende risikobasierte Toolvertrag gilt kanalgleich.
- Ein kurzes `JA` darf nur die bereits definierte eindeutige Risikostufe-2-
  Bestaetigung ausloesen. Bei null, mehreren, abgelaufenen oder veraenderten
  Kandidaten wird fail-closed abgebrochen.
- Fuer Risikostufe 3 wird im MVP keine neue Textabkuerzung erfunden. Falls der
  Kanal keine sichere native Schaltflaeche transportiert, zeigt der Agent den
  menschenlesbaren Vorgang und den bestehenden exakten Fallback an.
- Keine unsichere oder unklare Zustellung wird automatisch wiederholt. Empfang,
  Verarbeitung, Werkzeugausfuehrung und Antwortversand bleiben getrennte
  Zustandsphasen.
- Eingehende Dateien und Sprache sind im MVP deaktiviert. Eine spaetere
  Aktivierung erfordert Dateigroessenlimit, vollstaendigen ClamAV-Scan,
  Content-Type-Pruefung, kontrollierte Ablage und eigene Negativtests.

## Paketuebersicht und Reihenfolge

| Paket | Ergebnis | Voraussetzung |
| --- | --- | --- |
| M17.0 | belegte Infrastruktur- und Kompatibilitaetsbaseline | keine |
| M17.1 | ADR, Threat Model und festgelegte private Topologie | M17.0 |
| M17.2 | reproduzierbar gepinntes Talk-Plugin im Runtime-Image | M17.1 |
| M17.3 | sichere Konfiguration, Secrets, Migration und Diagnose | M17.2 |
| M17.4 | Identitaet, Routing und approval-sichere Chat-UX | M17.3 |
| M17.5 | mobile App, WireGuard und Benachrichtigungsabnahme | M17.4 |
| M17.6 | hermetische und reale End-to-End-Abnahme | M17.5 |
| M17.7 | signiertes Patch-Release und Main-Promotion | M17.6 |
| M17.8 | separat freigegebener Produktivrollout und Beobachtung | M17.7 |

## M17.0 – Infrastruktur- und Kompatibilitaetsbaseline

Status: am 2026-10-05 lokal und gegen die laufende Runtime read-only erhoben.
Android, Nextcloud `35.0.1` und die aktivierte Talk-App `25.0.5` wurden am
2026-10-06 von Jan bestaetigt. Wegen des noch nicht festgelegten privaten
Reverse-Proxy- und Webhookpfads bleibt der Status
`M17.0 TEILWEISE ABGENOMMEN`. Details:
[`MOBILE_NEXTCLOUD_TALK_BASELINE_M170.md`](MOBILE_NEXTCLOUD_TALK_BASELINE_M170.md).

### Ziel

Alle Voraussetzungen erfassen, ohne Nextcloud, Gateway oder produktive
Konfiguration zu veraendern.

### Scope

- Produktrelease, Git-Stand, eingebettete Coreversion, Pluginvertrag,
  Rollenimages und Gatewaybindung reproduzierbar erfassen.
- Read-only feststellen, ob Nextcloud Talk serverseitig vorhanden und mit der
  installierten Nextcloud-Version kompatibel ist.
- Netzwerkweg Telefon -> WireGuard -> Nextcloud sowie Nextcloud -> geplanter
  Webhook dokumentieren; DNS, TLS, Zertifikatskette und Reverse-Proxy-Eigner
  identifizieren.
- Das primaere Telefonbetriebssystem festhalten. Das andere Betriebssystem
  bleibt dokumentiert, ist aber kein Releaseblocker.
- Die zum eingebetteten Core passende Version und Integritaet von
  `@openclaw/nextcloud-talk` bestimmen. Ein erforderliches Coreupgrade wird als
  eigener Befund behandelt und nicht still in M17 aufgenommen.
- Ausgangswerte fuer Gateway-Speicher, Startzeit, Turnlatenz und Imagegroesse
  messen. M17 erhoeht keine Runtime-Limits.

### Pflichttests und Abnahme

- Baseline enthaelt keine Secrets, Tokens, persoenlichen Chattexte oder
  produktiven Webhook-Payloads.
- Jede Voraussetzung ist `vorhanden`, `fehlt`, `inkompatibel` oder
  `nicht gemessen`; Unbekanntes wird nicht als Erfolg gewertet.
- Es wurde kein Plugin installiert, kein Bot angelegt, kein Port geoeffnet und
  kein produktiver Dienst neu gestartet.

## M17.1 – ADR, Threat Model und private Ingresstopologie

### Ziel

Den kleinsten erreichbaren und rueckrollbaren Netzwerkpfad festlegen.

### Scope

- ADR fuer Nextcloud Talk als mobilen Primaerkanal anlegen.
- Trust Boundaries fuer Telefon, WireGuard, Nextcloud, Reverse Proxy,
  Webhook-Queue, Gateway, Agentensitzung und Talk-API dokumentieren.
- Eine der beiden erlaubten Topologien waehlen und die abgelehnte Alternative
  begruenden.
- Exakten HTTPS-Pfad, Quellnetz/IP-Regel, zulässige Methode, Bodylimit,
  Zeitlimits, Rate-/Parallelitaetsgrenzen und Logging-Redaktion definieren.
- Zertifikats- und Namensmodell festlegen. Selbst signierte Zertifikate sind nur
  mit explizit verwalteter privater CA zulaessig; TLS-Pruefung bleibt aktiv.
- Ausfall-, Replay-, DoS-, Prompt-Injection-, Identitaetsverwechslungs- und
  Secretabfluss-Szenarien modellieren.
- Rollback definieren: Kanal deaktivieren, Proxyroute entfernen, Bot deaktivieren
  und vorheriges Image starten, ohne Talk-Verlauf oder fremde Nextcloud-Daten zu
  loeschen.

### Pflichttests und Abnahme

- Telefon kann keinen anderen Gatewaypfad adressieren.
- Ein nicht signierter, falsch signierter oder wiederholter Webhook erreicht
  keinen Agententurn.
- Der Gateway-Port bleibt standardmaessig an Loopback gebunden oder besitzt eine
  begruendete, testbare dedizierte Ingressgrenze.
- Der Plan benoetigt weder Docker-Socket noch Gateway-Credential in Nextcloud.

## M17.2 – Reproduzierbare Plugin-Lieferkette

### Ziel

Das offizielle Plugin unveraenderlich und nachvollziehbar ins Runtime-Image
aufnehmen.

### Scope

- `@openclaw/nextcloud-talk` auf eine zum Core passende exakte Version pinnen.
- npm-Lockdatei, Pluginvertrag und Supply-Chain-Lock mit Registry-URL,
  Integritaet und Paket-Hash erweitern.
- Plugin ausschliesslich in das Runtime-Target aufnehmen; Proxy- und
  Maintenance-Images bleiben frei davon.
- Generierten Pluginindex deterministisch auf den read-only Imagepfad
  normalisieren. Keine aus dem Gateway-State geladene Laufzeitkopie zulassen.
- Rollen-, Offline-, Immutable-, SBOM-, Provenance-, CVE-, Secret- und
  Reproduzierbarkeitstests erweitern.
- Imagegroesse, Startzeit und Gateway-Speicher gegen M17.0 vergleichen.

### Pflichttests und Abnahme

- Zwei saubere Builds verwenden dieselbe Paketidentitaet und erzeugen die
  geforderte reproduzierbare Artefaktevidenz.
- Manipulierte Version, Integritaet, Registryquelle oder Pluginpfad wird vor dem
  Gatewaystart abgelehnt.
- Das Gateway versucht weder npm-Zugriff noch Pluginmutation zur Laufzeit.
- Es gibt keine Erhoehung der Runtime-, PIDs- oder CPU-Limits.

## M17.3 – Konfiguration, Secrets, Migration und Diagnose

### Ziel

Eine deklarative, fail-closed und rueckrollbare Kanalkonfiguration schaffen.

### Scope

- Dateibasiertes Botsecret und optionales App-Passwort in den bestehenden
  geschuetzten Secretvertrag aufnehmen; Mount nur fuer das Gateway.
- Konfigurationsschema fuer `baseUrl`, `botSecretFile`, `webhookPath`,
  `webhookPublicUrl`, `dmPolicy`, `allowFrom`, `groupPolicy`, Verlaufslimits und
  private SSRF-Ausnahme festlegen.
- `dangerouslyAllowPrivateNetwork` nur fuer die exakt festgelegte interne
  Nextcloud-URL zulassen; keine allgemeine private Zielerlaubnis daraus ableiten.
- Operator-Setup als kurzlebigen, explizit freizugebenden Migrationspfad
  implementieren. Der normale Gatewaystart bleibt ohne Config-Schreibrecht.
- Status-/Doctor-Ausgabe fuer Plugin, Webhooklistener, HMAC-Konfiguration,
  Nextcloud-Erreichbarkeit, Botfeatures und Policy bereitstellen, ohne Secretwerte
  auszugeben.
- Backup und Rollback fuer Konfiguration und Pluginindex vorsehen.

### Pflichttests und Abnahme

- Fehlendes, leeres, symlink-basiertes oder falsch berechtigtes Secret stoppt
  den Kanal, nicht das gesamte Datenmodell.
- Unbekannte Konfigurationsfelder, unsichere URL, falscher Pfad und widerspruech-
  liche Listenerkonfiguration scheitern vor Aktivierung.
- Logs, Status, Doctor, Backupmetadaten und Supportbundle enthalten keine
  Secretwerte oder Nachrichteninhalte.
- Migration ist idempotent und kann das vorherige funktionsfaehige Gatewayprofil
  wiederherstellen.

## M17.4 – Identitaet, Routing und approval-sichere Chat-UX

### Ziel

Nur Jans authentisierte Nachrichten dem bestehenden Hauptagenten zuordnen und
den aktuellen Freigabevertrag verstaendlich im Chat abbilden.

### Scope

- Bot in einem dedizierten Talk-Dialog aktivieren; keine automatische
  Raumaktivierung.
- Pairing gegen die stabile, normalisierte Nextcloud-Benutzer-ID testen und den
  Command-Owner explizit setzen. Displaynamen autorisieren nichts.
- Alle Nachrichten dieses Direktdialogs konsistent derselben vorgesehenen
  Agentensitzung zuordnen; Bot-Loops und Cross-User-Session-Mischung verhindern.
- Lange Antworten begrenzt und an Absatzgrenzen teilen; Antwortbezug und
  Statusreaktionen ohne Vervielfachung von Toolresultaten pruefen.
- Risikostufe 0 und bereits konfigurierte Risikostufe 1 ohne Zusatzdialog
  verwenden. Exakte aktuelle Ausfuehrungswuensche und eindeutiges `JA` fuer
  Risikostufe 2 gemaess bestehendem Vertrag testen.
- Risikostufe 3 menschenlesbar mit Empfaenger/Ziel, Wirkung, Aenderungen und
  Ablauf darstellen. Der sichere Fallback bleibt exakt gebunden; fehlende
  Talk-Schaltflaechen fuehren nicht zu einer schwächeren Freigabe.
- Optional einen spaeteren kanalgebundenen Approval-Adapter prototypisieren.
  Er darf nur nach eigener ADR und Negativtests freigegeben werden und muss
  Benutzer-ID, Sitzung, Reply-Bezug, Nonce, Toolcall, Operation,
  Argumentdigest, Ablauf und Single-use kryptografisch beziehungsweise
  serverseitig unveraenderbar binden.

### Pflichttests und Abnahme

- Ungepaarter Benutzer, falsche Benutzer-ID, anderer Raum und nicht erwaehnte
  Gruppenbots erzeugen keinen Agententurn.
- Null, mehrere, abgelaufene, wiederholte oder digest-veraenderte
  `JA`-Kandidaten werden abgelehnt.
- Risikostufe 3 kann nicht durch blosses `JA`, Reaktion, Zitat oder manipulierte
  Botnachricht freigegeben werden.
- Kanalwechsel uebertraegt weder Pending-Approval noch Toolcall-Evidenz.
- Ein Sendetimeout wird als zustellungsunsicher gemeldet und nicht automatisch
  wiederholt.

## M17.5 – Mobile App, WireGuard und Benachrichtigungen

### Ziel

Den echten Nutzungsweg auf Jans Telefon pruefen, ohne eine eigene App zu bauen.

### Scope

- Offizielle Nextcloud-Talk-App aus dem regulaeren Store installieren und mit
  der bestehenden Nextcloud verbinden; App-Version und Quelle dokumentieren.
- Zugriff bei aktivem WireGuard, WLAN-/Mobilfunkwechsel, Display-Sperre,
  App-Neustart und laengerem Hintergrundbetrieb testen.
- Benachrichtigungsweg des tatsaechlichen Betriebssystems dokumentieren.
  Pushdienste sind Transporthilfen und erhalten keine Agenten- oder Botsecrets.
- Verhalten bei deaktiviertem WireGuard klar anzeigen: offline beziehungsweise
  spaetere Synchronisation statt scheinbar erfolgreicher Zustellung.
- Text, Umlaute, Emojis, Markdown, lange Antworten, Zitate und Reaktionen testen.
- Lokale App-Sperre beziehungsweise Betriebssystemschutz empfehlen; auf dem
  Sperrbildschirm keine sensiblen Nachrichtenvorschauen voraussetzen.

### Pflichttests und Abnahme

- Chat und Antwort funktionieren im vorgesehenen WireGuard-Pfad auf dem
  primaeren Telefon.
- Ein Netzwechsel erzeugt keine doppelte Werkzeugausfuehrung und keine
  verlorene bereits dauerhaft angenommene Nachricht.
- Benachrichtigungen werden fuer `App offen`, `Hintergrund`, `Display gesperrt`
  und `WireGuard aus` getrennt bewertet; fehlende Pushzustellung wird nicht als
  fehlende Chatnachricht behauptet.
- Die App benoetigt weder Gatewaytoken noch direkten Gatewayzugriff.

## M17.6 – Hermetische und reale End-to-End-Abnahme

### Ziel

Den vollstaendigen Kanal unter Fehlern, Neustarts und Sicherheitsangriffen
belegen.

### Scope

- Hermetischen Fake-Talk-Server fuer HMAC, Webhookannahme, Deduplizierung,
  Reply-API, Timeout, Fehler und Rate-Limit aufbauen.
- Einen isolierten Nextcloud-Talk-Teststack oder eine ausdruecklich freigegebene
  Staginginstanz fuer den realen Botvertrag verwenden.
- Gatewayrestart zwischen dauerhafter Annahme und Verarbeitung testen.
- Mehrfachzustellung, Reihenfolgewechsel, grosse Payload, langsamer Client,
  falschen Content-Type, ungueltiges JSON und parallele Webhooks testen.
- Prompt-Injection in Nachricht, Displayname, Raumname und zitierter Nachricht
  testen; Remote-Inhalt darf keine Policy oder Freigabe ersetzen.
- Vollstaendigen Repository-, Wheel-, Rollenimage-, Compose-, Manifest-,
  Dokument-, SBOM-, Provenance-, Scan- und Reproduzierbarkeitspfad ausfuehren.
- Turnlatenz, Fehlerquote, Dedupequote, Peak-RSS und Imagegroesse gegen M17.0
  berichten.

### Pflichttests und Abnahme

- Text-Roundtrip, Antwortbezug, langer Text, Restart-Recovery und eindeutige
  Risikostufe-2-Bestaetigung sind gruen.
- Alle Identitaets-, HMAC-, Replay-, Approval-, SSRF-, Pfad-, Secret- und
  Rate-Limit-Negativtests sind gruen.
- Keine Runtime-Grenze wurde erhoeht und keine bestehende Mail-, Nextcloud-,
  Scheduler-, ClamAV- oder Toolorchestrierungspruefung regressiert.
- Das Urteil lautet eindeutig `M17 TECHNISCH ABGENOMMEN` oder
  `M17 NICHT ABGENOMMEN`.

## M17.7 – Signiertes Patch-Release und Main-Promotion

### Ziel

Den technisch abgenommenen Stand als nachvollziehbares Patch-Release liefern,
ohne ihn automatisch produktiv zu aktivieren.

### Scope

- Releaseidentitaet, Changelog, Releasebericht, AGENTS, Skillreferenzen,
  Architekturunterlagen, Komponenteninventar und Quellmanifest konsistent
  aktualisieren.
- Runtime-, Proxy- und Maintenance-Image aus demselben exakten Commit bauen;
  unveraenderte Rollen trotzdem gemeinsam pruefen und attestieren.
- Signierten Git-Tag, OCI-Digests, SBOM, Provenance und Cosign-Signaturen
  verifizieren.
- Nur den exakt getesteten Commit ohne Historienumschreibung nach `main`
  promoten.
- Rollbackmanifest auf das vorherige verifizierte Release erstellen.

### Pflichttests und Abnahme

- Git-Commit, Tag, Release, Manifest, Image-Revision, Attestierung und Signatur
  stimmen exakt ueberein.
- Registryartefakte sind digestgebunden; ein beweglicher Tag ist kein
  Deploynachweis.
- Weder Botanlage noch Nextcloud-Konfigurationsaenderung noch produktiver
  Gatewayrestart sind Teil dieser Stufe.

## M17.8 – Separater Produktivrollout und Beobachtungsfenster

### Ziel

Den Kanal nach eigener ausdruecklicher Betriebsfreigabe mit minimalem Risiko
aktivieren und den Erfolg auf dem echten Telefon belegen.

### Scope

- Verifiziertes Backup und Rollbackfaehigkeit vor jeder Aenderung nachweisen.
- Signierte Images per Digest deployen und den bestehenden read-only
  Gateway-/Fachfunktionscanary ausfuehren.
- Nextcloud-Bot mit zufaelligem Secret und den Features `webhook`, `response`
  und `reaction` anlegen; nur im dedizierten Dialog aktivieren.
- Exakte Proxyroute und Firewallregel aktivieren, danach Kanalstatus und
  Signaturpfad pruefen.
- Jan einmalig paaren, stabilen Benutzerbeleg kontrollieren und Gruppen
  deaktiviert lassen.
- Canary-Reihenfolge: Status -> unkritische Frage -> belegte Read-only-Abfrage
  -> eindeutige Risikostufe-2-Testaktion in einer kontrollierten Fixture.
  Kein echter Mailversand und keine unnoetige externe Aenderung als Canary.
- Mindestens 24 Stunden Fehler, Latenz, Deduplizierung, Speicher,
  Benachrichtigungen und bestehende Gatewaygesundheit beobachten.
- Bei Sicherheits-, Identitaets-, Zustellungs- oder Ressourcenabweichung Kanal
  deaktivieren und dokumentierten Rollback ausfuehren.

### Pflichttests und Abnahme

- Produktives Backup, Image- und Konfigurationsrollback sind verifiziert.
- Nur Jans Benutzer kann den Bot ausloesen; Gruppen und Gaeste bleiben gesperrt.
- Das Telefon kommuniziert ueber die native App und WireGuard, nicht mit dem
  Gatewayport.
- Read-only- und kontrollierter Approval-Canary besitzen aktuelle Evidenz und
  eindeutige Postconditions.
- Nach dem Beobachtungsfenster lautet das Urteil eindeutig
  `M17 PRODUKTIV ABGENOMMEN` oder `ROLLBACK AUSGEFUEHRT`.

## Verbindliche Testmatrix

| Bereich | Positive Pruefung | Negative Pruefung |
| --- | --- | --- |
| Plugin | gepinnte kompatible Version | latest, falsche Integritaet, Runtimeinstallation |
| Ingress | exakter HTTPS-Webhookpfad | anderer Pfad, Methode, Quelle, Uebergroesse |
| HMAC | gueltige signierte Nachricht | fehlend, falsch, Replay, veraenderter Body |
| Identitaet | gepaarte stabile Benutzer-ID | Displayname, Gast, anderer Benutzer/Raum |
| Routing | ein Direktchat, eine Sitzung | Cross-User-, Cross-Kanal- oder Bot-Loop |
| Approval | eindeutige aktuelle Stufe 2 | null/mehrere/stale/replayed/geaendert |
| Kritischer Write | gebundenes Allow-once | blosses JA, Reaktion oder Zitat |
| Zustellung | dauerhaft angenommen, einmal verarbeitet | Timeout, Doppelzustellung, Restart |
| Netzwerk | WireGuard, TLS, privater Rueckkanal | VPN aus, falsches Zertifikat, SSRF |
| Mobile | Vordergrund, Hintergrund, Netzwechsel | Offlinezustand als Erfolg gemeldet |
| Datenschutz | redigierte Logs und Status | Secret oder Chatinhalt in Diagnose |
| Runtime | Baseline innerhalb Limits | Limitanhebung, OOM, unerklaerter RSS-Anstieg |
| Rollback | Kanal aus, vorheriges Image gesund | Bot bleibt schreibfaehig oder Port offen |

## Gesamtdefinition „M17 abgeschlossen“

M17 ist erst abgeschlossen, wenn:

- der offizielle Talk-Connector kompatibel, exakt gepinnt, gescannt und im
  unveraenderlichen Runtime-Image enthalten ist;
- nur der exakte signierte Webhookpfad privat erreichbar ist und das Telefon
  keinen Gatewayzugriff besitzt;
- Botsecret, optionales App-Passwort und Benutzeridentitaet getrennt und
  fail-closed behandelt werden;
- Jans Direktchat stabil derselben vorgesehenen Agentensitzung zugeordnet wird;
- der risikobasierte Approval-Vertrag kanalgleich gilt und durch Talk nicht
  abgeschwaecht wird;
- reale mobile Chat-, Hintergrund-, Netzwechsel- und Benachrichtigungstests auf
  dem primaeren Telefon dokumentiert sind;
- Restart, Replay, Doppelzustellung und Zustellungsunsicherheit keinen doppelten
  externen Write ausloesen;
- Tests, Artefakte, Git-, Release- und Imageidentitaet fuer denselben Commit
  verifiziert sind;
- produktive Aktivierung separat freigegeben, beobachtet und entweder eindeutig
  abgenommen oder vollstaendig zurueckgerollt wurde.

## Naechster erlaubter Schritt

Nur M17.0 ist ohne weitere Designentscheidung zulaessig. Es ist read-only und
veraendert weder Nextcloud noch Gateway, Secrets, Netzwerk oder produktive
Container. M17.1 darf erst auf Basis dieser gemessenen Topologie entscheiden.

## Unabhaengiger Abschluss-Audit-Prompt

```text
Pruefe M17 aus docs/MOBILE_NEXTCLOUD_TALK_ROADMAP.md unabhaengig und kritisch.
Lies AGENTS.md, runtime-security.md, den Plugin-/Image-Lieferkettenvertrag, die
Gateway-, Approval-, Release- und Rollback-ADRs sowie alle M17-Nachweise
vollstaendig. Verifiziere, dass die native Nextcloud-Talk-App ausschliesslich
Nextcloud ueber WireGuard erreicht und dass nur der exakte signierte
Webhookpfad privat an das Gateway weitergeleitet wird. Pruefe Pluginpinning,
SBOM, Provenance, Signaturen, Secrettrennung, HMAC, Replay/Dedupe,
Benutzeridentitaet, Sitzungsrouting, risikobasierte Freigaben,
Zustellungsunsicherheit, mobile Hintergrundzustellung, Ressourcenwerte und
Rollback. Ein blosses JA darf niemals Risikostufe 3 freigeben. Veraendere keine
produktiven Daten oder Rechte waehrend des Audits. Berichte Findings nach
Schweregrad, fehlende Evidenz, Tests, Messwerte, Artefakte, Restrisiken und das
eindeutige Urteil M17 PRODUKTIV ABGENOMMEN oder M17 NICHT ABGENOMMEN.
```

## Upstream-Referenzen

- OpenClaw Nextcloud Talk:
  <https://docs.openclaw.ai/channels/nextcloud-talk>
- OpenClaw Pairing:
  <https://docs.openclaw.ai/start/pairing>
- Nextcloud Talk Android:
  <https://github.com/nextcloud/talk-android>
- Nextcloud Talk iOS:
  <https://github.com/nextcloud/talk-ios>
