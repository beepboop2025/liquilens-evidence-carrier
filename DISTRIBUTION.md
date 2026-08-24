# LiquiLens Evidence Carrier distribution

This matrix records public ways to run LiquiLens Evidence Carrier. A channel is
called **live** only when an anonymous or otherwise public retrieval and a real
runtime check have succeeded. An upstream pull request remains **under review**
until its maintainer merges and deploys it.

The immutable implementation release is `v0.14.0`. Later source revisions below
add distribution adapters without changing that signed release. LiquiLens is
not endorsed by the platforms or projects named here.

## Python release and uvx

The release wheel has SHA-256
`f0162affab57307c8e20acf91dcefc33840f91e8cf9969a8d5ec8d8df860cd24`.

```bash
uvx --no-cache \
  --from 'https://github.com/beepboop2025/liquilens-evidence-carrier/releases/download/v0.14.0/liquilens_evidence-0.14.0-py3-none-any.whl#sha256=f0162affab57307c8e20acf91dcefc33840f91e8cf9969a8d5ec8d8df860cd24' \
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
  ghcr.io/beepboop2025/liquilens-evidence-carrier@sha256:9ec0646269357e971a67e88c8076c3c52c1561b094c1f2093ee19882a33294d1 \
  --help
```

The dedicated MCP image defaults to the stdio server, runs as UID/GID 65532,
and inherits the exact CLI image above. Keep the evidence mount read-only and
disable network access:

```bash
docker run --rm -i --network none --read-only \
  --mount "type=bind,src=$PWD/evidence,dst=/evidence,readonly" \
  ghcr.io/beepboop2025/liquilens-evidence-carrier-mcp@sha256:d55f69e55e579603ae8b510de76b1191047427a92569424a17729ea7f7e3e2f7
```

Both indexes include `linux/amd64` and `linux/arm64` manifests, SBOMs, BuildKit
provenance, OCI source metadata, and GitHub artifact attestations.

## MCP clients

The official MCP Registry entry is
[`io.github.beepboop2025/liquilens-evidence-carrier`](https://registry.modelcontextprotocol.io/v0/servers/io.github.beepboop2025%2Fliquilens-evidence-carrier/versions/0.14.0).
The signed release also includes a checksum-pinned
[`MCPB bundle`](https://github.com/beepboop2025/liquilens-evidence-carrier/releases/download/v0.14.0/liquilens-evidence-carrier-mcp-0.14.0.mcpb).

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

The public skill is pinned by the signed `skill-v0.14.0` tag:

```bash
npx --yes skills@1.5.23 add \
  https://github.com/beepboop2025/liquilens-evidence-carrier/tree/skill-v0.14.0/skills/liquilens-evidence \
  --skill liquilens-evidence --agent codex --copy --yes
```

It is also discoverable on
[`skills.sh`](https://skills.sh/beepboop2025/liquilens-evidence-carrier/liquilens-evidence).
The skill verifies and rights-checks caller-supplied carriers; it does not
collect data, trade, recommend, or grant financial authority.

### Codex plugin marketplace

The same skill is packaged as a Git-backed Codex plugin. Pin the signed plugin
tag, then install the explicit opt-in plugin from the configured marketplace:

```bash
codex plugin marketplace add beepboop2025/liquilens-evidence-carrier \
  --ref plugin-v0.14.0
codex plugin add liquilens-evidence@liquilens
```

The plugin deliberately does not auto-start an MCP server or choose a local
filesystem root. Configure the verifier separately for the directory the user
has actually authorized.

## Browser and notebooks

- [Browser verifier](https://beepboop2025.github.io/liquilens-evidence-carrier/)
  performs verification locally in the browser and does not upload the carrier.
- [Anonymous Binder notebook](https://mybinder.org/v2/gh/beepboop2025/liquilens-evidence-carrier/3d079421c830fcc97ea08da3c54b8429eb5ed542?urlpath=lab/tree/notebooks/evidence_carrier_research.ipynb)
  executes a synthetic verification and tamper-rejection walkthrough.
- [Exact-revision Colab notebook](https://colab.research.google.com/github/beepboop2025/liquilens-evidence-carrier/blob/3d079421c830fcc97ea08da3c54b8429eb5ed542/notebooks/evidence_carrier_research.ipynb)
  loads publicly; attaching a Colab runtime can require a Google session.

## OpenBB

The standalone Router extension is pinned to the protected-main revision whose
public install, `openbb-build`, route registration, and generated Python call
passed on Python 3.11 through 3.14:

```bash
python -m pip install \
  'git+https://github.com/beepboop2025/liquilens-evidence-carrier.git@05a77927496bf22c8bfdb7cbce2d6f43054911d0#subdirectory=integrations/openbb'
openbb-build --force
```

It exposes `obb.liquilens.verify` and `POST /api/v1/liquilens/verify`. It is an
offline verifier for caller-supplied JSON, not a market-data provider.

## Repository-native enforcement

- Pin the GitHub Action with
  `uses: beepboop2025/liquilens-evidence-carrier@v0.14.0`.
- Configure the signed `v0.14.0` hook in `.pre-commit-config.yaml` to verify
  `*.evidence.json` and `*.carrier.json` before a commit lands.
- Use the dbt project directly from a pinned Git revision until its dbt Hub
  submission is accepted.

## Upstream review is not deployment

These submissions are open for external review and are not described as live
upstream listings:

- [conda-forge staged recipe #34616](https://github.com/conda-forge/staged-recipes/pull/34616)
- [SchemaStore catalog #6238](https://github.com/SchemaStore/schemastore/pull/6238)
- [FINOS Labs FDC3 App Directory #40](https://github.com/finos-labs/FDC3-App-Directory/pull/40)
- [dbt HubCap #446](https://github.com/dbt-labs/hubcap/pull/446)
- [Docker MCP Registry: Evidence Carrier #4766](https://github.com/docker/mcp-registry/pull/4766)
- [Docker MCP Registry: Financial Evidence #4765](https://github.com/docker/mcp-registry/pull/4765)
- [Awesome OpenBB #11](https://github.com/OpenBB-finance/awesome-openbb/pull/11)
- [GitHub Awesome Copilot #2785](https://github.com/github/awesome-copilot/pull/2785)

Bloomberg, LSEG, FactSet, S&P Global, Nasdaq, and other proprietary financial
platforms require their own entitlements, commercial validation, rights review,
or operator-controlled publication. A public adapter or outreach message is not
represented as a deployment on those systems.
