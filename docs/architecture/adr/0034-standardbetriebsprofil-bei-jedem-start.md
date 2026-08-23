# ADR-0034: Standardbetriebsprofil bei jedem Start

- Status: Accepted
- Datum: 2026-08-23
- Entscheider: Architecture Maintainers, Operations Maintainers
- Betroffene Bereiche: Runtime, Toolkatalog, Nextcloud, Mail

## Kontext

Normale Funktionen fuer eine bereits ausgewaehlte Kalender-, Aufgaben-, Kontakt-,
Mail- oder Workspace-Ressource wurden historisch durch einzelne `allow_*`-
Schalter und spaeter durch `setup standard-operations --yes` aktiviert. Diese
Zustandsmaschine blieb nach Updates zwar persistent, zwang den Benutzer aber zu
einer technischen Zweitfreigabe. Ein gesunder Agent konnte dadurch seine
registrierten Werkzeuge kennen und trotzdem behaupten, fuer normale Aufgaben erst
Konfigurationsdateien oder Mountrechte aendern zu muessen.

Gleichzeitig muessen Ressourcenwahl, Credentials, bestaetigte Serverrechte,
Single-Writer-Grenzen und die Freigabe einer konkreten externen Aenderung getrennt
bleiben. Ein allgemeiner Vollzugriff oder ein automatisches Erweitern von
Nextcloud-ACLs waere keine zulaessige Loesung.

## Entscheidung

Die releaseeigene Werkzeugkonfiguration definiert
`operations.profile = "standard"`. Der Loader wendet dieses Profil nach dem
Zusammenfuehren mit der persistenten Instanzdatei bei jedem Prozessstart an. Fuer
eine bereits aktivierte und eindeutig ausgewaehlte Ressource werden die normalen
nicht-destruktiven Werkzeugschalter effektiv eingeschaltet:

- Mail einzeln und kontrolliert verschieben;
- im begrenzten Workspace create-only schreiben, hochladen, Verzeichnisse anlegen
  und ohne Ueberschreiben verschieben;
- Kalender, Aufgaben und Kontakte lesen, create-only anlegen sowie genau ein
  bestehendes Objekt geschuetzt aktualisieren;
- den bereits aktivierten agentenverwalteten Deck-Workflow nutzen.

Das Profil veraendert die Instanzdatei beim Laden nicht. Es aktiviert keine
deaktivierte Domaene, waehlt keine Ressource, erzeugt keine Zugangsdaten und
registriert kein fehlendes Recht. Registry- und Live-Status bleiben fuer diese
Voraussetzungen autoritativ und schlagen bei einer Abweichung fail-closed fehl.

`operations.profile = "restricted"` ist der explizite Operator-Ausweichmodus; in
ihm gelten die einzelnen Instanzschalter unveraendert. Andere Werte werden
abgelehnt. Der vorhandene `setup standard-operations --yes`-Befehl bleibt als
`agent-cli`-gebundener Kompatibilitaets- und Reparaturpfad erhalten. Nur er darf
nach aktueller read-only DAV-Bestaetigung ein normales Recht in der lokalen
Registry nachtragen.

## Sicherheitsgrenze

Technische Werkzeugverfuegbarkeit ist keine fachliche Handlungsvollmacht.
Loeschen, Ueberschreiben, Teilen, Massenaktionen, ressourcenuebergreifende Moves,
Credential-/ACL-/Jobaenderungen und unbestaetigter Mailversand bleiben gesperrt
oder separat freigabepflichtig. Updates bestehender Objekte benoetigen weiterhin
den eindeutigen Nutzerauftrag, stabile UID/ID, Erwartungspruefung, aktuellen ETag,
Policy, ActionPlan und Audit.

ADR-0015 bleibt fuer read-only Gateway-Mounts und administrative Setupgrenzen
gueltig. Diese Entscheidung ersetzt nur die Annahme, dass ein normaler
Funktionsschalter zwingend als persistente Konfigurationsmutation aktiviert werden
muss. Die effektive Standardprojektion benoetigt im Gateway keinen Schreibmount.

## Konsequenzen

Nach Installation, Update und Neustart sieht und nutzt der Agent alle normalen
Werkzeuge seiner bereits konfigurierten Ressourcen ohne erneuten Setupdialog.
Alte `false`-Schalter verhindern den Standardbetrieb nicht mehr, solange die
Instanz nicht explizit `restricted` waehlt. Fehlende Ressourcen, Secrets oder
Rechte werden weiterhin ehrlich als Fehler gemeldet.

## Verifikation

Regressionstests laden eine unveraenderte Legacy-Konfiguration mehrfach, pruefen
die effektive vollstaendige Toolprojektion und einen Aufgabenstatus ohne
Setupaufforderung. Weitere Negativtests belegen, dass deaktivierte Domaenen
deaktiviert bleiben, `restricted` alte Schalter respektiert und unbekannte Profile
fail-closed abbrechen. Der bestehende Profiltest prueft weiterhin atomare
Registry-Reparatur nur mit expliziter Freigabe und aktueller DAV-Evidenz.
