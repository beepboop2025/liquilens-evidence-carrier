# Native x86_64 Linux Nix CI

This credential-free Railway service runs the x86_64-linux lane from
`.github/workflows/nix.yml`: architecture assertion, `nix flake check`, clean
committed lock, both local apps, and both exact public GitHub flake references
on main. It clones the exact native deployment source and requires current main
to be its ancestor; a later main movement invalidates the result.

Build-stage failure controls the native Railway commit status. Runtime only
checks the saved successful source receipt and emits `RAILWAY_NIX_LINUX_PASS`.
Use one replica, no public route, restart `NEVER`, and no service or shared
credentials. Source belongs to the isolated public PR base, never a publishing
service. The other three architecture checks and external PR fallback remain.

The official Nix 2.35.2 image is pinned by digest. Its documented container
mode disables Nix's inner sandbox; Railway's build container supplies the
process/filesystem boundary. This is stated explicitly in the proof log and is
not a claim of Nix derivation sandbox parity with the GitHub VM. No host setting,
privileged container, architecture emulation or interactive Mac installation is
used. Inputs remain pinned by the committed `flake.lock` and cache signatures.

Official container behavior: https://hub.docker.com/r/nixos/nix
