# ADR-0046: Unveraenderliche Mailentscheidungsevidenz

Status: Accepted

## Kontext

M9 bewahrte bei einer Korrektur die zuletzt gespeicherte Endentscheidung. Regel,
Modell und Kombination waren jedoch nicht getrennt und der Snapshot entstand
erst beim spaeteren Feedback. Dadurch konnten mutable Zwischenzustaende und
Legacyzeilen die Qualitaetsaussage verzerren.

## Entscheidung

Der Mail-Datenowner speichert fuer jede neue Klassifikation genau einen
append-only `decision_snapshots`-Datensatz. Er trennt Feature-, Regel-, Modell-
und Kombinationssnapshot, Quellenart sowie gehashte Sender-/Threadgruppe. Rohe
Adresse, Message-ID, Betreff, Body und Begruendung werden nicht dupliziert.
Feedback referenziert die interne Snapshot-ID. Fehlende Legacy-Snapshots werden
nicht rekonstruiert und abstainieren in der belastbaren Komponentenmetrik.

Train-/Eval-Auswertungen sind zeitlich geordnet und entfernen Gruppen, deren
Sender oder Thread die Grenze kreuzt. Qualitaet wird pro Komponente nach
Accuracy, Coverage, False Positive, False Negative und Abstention berichtet.
Sicherheitsfehler werden inhaltsfrei priorisiert, ohne Regeln zu aktivieren oder
Mails zu bewegen.

## Konsequenzen

- Neue Entscheidungen sind spaeter gegen Nutzerfeedback auswertbar.
- Legacydaten bleiben sichtbar, aber nicht gleichwertige Ground Truth.
- Der private Mailzustand waechst um kleine, inhaltsarme Datensaetze.
- Explizites Feedback-Forget loescht weiter nur die Feedbackzeile; der
  historische Entscheidungssnapshot bleibt Audit-/Qualitaetsevidenz.
- Produktive Regeln, Schwellen und Jobs bleiben durch M16.7 unveraendert.
