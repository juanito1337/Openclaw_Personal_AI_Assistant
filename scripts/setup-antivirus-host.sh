#!/usr/bin/env bash
set -Eeuo pipefail

if ((EUID != 0)); then
  echo "Dieses Host-Setup muss mit sudo ausgefuehrt werden:" >&2
  echo "  sudo bash $0" >&2
  exit 2
fi

OWNER_USER=${SUDO_USER:-jan}
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y clamav clamav-daemon clamdscan

# Stop the updater briefly so a foreground initial update cannot collide with it.
systemctl stop clamav-freshclam.service >/dev/null 2>&1 || true
freshclam --stdout
systemctl enable --now clamav-freshclam.service
systemctl enable --now clamav-daemon.service
systemctl restart clamav-daemon.service

for _ in $(seq 1 30); do
  if systemctl is-active --quiet clamav-daemon.service; then
    break
  fi
  sleep 1
done
systemctl is-active --quiet clamav-daemon.service || {
  systemctl status clamav-daemon.service --no-pager >&2 || true
  exit 1
}

# Membership is harmless and helps distributions that restrict the Unix socket
# to the clamav group. The scanner also uses fdpass/stream fallbacks.
if id "$OWNER_USER" >/dev/null 2>&1; then
  usermod -aG clamav "$OWNER_USER" || true
fi

clamdscan --version
printf 'Personal Assistant ClamAV health check\n' > /tmp/personal-assistant-clamav-health.txt
chmod 0644 /tmp/personal-assistant-clamav-health.txt
clamdscan --fdpass --no-summary --stdout /tmp/personal-assistant-clamav-health.txt || \
  clamdscan --stream --no-summary --stdout /tmp/personal-assistant-clamav-health.txt
rm -f /tmp/personal-assistant-clamav-health.txt

echo
echo "ClamAV ist installiert und aktiv."
echo "Falls der Benutzer neu zur Gruppe clamav hinzugefuegt wurde, einmal ab- und wieder anmelden."
