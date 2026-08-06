from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]


class CliIntegrationTests(unittest.TestCase):
    def test_end_to_end_with_fake_himalaya(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            temp = Path(temp_name)

            class OllamaHealthHandler(BaseHTTPRequestHandler):
                def do_GET(inner_self) -> None:  # noqa: N802 - HTTP handler API
                    if inner_self.path != "/api/tags":
                        inner_self.send_error(404)
                        return
                    payload = b'{"models":[{"name":"unused"}]}'
                    inner_self.send_response(200)
                    inner_self.send_header("Content-Type", "application/json")
                    inner_self.send_header("Content-Length", str(len(payload)))
                    inner_self.end_headers()
                    inner_self.wfile.write(payload)

                def log_message(inner_self, _format: str, *args) -> None:
                    return

            ollama_server = ThreadingHTTPServer(("127.0.0.1", 0), OllamaHealthHandler)
            ollama_thread = threading.Thread(target=ollama_server.serve_forever, daemon=True)
            ollama_thread.start()
            # Cleanups run in LIFO order: shut down the serving thread first,
            # then close the listening socket.
            self.addCleanup(ollama_server.server_close)
            self.addCleanup(ollama_server.shutdown)
            ollama_url = f"http://127.0.0.1:{ollama_server.server_port}"
            fake = temp / "himalaya"
            fake.write_text(
                textwrap.dedent(
                    r'''#!/bin/sh
set -eu
state="${FAKE_STATE_DIR:?}"

if [ "$1" = "folder" ] && [ "$2" = "list" ]; then
  printf '%s\n' '["INBOX","Spam","Agent/Spam","Agent/Routine","Agent/Weitergeleitet","Agent/Pruefen","Agent/Korrektur-Kein-Spam","Agent/Korrektur-Unwichtig","Agent/Korrektur-Wichtig","Agent/Korrektur-Wichtig/Korrektur-Rechnungen","Agent/Korrektur-Spam","Agent/Termin-Pruefen","Agent/Virusverdacht","Agent/Fehler"]'
  exit 0
fi

if [ "$1" = "envelope" ] && [ "$2" = "list" ]; then
  folder=""
  previous=""
  for argument in "$@"; do
    if [ "$previous" = "--folder" ]; then folder="$argument"; fi
    previous="$argument"
  done
  if [ "$folder" = "INBOX" ]; then
    cat <<'JSON'
[
 {"id":"1","subject":"Newsletter: 30 Prozent Rabatt nur heute","from":{"name":"Shop News","addr":"newsletter@shop.test"}},
 {"id":"2","subject":"Ihre Versandbestätigung zum Auftrag 4711","from":{"name":"Versand","addr":"noreply@shop.test"}},
 {"id":"3","subject":"Mail delivery failed","from":{"name":"Mailer-Daemon","addr":"mailer-daemon@gmx.net"}}
]
JSON
  else
    printf '%s\n' '[]'
  fi
  exit 0
fi

if [ "$1" = "message" ] && [ "$2" = "export" ]; then
  message_id=""
  for argument in "$@"; do
    case "$argument" in
      1|2|3) message_id="$argument" ;;
    esac
  done
  case "$message_id" in
    1) cat <<'EML'
From: Shop News <newsletter@shop.test>
To: Jan <jan@example.test>
Subject: Newsletter: 30 Prozent Rabatt nur heute
Message-ID: <m1@shop.test>
Content-Type: text/plain; charset=utf-8

Newsletter und Sonderangebot. Jetzt sichern, Gutschein nutzen und Newsletter abbestellen.
EML
      ;;
    2) cat <<'EML'
From: Versand <noreply@shop.test>
To: Jan <jan@example.test>
Subject: Ihre Versandbestätigung zum Auftrag 4711
Message-ID: <m2@shop.test>
Content-Type: text/plain; charset=utf-8

Ihre Sendung ist unterwegs. Kein Handlungsbedarf.
EML
      ;;
    3) cat <<'EML'
From: Mailer-Daemon <mailer-daemon@gmx.net>
To: Jan <jan@example.test>
Subject: Mail delivery failed
Message-ID: <m3@gmx.net>
Content-Type: text/plain; charset=utf-8

Delivery Status Notification: recipient address rejected.
EML
      ;;
    *) exit 2 ;;
  esac
  exit 0
fi

if [ "$1" = "message" ] && [ "$2" = "move" ]; then
  printf '%s\n' "$*" >> "$state/moves.log"
  exit 0
fi

if [ "$1" = "template" ] && [ "$2" = "send" ]; then
  cat >> "$state/sent.mml"
  exit 0
fi

if [ "$1" = "folder" ] && [ "$2" = "add" ]; then
  exit 0
fi

printf 'unsupported: %s\n' "$*" >&2
exit 2
'''
                ),
                encoding="utf-8",
            )
            fake.chmod(0o755)
            rules = temp / "rules.toml"
            rules.write_text(
                "[spam]\naddresses=[]\ndomains=[]\nsender_names=[]\nsubject_phrases=[]\n"
                "[important]\naddresses=[]\ndomains=[]\n"
                "[routine]\naddresses=[]\ndomains=[]\n",
                encoding="utf-8",
            )
            config = temp / "config.toml"
            config.write_text(
                textwrap.dedent(
                    f'''[mailbox]
himalaya_binary = "{fake}"
account = ""
source_folder = "INBOX"
from_header = "Jan <jan@example.test>"
forward_to = "jan-second@example.test"
page_size = 100

[folders]
spam = "Agent/Spam"
routine = "Agent/Routine"
forwarded = "Agent/Weitergeleitet"
review = "Agent/Pruefen"
feedback_not_spam = "Agent/Korrektur-Kein-Spam"
feedback_unimportant = "Agent/Korrektur-Unwichtig"
feedback_important = "Agent/Korrektur-Wichtig"
feedback_spam = "Agent/Korrektur-Spam"
appointment_review = "Agent/Termin-Pruefen"
error = "Agent/Fehler"

[ollama]
base_url = "{ollama_url}"
model = "unused"
timeout_seconds = 1
max_body_chars = 16000
temperature = 0.1

[thresholds]
spam = 0.95
relevant = 0.90
routine = 0.90
calendar = 0.95
min_forward_importance = 6

[forwarding]
enabled = true
attach_original_eml = true
retry_as_zip_on_rejection = true
subject_prefix = "[WICHTIG {{importance}}/10]"
payload_dir = "{temp / 'payloads'}"

[calendar]
enabled = true
auto_create = true
backend = "queue"
timezone = "Europe/Berlin"
command = ""
pending_dir = "{temp / 'calendar_pending'}"
created_dir = "{temp / 'calendar_created'}"
caldav_url_env = "MAIL_AGENT_CALDAV_URL"
caldav_username_env = "MAIL_AGENT_CALDAV_USERNAME"
caldav_password_env = "MAIL_AGENT_CALDAV_PASSWORD"

[digest]
enabled = false
hour_local = 18
min_items = 1
subject = "Digest {{date}}"

[notifications]
signal_enabled = false
signal_script = "{temp / 'signal-send.sh'}"
signal_recipient = ""

[runtime]
database = "{temp / 'mail_agent.sqlite3'}"
rules_file = "{rules}"
log_file = "{temp / 'mail_agent.log'}"
lock_file = "{temp / 'mail_agent.lock'}"
command_timeout_seconds = 10
'''
                ),
                encoding="utf-8",
            )
            himalaya_config = temp / "himalaya.toml"
            himalaya_config.write_text(
                '[accounts.agent]\ndefault = true\nemail = "jan@example.test"\n',
                encoding="utf-8",
            )
            tools_config = temp / "tools.toml"
            tools_config.write_text(
                "[nextcloud.workspace]\n"
                "enabled = false\n"
                + 'outbox = "' + str(temp / "test_workspace_outbox") + '"\n\n'
                + "[nextcloud.deck_orders]\n"
                + "enabled = false\n"
                + 'database = "' + str(temp / "test_orders.sqlite3") + '"\n\n'
                + "[security.antivirus]\n"
                + "enabled = false\n"
                + 'temp_dir = "' + str(temp / "test_antivirus_tmp") + '"\n\n'
                + "[portfolio]\n"
                + "enabled = false\n"
                + 'database = "' + str(temp / "portfolio.sqlite3") + '"\n'
                + 'import_root = "' + str(temp / "portfolio_import") + '"\n',
                encoding="utf-8",
            )
            assistant_resources = temp / "resources.toml"
            assistant_resources.write_text(
                "[[resources]]\n"
                'id = "mail-agent"\n'
                'kind = "email-service"\n'
                'connector = "mail-agent"\n'
                "enabled = true\n"
                'remote_id = "primary"\n'
                'permissions = ["read", "classify", "move", "forward"]\n',
                encoding="utf-8",
            )
            assistant_policies = temp / "policies.toml"
            assistant_policies.write_text("", encoding="utf-8")
            assistant_config = temp / "assistant.toml"
            assistant_config.write_text(
                "[runtime]\n"
                + f'database = "{temp / "assistant.sqlite3"}"\n'
                + f'log_file = "{temp / "assistant.log"}"\n'
                + f'resources_file = "{assistant_resources}"\n'
                + f'policies_file = "{assistant_policies}"\n'
                + f'secrets_file = "{temp / "secrets.env"}"\n\n'
                + "[nextcloud]\n"
                + "enabled = false\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["FAKE_STATE_DIR"] = str(temp)
            env["HIMALAYA_CONFIG"] = str(himalaya_config)
            env["OPENCLAW_WORKSPACE"] = str(temp)
            env["OPENCLAW_TOOLS_CONFIG"] = str(tools_config)
            env["PERSONAL_ASSISTANT_CONFIG"] = str(assistant_config)
            base_command = [
                sys.executable,
                "-m",
                "mail_agent",
                "--config",
                str(config),
                "run",
                "--limit",
                "10",
                "--no-digest",
            ]
            dry_run = subprocess.run(
                [*base_command, "--dry-run"],
                cwd=WORKSPACE,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr + dry_run.stdout)

            completed = subprocess.run(
                base_command,
                cwd=WORKSPACE,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            result = json.loads(completed.stdout)
            self.assertEqual(result["processed"], 3)
            statuses = [item["status"] for item in result["actions"]]
            self.assertEqual(statuses, ["spam", "routine", "forwarded"])
            moves = (temp / "moves.log").read_text(encoding="utf-8")
            self.assertIn("Agent/Spam", moves)
            self.assertIn("Agent/Routine", moves)
            self.assertIn("Agent/Weitergeleitet", moves)
            sent = (temp / "sent.mml").read_text(encoding="utf-8")
            self.assertIn("type=application/zip", sent)
            self.assertNotIn("type=message/rfc822", sent)
            self.assertIn("Message-ID: <mail-agent-forward-", sent)
            self.assertIn("Mail delivery failed", sent)
            self.assertEqual(list((temp / "payloads").glob("*")), [])


if __name__ == "__main__":
    unittest.main()
