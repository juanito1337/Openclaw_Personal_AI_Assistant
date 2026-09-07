#!/usr/bin/env bash
set -euo pipefail
umask 007

database=${CLAMAV_DATABASE_DIR:-/var/lib/clamav}
socket_path=${OPENCLAW_CLAMD_SOCKET:-/run/clamav/clamd.sock}
max_stream=${CLAMAV_STREAM_MAX_BYTES:-100000000}
max_threads=${CLAMAV_MAX_THREADS:-2}
max_queue=${CLAMAV_MAX_QUEUE:-16}
timeout=${CLAMAV_SCAN_TIMEOUT_SECONDS:-120}
self_check=${CLAMAV_SELF_CHECK_SECONDS:-600}

[[ "$database" == /var/lib/clamav ]] || {
  echo "Unerwartetes ClamAV-Datenbankverzeichnis" >&2
  exit 2
}
[[ "$socket_path" == /run/clamav/clamd.sock ]] || {
  echo "Unerwarteter ClamAV-Socketpfad" >&2
  exit 2
}
for value in "$max_stream" "$max_threads" "$max_queue" "$timeout" "$self_check"; do
  case "$value" in
    ''|*[!0-9]*) echo "Ungueltiger numerischer ClamAV-Wert" >&2; exit 2 ;;
  esac
done
(( max_stream >= 1024 && max_stream <= 1000000000 )) || exit 2
(( max_threads >= 1 && max_threads <= 32 )) || exit 2
(( max_queue >= max_threads && max_queue <= 256 )) || exit 2
(( timeout >= 5 && timeout <= 1800 )) || exit 2
(( self_check >= 60 && self_check <= 86400 )) || exit 2

python3 -P -m personal_assistant.clamav_health >/dev/null
[[ -d /run/clamav && ! -L /run/clamav ]] || {
  echo "ClamAV-Socketverzeichnis fehlt oder ist ein Symlink" >&2
  exit 2
}
rm -f -- "$socket_path"

config=/tmp/clamd.conf
{
  echo "DatabaseDirectory $database"
  echo "LocalSocket $socket_path"
  echo "LocalSocketGroup clamav"
  echo "LocalSocketMode 660"
  echo "FixStaleSocket yes"
  echo "Foreground yes"
  echo "LogTime yes"
  echo "LogClean no"
  echo "ExtendedDetectionInfo no"
  echo "TemporaryDirectory /tmp"
  echo "MaxScanSize $max_stream"
  echo "MaxFileSize $max_stream"
  echo "StreamMaxLength $max_stream"
  echo "MaxThreads $max_threads"
  echo "MaxQueue $max_queue"
  echo "ReadTimeout $timeout"
  echo "CommandReadTimeout $timeout"
  echo "SendBufTimeout $timeout"
  echo "MaxScanTime $((timeout * 1000))"
  echo "SelfCheck $self_check"
  echo "ExitOnOOM yes"
} > "$config"
chmod 0400 "$config"

exec /usr/sbin/clamd --config-file="$config"
