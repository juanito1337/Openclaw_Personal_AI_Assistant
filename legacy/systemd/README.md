# Frozen legacy systemd compatibility package

This directory is not the active deployment path. The immutable Docker stack and
its persistent desired job state are authoritative.

The units and interval helper are retained only so a verified pre-container home
can remain startable during the explicitly supported legacy rollback window. Never
start these writer units while the container mail worker is running. The package is
frozen from `3.4.0-r27.2.5` and verified by `manifest.json` through
`scripts/verify-legacy-package.py`.

Do not install these files for a new deployment. A legacy rollback must use the
verified home or linked migration archive recorded by the release backup. Removal
requires the M8 recovery gate and a separate accepted ADR.

Both the interval helper and the registered job controller reject activation unless
the verified rollback procedure explicitly sets
`OPENCLAW_ENABLE_LEGACY_SYSTEMD=YES`. Read-only detection and stopping an old writer
remain possible so container deployment can enforce the single-writer boundary.
