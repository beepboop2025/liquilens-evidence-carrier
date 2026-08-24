# LiquiLens Evidence for VS Code

LiquiLens Evidence adds local, fail-closed diagnostics for files ending in
`*.evidence.json` or `*.carrier.json`. It reuses the release browser verifier
to check exact fields, canonical content identity, clock order, rights,
redaction, and the all-false financial-authority boundary.

Verification runs inside the extension host. The extension does not upload file
contents, fetch a schema, collect telemetry, trade, recommend, rate credit, or
change the carrier. A successful projection or verification never widens the
source's redistribution rights.

## Use

Open or save a matching carrier file. Invalid evidence appears in the Problems
panel. To verify any active JSON document explicitly, run
**LiquiLens: Verify Evidence Carrier** from the Command Palette.

Automatic open/save validation can be disabled with
`liquilensEvidence.validateOnOpen` and
`liquilensEvidence.validateOnSave`.

The verifier evaluates the carrier's declared `clocks.as_of`; it does not
silently substitute the workstation clock. Use the LiquiLens CLI or MCP server
when a separate current policy-evaluation time is required.

## Install an attested VSIX from a signed source tag

Download the VSIX and `SHA256SUMS` from the `vscode-v0.1.0` GitHub
release. The release workflow verifies the signed source tag and commit, then
publishes a build-provenance attestation for the VSIX. Verify the checksum,
then run:

```bash
code --install-extension liquilens-evidence-0.1.0.vsix
```

Store listings are separate distribution events. A VSIX release does not imply
publication or endorsement by Microsoft, Eclipse Open VSX, Bloomberg, LSEG, or
another third party.
