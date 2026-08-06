from __future__ import annotations

import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Classification, ParsedMessage
from .storage import Storage
from .utils import normalize_address


@dataclass(slots=True)
class RuleContext:
    forced: Classification | None = None
    prevent_spam: bool = False
    notes: list[str] | None = None
    important_sender: bool = False
    known_contact: bool = False
    importance_boost: int = 0


class RuleEngine:
    def __init__(
        self,
        path: Path,
        storage: Storage,
        *,
        contact_lookup: Callable[[str], bool] | None = None,
        contacts_prevent_spam: bool = False,
        trust_contacts_for_calendar: bool = False,
        contact_importance_boost: int = 0,
    ) -> None:
        self.path = path
        self.storage = storage
        self.data = self._load(path)
        self.contact_lookup = contact_lookup
        self.contacts_prevent_spam = contacts_prevent_spam
        self.trust_contacts_for_calendar = trust_contacts_for_calendar
        self.contact_importance_boost = max(0, min(3, int(contact_importance_boost)))

    @staticmethod
    def _load(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        with path.open("rb") as handle:
            return tomllib.load(handle)

    def _values(self, section: str, key: str) -> list[str]:
        value = self.data.get(section, {}).get(key, []) if isinstance(self.data.get(section, {}), dict) else []
        if not isinstance(value, list):
            return []
        return [str(item).strip().lower() for item in value if str(item).strip()]

    @staticmethod
    def _contains_any(value: str, needles: list[str]) -> bool:
        lowered = value.lower()
        return any(needle in lowered for needle in needles)

    @staticmethod
    def _domain_matches(domain: str, configured_domains: list[str]) -> bool:
        """Match a domain rule against the domain itself and its subdomains."""
        normalized = domain.strip().lower().rstrip(".")
        return any(
            normalized == item.strip().lower().rstrip(".")
            or normalized.endswith("." + item.strip().lower().rstrip("."))
            for item in configured_domains
            if item.strip()
        )

    @staticmethod
    def _is_verified_bounce(message: ParsedMessage, sender_local: str, subject: str, body: str) -> bool:
        """Recognize a bounce only from strong delivery-status evidence.

        A sender local-part such as ``postmaster`` or ``mailer-daemon`` is merely a
        hint. Marketing systems sometimes use those names and include generic footer
        text such as "nicht zugestellt werden". Therefore sender plus body text alone
        must never trigger forwarding.
        """
        sender_signal = sender_local in {"mailer-daemon", "postmaster"}
        subject_markers = (
            "delivery status notification", "zustellung fehlgeschlagen", "unzustellbar",
            "undelivered mail", "mail delivery failed", "returned mail",
            "failure notice", "delivery failure", "nachricht konnte nicht zugestellt",
            "delivery unsuccessful", "mail system error - returned mail",
        )
        structured_body_markers = (
            "final-recipient:", "original-recipient:", "diagnostic-code:",
            "action: failed", "status: 5.", "reporting-mta:", "remote-mta:",
        )
        strong_body_markers = (
            "recipient address rejected", "mailbox unavailable",
            "delivery has failed", "could not be delivered",
            "zustellung an folgende empfaenger ist fehlgeschlagen",
            "zustellung an folgende empfänger ist fehlgeschlagen",
        )
        subject_signal = any(marker in subject for marker in subject_markers)
        structured_hits = sum(1 for marker in structured_body_markers if marker in body)
        strong_body_signal = any(marker in body for marker in strong_body_markers)
        raw_lower = message.raw[:250_000].lower()
        mime_signal = (
            b"message/delivery-status" in raw_lower
            or (b"multipart/report" in raw_lower and b"report-type=delivery-status" in raw_lower)
        )

        # Standards-compliant DSN MIME is sufficient on its own. Otherwise require
        # a bounce-like subject plus an independent sender or body signal. Never use
        # sender + generic body wording as proof.
        return bool(
            mime_signal
            or (subject_signal and (sender_signal or structured_hits >= 1 or strong_body_signal))
        )

    def _explicitly_important(self, message: ParsedMessage) -> bool:
        sender = normalize_address(message.sender_addr)
        return sender in self._values("important", "addresses") or self._domain_matches(
            message.sender_domain, self._values("important", "domains")
        )

    def is_trusted_sender(self, message: ParsedMessage, feedback_count: int = 2) -> bool:
        """Trust calendar automation only after an explicit user signal."""
        # Sender-wide feedback is deliberately not sufficient: one sender may
        # legitimately send invoices, newsletters and security warnings.
        return (
            self._explicitly_important(message)
            or self.storage.exact_feedback_verdict(message) == "relevant"
            or (self.trust_contacts_for_calendar and self._is_known_contact(message.sender_addr))
        )

    def _is_known_contact(self, sender_addr: str) -> bool:
        if not self.contact_lookup:
            return False
        try:
            return bool(self.contact_lookup(sender_addr))
        except Exception:
            # Contact lookup is a supporting signal. A CardDAV outage must never
            # stop mail triage or turn a message into spam.
            return False

    @staticmethod
    def _important_context(reason: str, *, appointment: bool = False) -> RuleContext:
        if appointment:
            return RuleContext(
                Classification(
                    "appointment", 0.99, 9, True, reason,
                    expected_action="Termin und Originalmail pruefen.",
                    source="trusted-rule",
                ),
                prevent_spam=True,
                important_sender=True,
            )
        return RuleContext(
            None,
            prevent_spam=True,
            notes=[reason + "; bei Zweifel als relevant einstufen und Terminangaben extrahieren."],
            important_sender=True,
        )

    def evaluate(self, message: ParsedMessage) -> RuleContext:
        pattern = self.storage.pattern_feedback_decision(message)
        exact_feedback = pattern.get("verdict")
        pattern_count = int(pattern.get("count") or 0)
        if exact_feedback == "spam" and pattern_count >= 2:
            return RuleContext(Classification(
                "spam", 1.0, 1, False,
                f"Mindestens zwei konsistente Nutzerkorrekturen fuer dasselbe Absender-/Betreffmuster ({pattern_count} Beispiele)",
                source="feedback-pattern",
            ))
        if exact_feedback == "routine" and pattern_count >= 2:
            return RuleContext(Classification(
                "routine", 1.0, 3, False,
                f"Mindestens zwei konsistente Nutzerkorrekturen fuer dasselbe Absender-/Betreffmuster ({pattern_count} Beispiele)",
                source="feedback-pattern",
            ), prevent_spam=True)
        if exact_feedback == "relevant":
            if message.calendar_invites:
                return self._important_context(
                    "Nutzerkorrektur fuer dasselbe Absender-/Betreffmuster: wichtige Termineinladung",
                    appointment=True,
                )
            return self._important_context(
                "Nutzerkorrektur fuer dasselbe Absender-/Betreffmuster: wichtig"
            )

        prevent_spam = bool(pattern.get("prevent_spam")) or exact_feedback == "not_spam"
        notes: list[str] = []
        not_spam = pattern.get("not_spam") or {}
        if prevent_spam:
            origin = str(not_spam.get("origin") or "Nutzerkorrektur")
            notes.append(
                "Nicht-Spam-Gegenbeleg fuer dasselbe Absender-/Betreffmuster "
                f"({origin}); Spamregeln und Spam-Modellergebnisse werden blockiert, "
                "Routine- oder Wichtig-Entscheidungen bleiben davon unberuehrt."
            )
        if exact_feedback in {"spam", "routine"} and pattern_count == 1:
            notes.append(
                "Erst eine Nutzerkorrektur fuer dieses Absender-/Betreffmuster vorhanden; "
                "Routine oder Spam wird aus Sicherheitsgruenden noch nicht automatisch erzwungen."
            )
        profile = self.storage.sender_feedback_profile(message.sender_addr)
        if pattern.get("conflict"):
            notes.append(
                "Fuer dieses Absender-/Betreffmuster existieren widerspruechliche Nutzerkorrekturen; "
                "keine automatische Feedbackentscheidung treffen."
            )
        if profile.get("mixed"):
            categories = ", ".join(sorted(profile.get("category_counts", {})))
            notes.append(
                "Gemischter Absender: fruehere Korrekturen enthalten unterschiedliche Mailtypen "
                f"({categories}). Absender allein darf die Kategorie nicht bestimmen."
            )

        sender = normalize_address(message.sender_addr)
        domain = message.sender_domain
        sender_name = message.sender_name.lower()
        subject = message.subject.lower()
        body = message.body_text.lower()

        if self._explicitly_important(message):
            if message.calendar_invites:
                return self._important_context("Absender steht auf der Wichtig-Liste und sendet eine Termineinladung", appointment=True)
            return self._important_context("Absender steht auf der Wichtig-Liste")

        spam_addresses = self._values("spam", "addresses")
        spam_domains = self._values("spam", "domains")
        spam_names = self._values("spam", "sender_names")
        spam_subjects = self._values("spam", "subject_phrases")
        if not prevent_spam and (
            sender in spam_addresses
            or self._domain_matches(domain, spam_domains)
            or self._contains_any(sender_name, spam_names)
            or self._contains_any(subject, spam_subjects)
        ):
            return RuleContext(Classification("spam", 0.99, 1, False, "Treffer in der expliziten Spam-Liste", source="rule"))

        routine_addresses = self._values("routine", "addresses")
        routine_domains = self._values("routine", "domains")
        if sender in routine_addresses or self._domain_matches(domain, routine_domains):
            return RuleContext(
                Classification("routine", 0.98, 3, False, "Absender steht auf der Routine-Liste", source="rule"),
                prevent_spam=True,
            )

        feedback_prevent_spam = prevent_spam
        known_contact = self._is_known_contact(message.sender_addr)
        if known_contact:
            if self.contacts_prevent_spam:
                prevent_spam = True
            notes.append(
                "Der Absender steht im Nextcloud-CardDAV-Adressbuch. Das ist ein positives Legitimitaetssignal, "
                "macht die Mail aber nicht automatisch wichtig."
            )

        combined = f"{sender_name} {sender} {subject} {body[:8000]}"
        sender_local = sender.split("@", 1)[0] if "@" in sender else sender
        if self._is_verified_bounce(message, sender_local, subject, body):
            return RuleContext(
                Classification(
                    "relevant", 0.99, 9, True,
                    "Zustellfehler oder Bounce; das ist ein Handlungsproblem und kein Spam",
                    summary="Eine versendete Nachricht konnte moeglicherweise nicht zugestellt werden.",
                    expected_action="Urspruenglichen Empfaenger und Versandfehler pruefen.",
                    source="rule",
                ),
                prevent_spam=True,
            )

        urgent_payment = (
            "mahnung", "ueberfaellig", "überfällig", "zahlung fehlgeschlagen", "lastschrift fehlgeschlagen",
            "konto gesperrt", "zahlungsfrist", "inkasso", "payment failed",
        )
        if any(marker in combined for marker in urgent_payment):
            notes.append(
                "Moegliches Zahlungs-, Frist- oder Inkassoproblem erkannt. Authentizitaet pruefen; bei echtem Handlungsbedarf hohe Wichtigkeit vergeben."
            )

        if message.calendar_invites:
            notes.append(
                "Die Mail enthaelt eine Kalenderdatei. Legitimitaet und bestaetigten Termin pruefen; unbekannte Absender duerfen keinen automatischen Kalendereintrag erzeugen."
            )

        promotional = (
            "newsletter", "rabatt", "sonderangebot", "jetzt sichern", "sale", "deal",
            "unsubscribe", "abbestellen", "gutschein", "nur heute", "aktion endet",
        )
        marketing_sender = any(marker in sender for marker in ("newsletter", "marketing", "news@", "offers@"))
        promo_hits = sum(1 for marker in promotional if marker in combined)
        if not prevent_spam and ((marketing_sender and promo_hits >= 1) or promo_hits >= 3):
            return RuleContext(
                Classification("spam", 0.97, 1, False, "Mehrere eindeutige Newsletter-/Werbesignale", source="rule")
            )

        routine_markers = (
            "versandbestaetigung", "versandbestätigung", "sendung ist unterwegs", "paket wurde zugestellt",
            "bestellbestaetigung", "bestellbestätigung", "rechnung nr", "invoice is now available",
            "beleg fuer ihre zahlung", "beleg für ihre zahlung", "retoure eingegangen", "erstattung wurde veranlasst",
        )
        is_reply = subject.lstrip().startswith(("re:", "aw:", "wg:", "fw:", "fwd:"))
        if (
            any(marker in combined for marker in routine_markers)
            and not any(marker in combined for marker in urgent_payment)
            and not message.calendar_invites
            and not is_reply
        ):
            return RuleContext(
                Classification("routine", 0.93, 3, False, "Automatische Bestell-, Versand-, Beleg- oder Rechnungsinformation", source="rule"),
                prevent_spam=True,
            )

        if feedback_prevent_spam and not any("Nicht-Spam-Gegenbeleg" in note for note in notes):
            notes.append("Der Nutzer hat dasselbe Absender-/Betreffmuster als Nicht-Spam korrigiert.")
        return RuleContext(
            None,
            prevent_spam,
            notes,
            False,
            known_contact,
            self.contact_importance_boost if known_contact else 0,
        )
