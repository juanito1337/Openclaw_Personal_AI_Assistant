# ADR-0001: Modularer Monolith im Multi-Container-Betrieb

- Status: Accepted
- Datum: 2026-08-05
- Entscheider: Architecture Maintainers
- Betroffene Milestones: M1-M8

## Kontext

Gateway, Mail, Sync, Portfolio, Monitoring und Supervisor benoetigen getrennte
Lebenszyklen und Healthchecks, gehoeren aber zu einem Produkt, Release und
Toolvertrag. Unabhaengige Produktimages oder autonome Agenten wuerden Versionierung
und Sicherheitsregeln duplizieren. Schmale Infrastrukturrollen koennen dagegen aus
demselben Commit und Release eigene Runtime-Targets erhalten; ADR-0011 praezisiert
diesen Punkt ab M7.

## Entscheidung

OpenClaw bleibt ein modularer Python-Monolith mit gemeinsamem Release- und
Toolvertrag. Compose startet rollenbezogene Prozesse mit getrennten Kommandos und
Healthchecks. Alle Rollen sind Teile genau eines Personal Assistant. Bis M6 nutzten
sie ein gemeinsames Image; ab M7 duerfen nach ADR-0011 gemessene, minimale Targets
aus demselben Quellstand verwendet werden.

## Konsequenzen

Prozessfehler sind isolierbar und Releases bleiben konsistent. Codekopplung ist
dadurch nicht automatisch geloest; Modulgrenzen und Importregeln muessen separat
gehaertet werden. Neue Container sind keine Erlaubnis fuer neue autonome Autoritaet.

## Verifikation

Compose-Rendering, Rollenmatrix, gemeinsame Releaseidentitaet und Container-Runtime-Tests.

## Offene Fragen

Welche internen Ports werden in M5 zu expliziten Schnittstellen statt direkten
Python-Imports?
