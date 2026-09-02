# Controlled version-tag creation

Status at 2026-09-02: active for `refs/tags/v*` in
`beepboop2025/liquilens-evidence-carrier`.

Two independent repository rulesets apply:

- ruleset `21288366`, **Immutable version tags**, blocks update and deletion
  with no bypass actor;
- ruleset `22065439`, **Controlled version tag creation**, blocks creation for
  ordinary users and integrations and permits only the `DeployKey` bypass
  class.

The repository has one write-enabled deploy key: ID `162020600`, titled
**LiquiLens controlled version tag push 2026-09**, with Ed25519 fingerprint
`SHA256:VVabEalJbH7sqkGvGAoeUYvff96jBZXDtPTNETgF57E`. Its reviewed public half
is `liquilens-evidence-carrier.tag_push.pub`; its private half must never enter
Git, a release artifact, a workflow log, or a hosted runtime.

`scripts/create_release_tag.py` is the controlled creation path. Before signing
or pushing, it requires a matching successful protected-main preflight, rechecks
the candidate and current protected-main head, verifies both live rulesets,
confirms the authenticated GitHub account cannot bypass them, confirms that the
recorded deploy key is the repository's only write-enabled deploy key, and
derives the supplied private key's public identity. The annotated tag remains
signed by the separate allowlisted release-signing key.

If the deploy key is lost, disable the creation ruleset only long enough to
install and review a replacement public key and update this receipt through a
protected pull request. Do not add a bypass actor to the immutable update and
deletion ruleset, and do not repoint or recreate a consumed version tag.
