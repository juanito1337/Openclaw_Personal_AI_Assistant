# R19 Adaptive Ollama Batches

R19 keeps `ollama.batch_size` as a hard upper limit and reduces only groups that
are likely to create slow or fragile model requests.

A message is considered structurally complex when it contains a calendar invite,
a body longer than the configured threshold, or more attachments than the
configured threshold. Calendar invitations are always isolated. Two complex
messages are not placed in the same batch. The estimated combined prompt also
must remain below `batch_adaptive_target_chars`.

Defaults are intentionally conservative and backward compatible:

```toml
batch_adaptive_enabled = true
batch_adaptive_target_chars = 18000
batch_adaptive_heavy_body_chars = 3500
batch_adaptive_max_attachments = 2
```

The existing `batch_size = 5` remains unchanged. Disabling adaptive batching
restores the exact legacy fixed-size grouping.

No category, rule, threshold, destination, model, timeout, `num_ctx`, or
`num_predict` value is changed by R19.
