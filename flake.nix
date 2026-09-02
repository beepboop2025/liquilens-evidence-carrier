{
  description = "Offline LiquiLens evidence carrier verifier and MCP server";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-darwin"
        "x86_64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      packageFor =
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          version = pkgs.lib.removeSuffix "\n" (builtins.readFile ./VERSION);
        in
        pkgs.python3Packages.buildPythonApplication {
          pname = "liquilens-evidence";
          inherit version;
          pyproject = true;
          src = self;

          # Nixpkgs pins the build backend through flake.lock. The upstream
          # exact pin is retained for pip/uv release builds and relaxed only
          # inside this hermetic Nix derivation.
          postPatch = ''
            substituteInPlace pyproject.toml \
              --replace-fail 'requires = ["setuptools==84.0.0"]' \
              'requires = ["setuptools"]'
          '';
          build-system = [ pkgs.python3Packages.setuptools ];

          nativeCheckInputs = with pkgs.python3Packages; [
            jsonschema
            pytestCheckHook
          ];
          pythonImportsCheck = [ "liquilens_evidence" ];

          meta = {
            description = "Portable, fail-closed financial evidence provenance";
            homepage = "https://liquilens.in/protocol/";
            changelog = "https://github.com/beepboop2025/liquilens-evidence-carrier/blob/main/CHANGELOG.md";
            license = pkgs.lib.licenses.asl20;
            mainProgram = "liquilens-evidence";
            platforms = systems;
          };
        };
    in
    {
      packages = forAllSystems (
        system:
        let
          package = packageFor system;
        in
        {
          default = package;
          liquilens-evidence = package;
        }
      );

      apps = forAllSystems (
        system:
        let
          package = packageFor system;
        in
        {
          default = {
            type = "app";
            program = "${package}/bin/liquilens-evidence";
          };
          liquilens-evidence = {
            type = "app";
            program = "${package}/bin/liquilens-evidence";
          };
          mcp = {
            type = "app";
            program = "${package}/bin/liquilens-evidence-mcp";
          };
          liquilens-evidence-mcp = {
            type = "app";
            program = "${package}/bin/liquilens-evidence-mcp";
          };
        }
      );

      checks = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          package = packageFor system;
        in
        {
          inherit package;
          cli-and-mcp = pkgs.runCommand "liquilens-evidence-cli-and-mcp-check" {
            nativeBuildInputs = [ package ];
          } ''
            export HOME="$TMPDIR/home"
            mkdir -p "$HOME" "$TMPDIR/evidence"

            liquilens-evidence issue ${self}/examples/descriptor.json \
              > "$TMPDIR/evidence/carrier.json"
            liquilens-evidence verify "$TMPDIR/evidence/carrier.json" \
              --as-of 2026-08-24T12:00:00Z \
              > "$TMPDIR/verification.json"
            grep --fixed-strings '"ok": true' "$TMPDIR/verification.json"
            liquilens-evidence issue-brief \
              --liquilens "$TMPDIR/evidence/carrier.json" \
              --as-of 2026-08-24T12:00:00Z \
              > "$TMPDIR/evidence/fleet-brief.json"
            liquilens-evidence verify-brief "$TMPDIR/evidence/fleet-brief.json" \
              --as-of 2026-08-24T12:00:00Z \
              > "$TMPDIR/brief-verification.json"
            grep --fixed-strings '"seiche": "missing"' \
              "$TMPDIR/brief-verification.json"

            printf '%s\n' \
              '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{},"io.modelcontextprotocol/clientInfo":{"name":"nix-flake-check","version":"0.15.0"}}}}' \
              | liquilens-evidence-mcp --root "$TMPDIR/evidence" \
              > "$TMPDIR/mcp-response.json"
            grep --fixed-strings '"verify_carrier"' "$TMPDIR/mcp-response.json"
            grep --fixed-strings '"project_carrier"' "$TMPDIR/mcp-response.json"
            grep --fixed-strings '"verify_fleet_brief"' "$TMPDIR/mcp-response.json"
            grep --fixed-strings '"verify_trade_safety_receipt"' \
              "$TMPDIR/mcp-response.json"

            touch "$out"
          '';
        }
      );
    };
}
