# M16.7: Messbare Mail-Lern-, Review- und Antwortqualitaet

Stand: 2026-09-21. M16.7 ist eine synthetische Entwicklungsabnahme. Es wurden
keine produktiven Mails gelesen oder verschoben, keine Regel aktiviert und kein
Job gestartet.

## Ergebnis

Neue Klassifikationen erzeugen beim ersten Speichern einen unveraenderlichen,
inhaltsarmen Entscheidungssnapshot. Er enthaelt nur typisierte Featurezaehler,
getrennte Regel-/Modell-/Kombinationsentscheidungen, Quellenart sowie gehashte
Sender- und Threadgruppen. Adresse, Message-ID, Betreff, Body, Begruendung und
Zusammenfassung werden nicht in der neuen Tabelle dupliziert. SQLite-Trigger
verhindern Update und Delete. Eine spaetere Einzelkorrektur bindet ihre
Feedbackzeile an genau diese Snapshot-ID.

Bestehende Zeilen werden nicht rueckwirkend aus dem heutigen Zustand
rekonstruiert. Ohne M16.7-Snapshot sind sie `legacy_rows_without_original_decision`
und bleiben ausserhalb der belastbaren Regel-/Modell-/Kombinationsmetrik. Das
alte M9-Feld bleibt nur fuer Kompatibilitaet erhalten.

`mail learning evaluate` berichtet getrennt:

- Sender-, Betreffmuster-, Regel-, Modell- und Kombinationsentscheidung,
- Accuracy und Coverage,
- False Positive, False Negative und Abstention insgesamt sowie je Kategorie,
- `relevant_missed` und `spam_forward_risk`,
- Singleton-, Mischsender- und Konfliktzahlen,
- einen chronologischen 70/30-Holdout, aus dem uebergreifende Sender- und
  Threadgruppen vollstaendig entfernt werden,
- eine inhaltsfreie Wirkungsqueue fuer `relevant-not-forwarded`,
  `spam-forward-risk` und Patternkonflikte.

Die Queue aktiviert keine Regel. `mail review list` liefert zusaetzlich eine
typisierte Wirkungsprioritaet; Einzelkorrektur, Freigabe und Mailbewegung bleiben
unveraendert getrennt.

## Belegschwellen

Die Laufzeitpolicy bleibt unveraendert konservativ: Relevant darf ein aelterer
konsistenter Beleg schuetzen; Routine und Spam benoetigen mindestens zwei
aeltere konsistente Belege. Ein Konflikt verhindert Aktivierung. Die Evaluation
weist dies maschinenlesbar als `minimum_evidence`, `conflict_free_required` und
`automatic_activation=false` aus. M16.7 senkt keine Klassifikationsschwelle.

## Reproduktion

```bash
./.venv/bin/python scripts/benchmark_mail_learning_m167.py
./.venv/bin/python -m pytest -q \
  tests/test_mail_learning_quality_m167.py \
  tests/test_learning_quality.py \
  tests/test_learning_patterns.py \
  tests/test_mail_review_m9.py
```

Der SHA-gebundene Korpus und die kompakten Ausgangswerte stehen in
[`m16.7-mail-learning-baseline.json`](architecture/m16.7-mail-learning-baseline.json).
Er enthaelt zehn rein synthetische, inhaltsfreie Faelle. Die absichtlich
eingebauten Fehler ergeben jeweils einen Fall `relevant-not-forwarded` und
`spam-forward-risk`. Die niedrige Sender-/Pattern-Coverage ist beabsichtigt: der
Korpus belegt Abstention bei fehlender historischer Evidenz und ist keine
Schaetzung der Produktivqualitaet.

## Antwortgrenze

Mailantworten muessen weiterhin `complete`, `folder_errors`,
`results_may_be_truncated`, `decision` und `negative_claim_allowed` auswerten.
Ein unvollstaendiges oder abstainendes Ergebnis erlaubt in Deutsch, Englisch
oder Spanisch keine definitive Negativaussage. Positive Evidenz bleibt davon
unberuehrt.

## Grenzen

- Der synthetische Korpus ist ein Regressionstest, kein Produktivbenchmark.
- Produktive Altentscheidungen ohne M16.7-Snapshot werden bewusst nicht
  nachkonstruiert.
- Feedback bleibt Einzelkorrektur; es gibt weder Auto-Aktivierung noch
  historische Massenverschiebung.
- Eine spaetere Qualitaetsgrenze darf erst aus ausreichend grosser, zeitlich und
  gruppenseparierter Evidenz beschlossen werden.
