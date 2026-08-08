from __future__ import annotations

import getpass
import os
import shutil
from email.utils import parseaddr
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import Config, load_config
from .envfile import default_env_file, update_env_file
from .nextcloud import NextcloudSkillClient
from .setup_assistant import update_toml_values


def _yes_no(label: str, default: bool = True) -> bool:
    suffix = "[J/n]" if default else "[j/N]"
    answer = input(f"{label} {suffix}: ").strip().casefold()
    if not answer:
        return default
    return answer in {"j", "ja", "y", "yes"}


def _prompt(label: str, current: str = "") -> str:
    suffix = f" [{current}]" if current else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or current


def _resource_name(item: dict[str, Any]) -> str:
    for key in ("displayName", "name", "calendar", "addressBook", "title", "href", "url"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return "unbenannt"


def _choose_resource(label: str, items: list[dict[str, Any]], current: str = "") -> str:
    if not items:
        raise RuntimeError(f"Nextcloud lieferte keine {label}")
    names = [_resource_name(item) for item in items]
    print(f"\nVerfuegbare {label}:")
    for index, name in enumerate(names, 1):
        marker = " (aktuell)" if current and name.casefold() == current.casefold() else ""
        print(f"  {index}. {name}{marker}")
    default_index = 1
    if current:
        for index, name in enumerate(names, 1):
            if name.casefold() == current.casefold():
                default_index = index
                break
    selection = input(f"{label} waehlen [Nummer, Standard {default_index}]: ").strip()
    if not selection:
        return names[default_index - 1]
    if selection.isdigit() and 1 <= int(selection) <= len(names):
        return names[int(selection) - 1]
    for name in names:
        if name.casefold() == selection.casefold():
            return name
    raise ValueError(f"Ungueltige Auswahl fuer {label}: {selection!r}")




def _validate_remote_folder(value: str) -> str:
    cleaned = (value or "").strip().replace("\\", "/").strip("/")
    parts = [part.strip() for part in cleaned.split("/") if part.strip()]
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError("Der Nextcloud-Rechnungsordner ist ungueltig")
    return "/".join(parts)


def _validate_mail_address(value: str, label: str) -> str:
    address = parseaddr((value or "").strip())[1].strip()
    if "@" not in address or "\r" in address or "\n" in address:
        raise ValueError(f"{label} ist keine gueltige E-Mail-Adresse")
    return address

def _validate_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("NEXTCLOUD_URL muss eine vollstaendige HTTP(S)-URL sein")
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and (parsed.hostname or "").casefold() not in local_hosts:
        raise ValueError(
            "Der Nextcloud-Skill akzeptiert aus Sicherheitsgruenden ausserhalb von localhost nur HTTPS."
        )
    return value


def interactive_nextcloud_setup(
    config: Config,
    client: NextcloudSkillClient,
    *,
    env_path: Path | None = None,
) -> tuple[Config, list[Path]]:
    """Configure the release-owned native Nextcloud bridge for the mail agent."""

    print("NEXTCLOUD CALDAV/CARDDAV SETUP")
    print("==============================")
    print(
        "Der Mail-Agent nutzt die native, eingeschraenkte Release-Bruecke fuer:\n"
        "  - Kalender und Adressbuecher auflisten\n"
        "  - Kontakte lesen (CardDAV)\n"
        "  - nach ausdruecklicher JA-Freigabe neue Termine anlegen (CalDAV)\n"
        "Rechnungs-PDFs werden getrennt ueber eine eng begrenzte WebDAV-Bruecke hochgeladen. "
        "Diese Bruecke kann nur Ordner anlegen und neue PDF-Dateien ohne Ueberschreiben speichern; "
        "sie kann keine Dateien loeschen, verschieben oder freigeben.\n\n"
        "WICHTIG: Das Nextcloud-App-Passwort selbst ist kontoweit. Verwende deshalb ein separates, "
        "jederzeit widerrufbares App-Passwort nur fuer diesen Agenten.\n"
    )

    if not client.available or not client.script_path.is_file():
        raise RuntimeError("Native Nextcloud-Bruecke fehlt im verifizierten Release")
    print(f"Connector: native Release-Bruecke unter {client.script_path}")

    env_file = (env_path or default_env_file()).expanduser().resolve()
    current_url, current_user, current_token = client.credentials()
    base_url = _validate_base_url(_prompt("Nextcloud-Basis-URL", current_url))
    username = _prompt("Nextcloud-Benutzername", current_user)
    if not username:
        raise ValueError("Nextcloud-Benutzername darf nicht leer sein")
    token_prompt = "Nextcloud-App-Passwort"
    if current_token:
        token_prompt += " [Enter behaelt den vorhandenen Wert]"
    token = getpass.getpass(token_prompt + ": ").strip() or current_token
    if not token:
        raise ValueError("Ein Nextcloud-App-Passwort ist erforderlich")

    env_values = {
        config.nextcloud.base_url_env: base_url,
        config.nextcloud.username_env: username,
        config.nextcloud.token_env: token,
    }
    previous_environment = {name: os.environ.get(name) for name in env_values}
    previous_enabled = config.nextcloud.enabled
    for name, value in env_values.items():
        os.environ[name] = value

    # Validate credentials and resource selection before persisting either secrets
    # or config.toml. A failed login therefore leaves the previous setup intact.
    config.nextcloud.enabled = True
    try:
        calendars = client.list_calendars()
        calendar = _choose_resource("Kalender", calendars, config.nextcloud.calendar)

        contacts_enabled = _yes_no("Nextcloud-Kontakte als zusaetzliches Legitimitaetssignal nutzen", default=True)
        addressbook = ""
        if contacts_enabled:
            addressbooks = client.list_addressbooks()
            addressbook = _choose_resource("Adressbuch", addressbooks, config.nextcloud.addressbook)

        contacts_prevent_spam = False
        trust_contacts = False
        if contacts_enabled:
            contacts_prevent_spam = _yes_no(
                "Bekannte Kontakte vor einer reinen Modell-Spamentscheidung schuetzen",
                default=True,
            )
            trust_contacts = _yes_no(
                "Alle CardDAV-Kontakte automatisch fuer Kalendererstellung vertrauen",
                default=False,
            )
        approval_recipient = _validate_mail_address(
            _prompt(
                "Adresse fuer Terminfreigabe-Mails",
                config.calendar.approval_recipient or config.mailbox.forward_to,
            ),
            "Adresse fuer Terminfreigaben",
        )
        approval_reply_from = _validate_mail_address(
            _prompt(
                "Absenderadresse, von der JA/NEIN-Antworten akzeptiert werden",
                config.calendar.approval_reply_from or config.mailbox.forward_to,
            ),
            "Antwort-Absenderadresse",
        )
        auto_create = _yes_no(
            "Nach einer gueltigen JA-Antwort einen zukuenftigen Termin automatisch in Nextcloud anlegen",
            default=True,
        )
        invoice_enabled = _yes_no(
            "Rechnungs-PDFs aus sicher erkannten Routine-Mails in Nextcloud archivieren",
            default=True,
        )
        invoice_folder = config.invoices.nextcloud_folder
        if invoice_enabled:
            invoice_folder = _validate_remote_folder(
                _prompt("Nextcloud-Ordner fuer Rechnungen", invoice_folder)
            )
        if trust_contacts:
            print(
                "Hinweis: Diese Option erweitert die Kalenderfreigabe stark. Harte [important]-Regeln und "
                "zweimaliges Korrekturfeedback sind die sicherere Voreinstellung."
            )
    except Exception:
        config.nextcloud.enabled = previous_enabled
        for name, previous in previous_environment.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
        raise

    env_existed = env_file.exists()
    env_backup = update_env_file(env_file, env_values)
    try:
        config_backup = update_toml_values(
            config.path,
            {
                ("nextcloud", "enabled"): True,
                ("nextcloud", "calendar"): calendar,
                ("nextcloud", "addressbook"): addressbook,
                ("nextcloud", "contacts_enabled"): contacts_enabled,
                ("nextcloud", "contacts_prevent_spam"): contacts_prevent_spam,
                ("nextcloud", "trust_contacts_for_calendar"): trust_contacts,
                ("calendar", "enabled"): True,
                ("calendar", "backend"): "nextcloud_skill",
                ("calendar", "auto_create"): auto_create,
                ("calendar", "require_trusted_sender"): True,
                ("calendar", "approval_required"): True,
                ("calendar", "approval_recipient"): approval_recipient,
                ("calendar", "approval_reply_from"): approval_reply_from,
                ("calendar", "require_future"): True,
                ("invoices", "enabled"): invoice_enabled,
                ("invoices", "nextcloud_folder"): invoice_folder,
                ("invoices", "require_routine"): True,
            },
        )
        try:
            updated = load_config(config.path)
        except Exception:
            shutil.copy2(config_backup, config.path)
            raise
    except Exception:
        if env_backup:
            shutil.copy2(env_backup, env_file)
        elif not env_existed:
            env_file.unlink(missing_ok=True)
        config.nextcloud.enabled = previous_enabled
        for name, previous in previous_environment.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
        raise

    print(f"Zugangsdaten gespeichert: {env_file} (Dateirechte 0600)")
    backups = [config_backup]
    if env_backup:
        backups.append(env_backup)
    print(f"Konfiguration gespeichert: {updated.path}")
    print("Wichtig: Die Konfigurationsaenderung macht den alten Dry-Run ungueltig.")
    print("Naechste Befehle:")
    print("  ./scripts/mail-agent.sh nextcloud doctor")
    print("  ./scripts/mail-agent.sh nextcloud sync-contacts")
    print("  ./scripts/mail-agent.sh doctor")
    print("  ./scripts/mail-agent.sh run --dry-run --no-digest --limit 20")
    return updated, backups
