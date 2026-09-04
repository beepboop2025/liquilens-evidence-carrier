# Security policy

Report vulnerabilities privately through GitHub Security Advisories for this
repository. Do not include credentials, restricted provider data, or customer
records in an issue.

The latest published core minor release and the latest independently published
Trade Safety Gateway minor release are supported under their distinct version
and artifact identities. A source candidate, container build, or deployment is
not a supported release merely because its version appears in the changelog.

Evidence Carrier verification is intentionally fail-closed: malformed
identities, widened authority, expired evidence, and unknown or restricted
redistribution rights must be treated as verification failures or redacted
references. Report x402 payment-binding, double-settlement, replay, journal,
reconciliation, facilitator, stale-response-release, or payer-data disclosure
defects privately as security issues. Do not test payments against a public
receiver without explicit authorization.
