# LiquiLens Evidence Carrier distribution

This matrix records public ways to run LiquiLens Evidence Carrier. A channel is
called **live** only when an anonymous or otherwise public retrieval and a real
runtime check have succeeded. An upstream pull request remains **under review**
until its maintainer merges and deploys it.

The current core implementation release is `v0.19.0`. Annotated tag object
`c3239bfc7c4d3c4b7fc5ce26e0f602962e7d4337` targets allowlisted SSH-signed
release commit `8f5738c9e77cc95b9a68543d478b9521f5595d61`, tree
`acca6fa7aab75ebc91bf044e153c6468cd6f9c0c`. The exact commit passed
[preflight run 33630656569](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33630656569)
before the tag was created. The tag-triggered
[release run 33630790150](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33630790150)
published 23 assets at `2026-09-02T12:36:19Z`; all 22 entries in the downloaded
`SHA256SUMS` passed, and strict GitHub provenance verification matched the same
22 non-manifest subjects. The official MCP Registry record was independently
verified active/latest at 0.19.0.

Core `v0.19.0`, gateway `0.1.2`, OpenBB extension `0.2.0`, and the
TypeScript-compatible Node package `0.1.0` add raw-byte conformance and
paper-only enforcement while keeping live orders unconditionally held. The
root wheel SHA-256 is
`1adccb72376f50456fd16a979e372f802ae73ba35b766633bc3d8bd4ab5abcc8`;
the MCPB SHA-256 is
`11db11aefafcc6c4ba558877d1f9892fc708150b3afbaa28a741e74435b9a91a`;
the gateway 0.1.2 wheel SHA-256 is
`e3c685a300aadaafa406ccf38b2d8c56107e7145f6a075d0909a9c74a715f285`;
and the `SHA256SUMS` file hashes to
`c6d52cbf8794db6e478e3b2ea9e1ed8eee7757137650892a6a96fcbb839bb6bc`.
[Release attestation 44695012](https://github.com/beepboop2025/liquilens-evidence-carrier/attestations/44695012)
binds those 22 artifacts to the signed source and tagged release workflow.

The core CLI OCI index is published at
`sha256:bdbfed2afa87f25e8ef88dffeb4ba7ab198854705528c0de5abe31552a170b9a`
by [run 33630789998](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33630789998),
with [attestation 44695462](https://github.com/beepboop2025/liquilens-evidence-carrier/attestations/44695462).
The read-only gateway index is published at
`sha256:b5c43013da1fdddd9e6e56cab0e4f0f562e39ab25cc640869c5008e3457218e3`
by [run 33630790011](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33630790011),
with [attestation 44695195](https://github.com/beepboop2025/liquilens-evidence-carrier/attestations/44695195).
GitHub reports the Release record as `immutable: false`; ruleset `21288366`
protects `v*` tags from update/deletion with no bypass, and the current asset
bytes are checksum- and transparency-attested, but the Release assets are not
claimed as platform-enforced immutable.

Core publication does not automatically update the independently released OCI,
Nix, browser, Agent Skill, Codex-plugin, or external-directory channels. The
matrix below keeps each channel at its own receipt-backed version. LiquiLens is
not endorsed by the platforms or projects named here.

## Trade Safety v1 published core and hosted boundary

Release `v0.19.0` carries strict Trade Safety request, policy, broker-preview
and receipt contracts, local CLI/MCP verification, cross-language vectors,
FDC3 assets, and separately packaged read-only sandbox-gateway wheels/source.
The release and official Registry publication are live; a hosted gateway is
not. The five canonical Trade Safety schema/FDC3 URLs became live with exact
tagged bytes in LiquiLens Pages
[run 33592149926](https://github.com/beepboop2025/liquilens-site/actions/runs/33592149926),
which succeeded at 2026-09-02T04:49:12Z for site revision
`3ec660175c81c5b282715ee400eea2f771dc2610`. Production consumers can pin
v0.19.0 for local or tenant-hosted use, but must not infer a hosted gateway or
live-order activation from package or schema publication.

### Immutable v0.17.0 failed attempt

The annotated `v0.17.0` tag object
`cb85e527c2b74abf476fd9a01b73b2235ce976b7` is immutable and targets GitHub
protected-main merge `edde9b92ad9851d2974b91326a8c3877f4386d3a`.
[Release run 33585764285](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33585764285)
failed in `Verify signed tag, commit, ancestry, and version`: tag verification
passed, but the merge-commit signature could not be verified. Artifact build,
attestation, GitHub publication, and the Registry job were skipped. No v0.17.0
GitHub release or official MCP Registry version exists, so `v0.17.0` is not a
distribution pin and must not be recreated or force-moved. The frozen
`mcpb/release-readmes/0.17.0.md` continues to describe only the candidate bundle
bytes that were prepared; it is not a publication receipt.

## Fleet Brief v1

`liquilens.fleet-brief.v1` adds one content-addressed section for each canonical
producer without fetching native carriers or combining their scores. The
package, CLI, and offline MCP server support local issuance/verification; the
schema and examples are in `protocol/liquilens-fleet-brief-v1.schema.json` and
`examples/fleet-brief/`. They are included in the signed `v0.19.0` release.
That makes the contract available; it does not claim that every product already
emits a native carrier or that every downstream directory has indexed it.

## Python release and uvx

The `v0.19.0` release wheel has SHA-256
`1adccb72376f50456fd16a979e372f802ae73ba35b766633bc3d8bd4ab5abcc8`.

```bash
uvx --no-cache \
  --from 'https://github.com/beepboop2025/liquilens-evidence-carrier/releases/download/v0.19.0/liquilens_evidence-0.19.0-py3-none-any.whl#sha256=1adccb72376f50456fd16a979e372f802ae73ba35b766633bc3d8bd4ab5abcc8' \
  liquilens-evidence --help
```

## Homebrew

```bash
brew install beepboop2025/tap/liquilens-evidence
liquilens-evidence --help
```

## Nix

The flake was exercised on Linux and macOS, on both x86-64 and ARM64. Pin the
tested source revision:

```bash
nix run github:beepboop2025/liquilens-evidence-carrier/3c97b71093f8bca201e74bb5cc7ddbe50d9fa052 -- --help
nix run github:beepboop2025/liquilens-evidence-carrier/3c97b71093f8bca201e74bb5cc7ddbe50d9fa052#mcp -- --root ./evidence
```

## OCI images

The CLI image is a public multi-platform image:

```bash
docker run --rm \
  ghcr.io/beepboop2025/liquilens-evidence-carrier@sha256:bdbfed2afa87f25e8ef88dffeb4ba7ab198854705528c0de5abe31552a170b9a \
  --help
```

The v0.19.0 CLI index was published and its amd64/arm64 manifests were exercised
by [container run 33630789998](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33630789998).
The index resolves to `linux/amd64`
`sha256:1262b93244da0f6666e8ae55d19fda70a173f65d552064598ff9c44760b99c6b`
and `linux/arm64`
`sha256:e408163fcc41ac7166e7ff86aaa55f042791259d3a58ea59ae0e81ffd175acbc`.
[Attestation 44695462](https://github.com/beepboop2025/liquilens-evidence-carrier/attestations/44695462)
binds the exact index digest to release commit
`8f5738c9e77cc95b9a68543d478b9521f5595d61` and `container.yml` on the v0.19.0
tag. Each platform manifest has an attached SPDX SBOM and SLSA v1 BuildKit
provenance layer.

The gateway image is a separate public multi-platform, read-only sandbox:

```bash
docker run --rm --network none --read-only \
  ghcr.io/beepboop2025/liquilens-trade-safety-gateway@sha256:b5c43013da1fdddd9e6e56cab0e4f0f562e39ab25cc640869c5008e3457218e3
```

[Gateway run 33630790011](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/33630790011)
resolved `linux/amd64`
`sha256:3ec553fcbbaef30e496b15895231ff628ddce7f72bcdf30817f2366171f426b2`
and `linux/arm64`
`sha256:0d20d45473b6d389df116336a48c5bb19e7fc385dac4448f02e6593d8ce2e94f`,
then smoke-tested the published digest. Its index labels gateway `0.1.2`, core
`0.19.0`, and authority `read-only-hash-only-sandbox`.
[Attestation 44695195](https://github.com/beepboop2025/liquilens-evidence-carrier/attestations/44695195)
binds the index to the same release commit and tag; each platform also has SPDX
SBOM and SLSA v1 BuildKit provenance layers.

The currently published independently versioned dedicated MCP image remains at
its v0.15.0 receipt. It defaults to the stdio server and runs as UID/GID 65532,
but it does not contain the v0.19.0 Trade Safety verifier. Its controlled v0.19.0
workflow has not been dispatched, so the dedicated MCP image is not silently
claimed current. Keep the evidence mount read-only and disable network access:

```bash
docker run --rm -i --network none --read-only \
  --mount "type=bind,src=$PWD/evidence,dst=/evidence,readonly" \
  ghcr.io/beepboop2025/liquilens-evidence-carrier-mcp@sha256:4e4ffb010b52375b3203b2dc43706c7fa508de2bf8368eca465f49d56392dcea
```

The current core and gateway indexes both include `linux/amd64` and
`linux/arm64` manifests. The older dedicated MCP index was published and
smoke-tested without network access
or a writable root in [run 32897003998](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/32897003998).

## MCP clients

The official MCP Registry entry is
[`io.github.beepboop2025/liquilens-evidence-carrier`](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.beepboop2025%2Fliquilens-evidence-carrier/versions/0.19.0).
The signed release also includes a checksum-pinned
[`MCPB bundle`](https://github.com/beepboop2025/liquilens-evidence-carrier/releases/download/v0.19.0/liquilens-evidence-carrier-mcp-0.19.0.mcpb)
with SHA-256
`11db11aefafcc6c4ba558877d1f9892fc708150b3afbaa28a741e74435b9a91a`.
The record was published at `2026-09-02T12:36:47.695864Z`, reports `active`
and `isLatest: true`, and declares stdio transport, `networkAccess: false`, and
`financialAuthority: none`.

For direct stdio configuration:

```json
{
  "mcpServers": {
    "liquilens-evidence-carrier": {
      "command": "liquilens-evidence-mcp",
      "args": ["--root", "/absolute/path/to/evidence"]
    }
  }
}
```

## Coding agents

The Fleet-Brief-aware public skill is pinned by the signed `skill-v0.15.0` tag:

```bash
npx --yes skills@1.5.23 add \
  https://github.com/beepboop2025/liquilens-evidence-carrier/tree/skill-v0.15.0/skills/liquilens-evidence \
  --skill liquilens-evidence --agent codex --copy --yes
```

An anonymous `skills@1.5.23` discovery found exactly one skill, and a clean
project install matched SHA-256
`adef1a05e047457b752543633536b4e857532b194bf83396175f3f625bc87379`.
The source tag resolves to commit
`afeb5ad9f7ab795a23882a9e714156c6aafd59c0`; the corresponding public CI
receipt is [run 32896092859](https://github.com/beepboop2025/liquilens-evidence-carrier/actions/runs/32896092859).
It is also discoverable on
[`skills.sh`](https://skills.sh/beepboop2025/liquilens-evidence-carrier/liquilens-evidence).
The skill verifies and rights-checks caller-supplied carriers; it does not
collect data, trade, recommend, or grant financial authority.

### Codex plugin marketplace

The same skill is packaged as a Git-backed Codex plugin. Pin the signed plugin
tag, then install the explicit opt-in plugin from the configured marketplace:

```bash
codex plugin marketplace add beepboop2025/liquilens-evidence-carrier \
  --ref plugin-v0.14.1
codex plugin add liquilens-evidence@liquilens
```

The plugin deliberately does not auto-start an MCP server or choose a local
filesystem root. Configure the verifier separately for the directory the user
has actually authorized.

## VS Code desktop, remote, web, and Codespaces

The public `vscode-v0.1.0` release contains an attested VSIX built from a signed
source tag. The package is 16,612 bytes with SHA-256
`ebc17ca1aa54d3e6c93494bb19f82df2f6460f314c40074a4f6b41d94170d6cf`.

```bash
curl --fail --location --remote-name \
  https://github.com/beepboop2025/liquilens-evidence-carrier/releases/download/vscode-v0.1.0/liquilens-evidence-0.1.0.vsix
curl --fail --location --remote-name \
  https://github.com/beepboop2025/liquilens-evidence-carrier/releases/download/vscode-v0.1.0/SHA256SUMS
shasum -a 256 --check SHA256SUMS
code --install-extension liquilens-evidence-0.1.0.vsix
```

The extension verifies matching carrier JSON locally and fail-closed. It has no
runtime dependency, telemetry, schema fetch, or financial authority. The same
browser bundle supports desktop, remote, virtual, untrusted, and web
workspaces. This public release is not a Microsoft Marketplace or Open VSX
listing; those stores require separate publisher-controlled publication.

## Airflow

The separate Apache Airflow provider release is checksum-pinned to a public
wheel whose provider discovery, database migration, DAG serialization,
operator execution, and DAG run passed in a clean Airflow 3.3.1 installation:

```bash
python -m pip install \
  'https://github.com/beepboop2025/liquilens-airflow-provider/releases/download/v0.1.0/liquilens_airflow_provider-0.1.0-py3-none-any.whl#sha256=aa91a2528ebf2e1583c379a08ce60f9aa52fc33d9d89da0bab9876d5720956bf'
```

Repository and release receipts are at
[`beepboop2025/liquilens-airflow-provider`](https://github.com/beepboop2025/liquilens-airflow-provider/releases/tag/v0.1.0).
This is a public provider artifact, not an Apache Software Foundation catalog
listing or endorsement.

## Dev Containers and Codespaces images

Use the public Dev Container Feature by immutable OCI digest in
`.devcontainer/devcontainer.json`:

```json
{
  "features": {
    "ghcr.io/beepboop2025/liquilens-devcontainer-features/liquilens-evidence@sha256:79ac17d7c3f91dc9360c6aa63cb9e4fa0081d5c81e1a1492b2198a8280f5b22d": {}
  }
}
```

An anonymous non-root consumer resolved that digest, revalidated the exact
carrier wheel, and exercised both the CLI and MCP server. The feature is
available directly from GHCR and listed in the
[official feature index](https://containers.dev/features). Feature `1.0.0`
installs Carrier `0.14.0`; see the [installation guide](docs/INSTALLATION.md).

## Browser and notebooks

- [Browser verifier](https://beepboop2025.github.io/liquilens-evidence-carrier/)
  performs verification locally in the browser and does not upload the carrier.
- [Anonymous Binder notebook](https://mybinder.org/v2/gh/beepboop2025/liquilens-evidence-carrier/3d079421c830fcc97ea08da3c54b8429eb5ed542?urlpath=lab/tree/notebooks/evidence_carrier_research.ipynb)
  executes a synthetic verification and tamper-rejection walkthrough.
- [Exact-revision Colab notebook](https://colab.research.google.com/github/beepboop2025/liquilens-evidence-carrier/blob/3d079421c830fcc97ea08da3c54b8429eb5ed542/notebooks/evidence_carrier_research.ipynb)
  loads publicly; attaching a Colab runtime can require a Google session.

## OpenBB

The standalone Router extension 0.2.0 is checksum-pinned to the signed v0.19.0
release asset. Its build, route registration, and generated Python call passed
on Python 3.11 through 3.14:

```bash
python -m pip install \
  'https://github.com/beepboop2025/liquilens-evidence-carrier/releases/download/v0.19.0/openbb_liquilens_evidence-0.2.0-py3-none-any.whl#sha256=1dea5f5284e45746f094c5051d11f8c2aca9b9e5e62bc4f7d41e10965d5fc82d'
openbb-build --force
```

It exposes `obb.liquilens.verify` and `POST /api/v1/liquilens/verify`. It is an
offline verifier for caller-supplied JSON, not a market-data provider.

## Repository-native enforcement

- Pin the GitHub Action with
  `uses: beepboop2025/liquilens-evidence-carrier@v0.19.0`.
- Configure the signed `v0.19.0` hook in `.pre-commit-config.yaml` to verify
  `*.evidence.json` and `*.carrier.json` before a commit lands.
- Use the dbt project directly from a pinned Git revision until its dbt Hub
  submission is accepted.

## Upstream distribution state

### Live or merged upstream

- [Dev Containers community index #729](https://github.com/devcontainers/devcontainers.github.io/pull/729)
  is merged, and the [Dev Container collections catalog](https://containers.dev/collections.html)
  lists the LiquiLens Evidence Carrier Features collection.
- [SchemaStore catalog #6238](https://github.com/SchemaStore/schemastore/pull/6238)
  is merged for the Carrier and redacted-reference schemas.
- [conda-forge staged recipe #34616](https://github.com/conda-forge/staged-recipes/pull/34616)
  is merged. Carrier `0.15.0` installs from the ordinary `conda-forge` channel;
  the [installation guide](docs/INSTALLATION.md) records the tested command,
  exact artifact, and version boundaries.

Fleet Brief v1 is not claimed as a SchemaStore listing; its follow-up entry
waits for the canonical site schema to be deployed and verified.

### Under external review

These submissions are open for external review and are not described as live
upstream listings:

- [FINOS Labs FDC3 App Directory #40](https://github.com/finos-labs/FDC3-App-Directory/pull/40)
- [dbt HubCap #446](https://github.com/dbt-labs/hubcap/pull/446)
- [Docker MCP Registry: Evidence Carrier #4766](https://github.com/docker/mcp-registry/pull/4766)
- [Docker MCP Registry: Financial Evidence #4765](https://github.com/docker/mcp-registry/pull/4765)
- [Awesome OpenBB #11](https://github.com/OpenBB-finance/awesome-openbb/pull/11)

### Closed without listing

- [GitHub Awesome Copilot #2785](https://github.com/github/awesome-copilot/pull/2785)
  was closed without merge and is not represented as an upstream listing.

Bloomberg, LSEG, FactSet, S&P Global, Nasdaq, and other proprietary financial
platforms require their own entitlements, commercial validation, rights review,
or operator-controlled publication. A public adapter or outreach message is not
represented as a deployment on those systems.
