# Core 0.20.0 publication receipt

Core `v0.20.0` was published at `2026-09-08T20:44:43Z` by
[release run 34276508204](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/34276508204),
after protected preflight run `34276360736`. Signed annotated tag object
`51cbb4645bbd603f38be65f23d8c03a4b9d30272` targets signed release commit
`bfe665431c0c2203e71d1a5e9c7ba57095320585`.

The anonymously downloaded wheel and MCPB match the published `SHA256SUMS`:

- `liquilens_evidence-0.20.0-py3-none-any.whl`:
  `d864af722153f66a5cad5855ce00fb70809a4a078d7d972a75ce714ebbdf57cc`
- `liquilens-evidence-carrier-mcp-0.20.0.mcpb`:
  `1c740167183cba7ad39862ee749f497930f43cfd27b1362d703c81e71d5b2d6f`

Both attestations verified against `.github/workflows/release.yml`,
`refs/tags/v0.20.0`, the exact source digest above, and run attempt 1.
The [official Registry entry](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.beepboop2025%2Fliquilens-evidence-carrier/versions/0.20.0)
was read back as active and latest with the same MCPB digest.

The release adds two offline MCP resources:

- `liquilens-evidence://research/network-schema`: the Seiche research-network
  response schema, including source rights, missingness and pagination.
- `liquilens-evidence://research/network-routing`: explicit research routes
  across Palimpsest, Seiche, LiquiLens, Undertow, NarcoScope and Market Brief.

These are additive discovery resources. Existing carrier and Trade Safety v1
schemas and the four verification/projection tools retain their meanings.
Existing documents require no migration. The offline server performs no fetch,
and reading the new resources grants no publication, training or financial
authority. The separately deployed producers own their rights and freshness
decisions.

Release acceptance used the existing metadata preflight, offline contracts,
package checks, reproducibility check, controlled tag creation, and signed
release workflow. Independent consumer channels retain their existing released
pins until their own release receipts establish an upgrade. This receipt does
not claim a PyPI, OCI, gateway, IDE extension, or other independently versioned
consumer release.
