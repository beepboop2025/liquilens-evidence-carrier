# Run LiquiLens Evidence Carrier with Nix

The repository is a directly runnable flake for the v0.14.0 verifier and
offline stdio MCP server. It requires no Python environment or package-registry
account.

Run the verifier from the current public default branch:

```bash
nix run github:beepboop2025/liquilens-evidence-carrier -- --help
nix run github:beepboop2025/liquilens-evidence-carrier -- verify carrier.json
```

Run the MCP server through the dedicated app:

```bash
nix run github:beepboop2025/liquilens-evidence-carrier#mcp -- \
  --root /absolute/path/to/evidence
```

Use the exact flake source that introduced the pinned v0.14.0 build for an
immutable invocation:

```bash
nix run \
  github:beepboop2025/liquilens-evidence-carrier/225c3e2fc96efb0ca78256e3ec96ac25901c10c7 \
  -- verify carrier.json
```

The committed `flake.lock` pins the NixOS 26.05 package set. The flake exposes
native outputs for `x86_64-linux`, `aarch64-linux`, `x86_64-darwin`, and
`aarch64-darwin`:

- `packages.<system>.default` and `packages.<system>.liquilens-evidence`;
- `apps.<system>.default` and `apps.<system>.liquilens-evidence` for the CLI;
- `apps.<system>.mcp` and `apps.<system>.liquilens-evidence-mcp` for stdio MCP;
  and
- `checks.<system>.package` and `checks.<system>.cli-and-mcp` for native builds
  and behavior.

Nix may fetch public build inputs before launch. The resulting verifier and MCP
server themselves retain the product boundary: zero third-party Python runtime
dependencies, no network access, read-only local evidence inspection, and no
financial authority, recommendation, rating, or execution capability.
