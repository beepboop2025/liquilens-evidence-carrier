# Install and verify local evidence

Choose a channel by the version and capabilities you need. The following
availability was checked on September 5, 2026.

| Route | Available version | Use |
|---|---|---|
| [Signed core release](../README.md#install-and-verify) | Carrier `0.19.0` | Current core CLI, offline MCP server, and Trade Safety verification |
| [conda-forge](https://anaconda.org/conda-forge/liquilens-evidence) | Carrier `0.15.0` | Carrier/reference and four-product Fleet Brief verification in a Conda environment |
| [Dev Container Feature](https://containers.dev/features) | Feature `1.0.0`, installing Carrier `0.14.0` | Carrier verification and projection in a development container |
| [SchemaStore catalog](https://www.schemastore.org/api/json/catalog.json) | Carrier v1 and Reference v1 schemas | JSON editor completion and structural validation |

The package-manager and Feature versions do not include the later Trade Safety
commands. Schema discovery does not install a verifier.

## Conda-forge 0.15.0

Create an isolated environment using the ordinary `conda-forge` channel:

```bash
micromamba create -n liquilens-evidence --override-channels -c conda-forge \
  python=3.13 liquilens-evidence=0.15.0
micromamba run -n liquilens-evidence liquilens-evidence --help
micromamba run -n liquilens-evidence liquilens-evidence-mcp --version
```

A clean macOS arm64/Python 3.13 installation resolved the package from ordinary
[channel repodata](https://conda.anaconda.org/conda-forge/noarch/current_repodata.json)
and passed both CLI entry-point checks. It used
[`liquilens-evidence-0.15.0-pyhc364b38_0.conda`](https://conda.anaconda.org/conda-forge/noarch/liquilens-evidence-0.15.0-pyhc364b38_0.conda),
SHA-256 `5995bb7250dc34dc94715825e82038a1a48441d68950ccfa2f6a7d708f0f7877`.
No explicit `label/main` channel is needed. The
[feedstock](https://github.com/conda-forge/liquilens-evidence-feedstock) maintains
this independent package channel.

For an offline check with no source evidence, issue and verify an empty brief:

```bash
micromamba run -n liquilens-evidence liquilens-evidence issue-brief \
  --as-of 2026-09-05T20:00:00Z > empty-fleet-brief.json
micromamba run -n liquilens-evidence liquilens-evidence verify-brief \
  empty-fleet-brief.json --as-of 2026-09-05T20:00:00Z
```

Each of the four sections remains `missing`; execution, recommendation, and
credit-rating permissions remain false, and financial authority remains
`none`. Verification confirms that this missing-evidence record is intact.
It does not supply evidence or authorize an action. The installed package also
rejected a tampered brief in the recorded smoke check.

## Dev Containers

The [official feature index](https://containers.dev/features) lists the
community-maintained LiquiLens Evidence Carrier Feature `1.0.0`. Its
[installation contract](https://github.com/beepboop2025/liquilens-devcontainer-features/tree/main/src/liquilens-evidence)
pins Carrier `0.14.0` and depends on the Python Feature configured for Python
3.11. Add the published digest to the `features` object in your existing
`.devcontainer/devcontainer.json`:

```json
{
  "features": {
    "ghcr.io/beepboop2025/liquilens-devcontainer-features/liquilens-evidence@sha256:79ac17d7c3f91dc9360c6aa63cb9e4fa0081d5c81e1a1492b2198a8280f5b22d": {}
  }
}
```

After rebuilding the development container, `liquilens-evidence --help` and
`liquilens-evidence-mcp --version` inspect the installed commands. The Feature
checks its pinned wheel checksum before execution. Its offline MCP server reads
explicit JSON paths below the configured root; it does not retrieve market
data. The community listing is not a platform endorsement.

## SchemaStore editor setup

The [SchemaStore catalog](https://www.schemastore.org/api/json/catalog.json)
contains these filename associations:

| Document | Recognized filenames | Schema |
|---|---|---|
| Full carrier | `liquilens-evidence-carrier.json`, `*.liquilens-evidence-carrier.json` | [Carrier v1](https://liquilens.in/protocol/liquilens-evidence-carrier-v1.schema.json) |
| Redacted reference | `liquilens-evidence-carrier-reference.json`, `*.liquilens-evidence-carrier-reference.json` | [Reference v1](https://liquilens.in/protocol/liquilens-evidence-carrier-reference-v1.schema.json) |

Editors that consume SchemaStore can use these associations for completion and
structural validation. For other filenames, configure the editor's schema
association without changing the evidence JSON. Continue to run
`liquilens-evidence verify` to check content identity, clocks, rights, and
authority. Schema validation alone does not establish those properties.

These two catalog entries do not imply a Fleet Brief or Trade Safety
SchemaStore listing. Their canonical contracts remain linked from the
[core documentation](../README.md#canonical-contract-identities).
