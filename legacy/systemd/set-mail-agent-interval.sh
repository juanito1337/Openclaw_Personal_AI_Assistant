#!/usr/bin/env bash
set -euo pipefail

if [[ ${OPENCLAW_ENABLE_LEGACY_SYSTEMD:-} != "YES" ]]; then
  echo "Abgelehnt: eingefrorener Legacy-systemd-Helfer; fuer den Dockerbetrieb 'assistant jobs' verwenden." >&2
  echo "Nur ein verifizierter Legacy-Rollback darf OPENCLAW_ENABLE_LEGACY_SYSTEMD=YES setzen." >&2
  exit 2
fi

INTERVAL="${1:-}"
TIMER_FILE="$HOME/.config/systemd/user/mail-agent.timer"

usage() {
  cat <<'USAGE'
Verwendung:
  OPENCLAW_ENABLE_LEGACY_SYSTEMD=YES ./legacy/systemd/set-mail-agent-interval.sh 15m
  OPENCLAW_ENABLE_LEGACY_SYSTEMD=YES ./legacy/systemd/set-mail-agent-interval.sh 20m
  OPENCLAW_ENABLE_LEGACY_SYSTEMD=YES ./legacy/systemd/set-mail-agent-interval.sh 30m
  OPENCLAW_ENABLE_LEGACY_SYSTEMD=YES ./legacy/systemd/set-mail-agent-interval.sh 1h
  OPENCLAW_ENABLE_LEGACY_SYSTEMD=YES ./legacy/systemd/set-mail-agent-interval.sh 2h
  OPENCLAW_ENABLE_LEGACY_SYSTEMD=YES ./legacy/systemd/set-mail-agent-interval.sh status

Das Intervall beginnt nach dem Ende des vorherigen Agentenlaufs.
Erlaubte Intervalle: 15m, 20m, 30m, 1h, 2h
USAGE
}

show_status() {
  systemctl --user status mail-agent.timer --no-pager || true
  echo
  systemctl --user list-timers --all | grep mail-agent || true
}

if [[ "$INTERVAL" == "status" ]]; then
  show_status
  exit 0
fi

case "$INTERVAL" in
  15m|20m|30m|1h|2h)
    ;;
  -h|--help|help|"")
    usage
    exit 0
    ;;
  *)
    echo "FEHLER: Nicht erlaubtes Intervall: $INTERVAL" >&2
    usage >&2
    exit 2
    ;;
esac

mkdir -p "$(dirname "$TIMER_FILE")"

if [[ -f "$TIMER_FILE" ]]; then
  BACKUP_DIR="$HOME/.local/state/mail-agent/backups"
  mkdir -p "$BACKUP_DIR"
  BACKUP_FILE="$BACKUP_DIR/mail-agent.timer.$(date +%Y%m%d-%H%M%S).bak"
  cp -a "$TIMER_FILE" "$BACKUP_FILE"
  mapfile -t OLD_BACKUPS < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'mail-agent.timer.*.bak' -printf '%T@ %p\n' | sort -nr | tail -n +11 | cut -d' ' -f2-)
  if (( ${#OLD_BACKUPS[@]} )); then
    rm -f -- "${OLD_BACKUPS[@]}"
  fi
  echo "Sicherung erstellt: $BACKUP_FILE"
fi

cat > "$TIMER_FILE" <<EOF_TIMER
[Unit]
Description=Mail-Agent nach Ende des vorherigen Laufs erneut pruefen

[Timer]
OnBootSec=2min
OnUnitInactiveSec=$INTERVAL
AccuracySec=1s
Unit=mail-agent.service

[Install]
WantedBy=timers.target
EOF_TIMER

systemd-analyze --user verify "$TIMER_FILE"
systemctl --user daemon-reload
systemctl --user enable --now mail-agent.timer
systemctl --user restart mail-agent.timer

echo
echo "Mail-Agent-Leerlaufintervall gesetzt auf: $INTERVAL"
show_status
