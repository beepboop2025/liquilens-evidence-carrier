# Core 0.20.1 publication receipt

Verified at 2026-09-09T01:26:45.075487+00:00. Status: signed, published, attested, and active/latest.

The offline routing resource uses Palimpsest's published catalog API while its
additional MCP tool awaits owner approval. This release preserves the existing
verification tools, Trade Safety schema bytes, and independent channel versions.

- Signed source: `6f9077bf0879a76db7b9bf98fb37839f7755dd9f`.
- Signed annotated tag: `v0.20.1`, object `6e00524ab201c87dd87273c8c205f82912c1b80f`.
- Protected-main [preflight 34298597462](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/34298597462) passed before tagging.
- [Release 34298762148](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/34298762148/attempts/1) published 23 assets at `2026-09-09T01:20:44Z`. All 22 downloaded artifact checksums passed.
- SHA256SUMS SHA-256: `0a5982010e361b8561730076125f92574cb531b743702d437ccf7f400114a4ae`.
- Python wheel SHA-256: `fcf11829842de317abf42be9cbfe19bdd138dad99e5ac60476f87048da1f9768` (183,175 bytes).
- Deterministic MCPB SHA-256: `09b25571f61f4ba2bd5e1c367a580355a6e08eccf0d1c34bdde0bbd460be0ed6` (98,570 bytes).

Independent `gh attestation verify` checks for the downloaded wheel and MCPB
matched the exact signed source, `refs/tags/v0.20.1`, GitHub-hosted execution,
and release invocation above. Each verified statement bound all 22 artifact hashes.
The downloaded routing resource explicitly uses the available catalog API and
does not advertise the pending Palimpsest tool as callable.

The [official Registry record](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.beepboop2025%2Fliquilens-evidence-carrier/versions/0.20.1)
and latest record were independently retrieved. Both matched the signed
`server.json`, reported `active` and `isLatest: true`, and pinned the MCPB hash
above. Registry publication time: `2026-09-09T01:21:00.066329Z`.

The immutable per-version README inside the MCPB remains the original candidate
text; the publication evidence is this external receipt. Core publication does
not update independently released OCI, gateway, editor, skill, plugin, or hosted
services. Their existing receipts retain their own identities and authority limits.
