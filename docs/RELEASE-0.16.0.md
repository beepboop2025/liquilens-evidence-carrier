# v0.16.0 release record

Release state verified 2026-08-29: **signed, published, attested, and active in
the official MCP Registry**.

The signed tag object
`c94dd8dd4208fee3f741730cc9c25f591e5bd7aa` points to reviewed release commit
`410f7d91114fba715e9a9ae830faa775064a4502`. Protected-main merge
`aac924b1d8f7fe7215e8853788edfdf4039a46f5` contains that commit. GitHub
published the release at `2026-08-29T15:46:36Z`, and the official Registry
published active/latest version `0.16.0` at
`2026-08-29T15:46:49.069496Z`.

## Scope

- Version the Python package, protocol catalog, MCPB manifest, Registry
  declaration, and Codex plugin together as `0.16.0`.
- Expose the existing local four-product Fleet Brief verification capability in
  plugin discovery metadata.
- Keep the MCP server offline and read-only. It still accepts only explicit
  local paths under the configured root, performs no discovery fetch, and has
  no trading, recommendation, rating, payment, credential, or platform
  authority.
- Make no schema, canonical identifier, evidence-clock, rights, or Fleet Brief
  state-semantics change.

## Published artifacts and receipts

- Wheel: `liquilens_evidence-0.16.0-py3-none-any.whl`, SHA-256
  `317c06b728a2b087eca3d51ba1cdf3f7570e4078334829959008ceb0a29dfd11`.
- Python sdist: `liquilens_evidence-0.16.0.tar.gz`, SHA-256
  `62c4693ac8be044d8ef6931a34cfab46018c895884edffee0a82db63462d2fd2`.
- MCPB: `liquilens-evidence-carrier-mcp-0.16.0.mcpb`, SHA-256
  `c44b13b2efc4622a8ecfc06848f32358982dd2a9458a271e1ed77d646791961a`.
- Integration kit: `liquilens-evidence-carrier-0.16.0.tar.gz`, SHA-256
  `542d3074e52f69a60bdbfba794185fd472b62f1e80f475e1d1f72f04d6f2fb1e`.
- Checksum manifest: `SHA256SUMS`, SHA-256
  `8bcfe088e85fb8da6867e996e73840f374deb28e0d43e06206ea117605176e94`.

Release workflow
[33261143612](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33261143612)
verified the signed tag and commit, rebuilt and replayed the deterministic MCPB,
published the assets and build-provenance attestations, verified the anonymous
MCPB download, and published the matching `server.json` record. The Registry
record is
[`0.16.0`](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.beepboop2025%2Fliquilens-evidence-carrier/versions/0.16.0).

## Completed release gates

1. The reviewed source merged to protected `main` with CI green.
2. Version surfaces, plugin/skill parity, protocol hashes, deterministic MCPB
   replay, MCP protocol responses, and the full test matrix passed.
3. The signed `v0.16.0` tag was created on the reviewed commit and has not been
   moved or reused.
4. `.github/workflows/release.yml` rebuilt from that signed commit, compared two
   MCPB builds byte-for-byte, published checksums, and attested runnable/package
   artifacts.
5. The anonymous MCPB download matched both `server.json` and `SHA256SUMS`, and
   its GitHub build-provenance attestation verified.
6. The dependent job published `0.16.0` to the official MCP Registry, where it
   is active and latest.

## Independent channels and the embedded README

The core GitHub release and official MCP Registry are `0.16.0`. Separate
container, Nix, browser, Agent Skill, Codex-plugin, and external-directory
channels remain at their independently verified versions until their own build,
publication, and retrieval receipts exist. A core release does not silently
promote those channels.

`README.md` was embedded in the deterministic `v0.16.0` MCPB before publication
and therefore contains the then-correct preparation checkpoint. That released
bundle is immutable. The repository preserves those exact bytes in
`mcpb/release-readmes/0.16.0.md` so the published hash remains reproducible,
while the root README records current live state. Correcting the README inside
the distributed MCPB requires a new patch release; it must not be achieved by
moving the `v0.16.0` tag or replacing its assets.
