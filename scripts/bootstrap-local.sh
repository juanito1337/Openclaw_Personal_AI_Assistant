#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
umask 077

created=0
for name in config rules; do
  target="mail_agent/${name}.toml"
  source="mail_agent/${name}.example.toml"
  if [[ -e "$target" ]]; then
    echo "Vorhanden, nicht ueberschrieben: $target"
  else
    cp "$source" "$target"
    chmod 600 "$target"
    echo "Erstellt: $target"
    created=1
  fi
done

mkdir -p \
  mail_agent/data/forward_payloads \
  mail_agent/data/calendar_pending \
  mail_agent/data/calendar_created \
  mail_agent/data/backups
chmod 700 mail_agent/data mail_agent/data/* 2>/dev/null || true

mkdir -p "$HOME/.config"
if [[ ! -e "$HOME/.config/mail-agent.env" ]]; then
  install -m 600 deploy/mail-agent.env.example "$HOME/.config/mail-agent.env"
  echo "Erstellt: $HOME/.config/mail-agent.env (Platzhalter vor Nextcloud-Nutzung ersetzen)"
else
  echo "Vorhanden, nicht ueberschrieben: $HOME/.config/mail-agent.env"
fi

if (( created )); then
  echo "Naechster Schritt: ./scripts/mail-agent.sh configure"
else
  echo "Lokale Konfiguration ist bereits vorhanden."
fi
