# Host antivirus gate

The Personal Assistant uses ClamAV on the host. `clamav-daemon` keeps the engine
and signatures resident; the agent starts individual scans with `clamdscan`.
`clamscan` is an optional slower fallback.

## Security order

1. Export the original RFC822 message to a private temporary file.
2. Scan the complete raw message.
3. Parse the mail only after a clean result.
4. Extract and scan every physical attachment individually.
5. Block forwarding, calendar commands and invoice actions on infection or scan error.
6. Re-check selected invoice PDFs immediately before ActionPlan creation.
7. Scan every controlled workspace upload before a Nextcloud write.

Clean-cache entries are keyed by SHA-256 and the complete ClamAV engine/database
identity. Signature updates therefore invalidate older clean results.

## Outcomes

- clean: processing continues;
- infected: mail moves to `Agent/Virusverdacht`;
- scanner error or size limit: fail-closed, mail moves to `Agent/Fehler`;
- no infected content is deleted or submitted to an external service.

## Commands

```bash
sudo bash scripts/setup-antivirus-host.sh
./scripts/mail-agent.sh setup
./scripts/assistant.sh security antivirus doctor
./scripts/assistant.sh security antivirus self-test
./scripts/assistant.sh security antivirus scan \
  --file personal_assistant/data/workspace_outbox/example.pdf
```

The manual scan command accepts only files inside the controlled workspace
outbox. Automatic mail scans use private temporary files with mode 0600.
