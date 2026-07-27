# Hotfix 3.4.0-r2

Dieser Hotfix haertet den E-Mail-Parser gegen formal defekte Legacy-Header.

Behoben:

- `IndexError: list index out of range` bei kaputten From/To/Cc/Reply-To/Message-ID-Headern
- einzelne defekte MIME- oder Anhangsheader brechen nicht mehr die gesamte Mailverarbeitung ab
- bestehende Mailtexte, Absender-Fallbacks und stabile Message-Keys bleiben erhalten

Nicht geaendert:

- Mailregeln und Feedback
- Datenbanken und Suchindex
- Weiterleitung, Drain-Modus und systemd-Units
- Nextcloud-Konfiguration und Secrets

Nach Installation ist wegen der Quellcodeaenderung ein neuer Mail-Dry-Run erforderlich.
