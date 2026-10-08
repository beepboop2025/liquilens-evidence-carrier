# Attach the private host to an existing paper installation

An already bound paper installation must keep its existing account, credentials,
HMAC key, limits, lock and audit history. Do **not** run `liquilens-agent-host init`
or copy that account into `/var/lib/liquilens-agent-host`. The attach helper adds
agent authentication to the existing state directory; it never enables trading,
starts a service, initializes a journal, or calls a source or broker.

This migration supports exactly an inactive, disabled
`liquilens.paper-funding-exit.v1` installation with a bound paper account and
complete credentials. Its original private file set must be exactly
`config.json`, `paper.env`, `audit.sqlite3`, and `operator.lock`. The SQLite store
must have no intents, direction reservations, order observations, or prior host
tables. Existing `blocked` and `configuration_blocked` events are preserved.
Any other files or audit history require a separately reviewed migration.

## Prepare and review

Qualify the exact checkout and environment first, including
`deploy/test_attach_agent_host.py`. Retain the source commit and test results.
The helper must run as the existing state owner with the qualified integration
Python, not the system Python. Its local import dependencies are the same locked
dependencies as the installed host.

As root, create `/var/lib/liquilens-agent-attach` mode 0700, owned by the existing
`liquilens-copilot` user and group, on the same filesystem as the state. This is a
private migration-artifact directory containing new agent tokens, **not** a new
account state directory. Keep it out of source control and public evidence.

Set these shell variables to reviewed absolute paths; the release path must
identify an immutable, qualified source commit:

```sh
ATTACH_RELEASE=/opt/liquilens-trading-copilot/releases/REVIEWED_COMMIT
ATTACH_PYTHON="$ATTACH_RELEASE/integrations/trading-copilot/.venv/bin/python"
ATTACH_HELPER="$ATTACH_RELEASE/integrations/trading-copilot/deploy/attach_agent_host.py"
ATTACH_STATE=/var/lib/liquilens-trading-copilot
ATTACH_PLAN=/var/lib/liquilens-agent-attach/REVIEWED_TRANSACTION
```

Read-only eligibility check (including the existing exclusive lock and systemd
unit states):

```sh
runuser -u liquilens-copilot -- "$ATTACH_PYTHON" -B "$ATTACH_HELPER" check \
  --state-dir "$ATTACH_STATE"
```

Prepare private access material without changing account state:

```sh
runuser -u liquilens-copilot -- "$ATTACH_PYTHON" -B "$ATTACH_HELPER" prepare \
  --state-dir "$ATTACH_STATE" --plan-dir "$ATTACH_PLAN"
```

The JSON output contains a manifest SHA-256, never token or account values.
Retain that digest and inspect `manifest.json` privately. It binds all original
file content hashes, device/inode identities, owner/group/mode, size and
modification/change clocks, as well as the exact new token/auth files. Access
times and the state directory's change clocks are excluded because reading and
adding files necessarily change them. State-directory identity and permissions
remain bound. The original four files are never rewritten, chmodded, or replaced.

Before applying, capture existing unit definitions and enabled/active states.
Stop and disable the old copilot timer and service, then retire their unit files
under a retained root-owned receipt and mask both original unit names. Verify
both are inactive with no PID and masked. `Conflicts=` and the process lock add
defense, but do not replace retirement: a timer may otherwise race startup or
be restarted later. The helper independently requires all old execution units
and the host to be inactive and disabled/static/masked. It does not stop them.

## Apply or resume exactly the prepared transaction

After reviewing the manifest, supply its exact digest from preparation:

```sh
runuser -u liquilens-copilot -- "$ATTACH_PYTHON" -B "$ATTACH_HELPER" apply \
  --state-dir "$ATTACH_STATE" --plan-dir "$ATTACH_PLAN" \
  --manifest-sha256 REVIEWED_MANIFEST_SHA256
```

The helper obtains the **existing** exclusive lock without creating it,
revalidates the original state, and atomically publishes each private token file
using exclusive hardlinks. Auth is published last. Every file and containing
directory is fsynced. It never overwrites an existing destination. A destination
is accepted on resume only when its inode, content and ownership match that
transaction's retained file. Identical bytes in an unrelated file are refused.

After an interruption, keep services stopped and rerun the exact `apply` command
with the same plan and digest. Partial publication is resumable and does not
rotate tokens. A successful repeated apply is idempotent **until host startup**;
startup initializes host journals, so it intentionally no longer qualifies as an
unused paper state. Never delete journals or audit rows to rerun this migration.

If preparation failed before completing `manifest.json`, it has not changed the
account state. Retain and inspect that incomplete artifact; use a new transaction
directory for another prepare. Never reinterpret partial material as a complete
plan. If apply reports state or manifest drift, stop and reconcile it; do not
edit the expected digest or delete unexplained destination files.

The retained private plan and state token files share inodes. Do not edit either
copy. Keep the transaction for recovery and protect it like credentials. Future
token rotation must stop the host and replace files in a separately reviewed
operation. Deleting an unused plan link never revokes its published state token.

## Start disabled and verify local access

Run `liquilens-agent-host doctor --state-dir "$ATTACH_STATE"` as the state owner.
Local readiness must pass with `ready_for_order=false` and `enabled=false`.
Compare the original four files with the preparation manifest before startup.
The configured agent identity and HMAC key remain unchanged.

Render `deploy/liquilens-agent-host-attached.service` with `@RELEASE_DIR@` set to
the exact qualified release path, then install the rendered bytes as
`liquilens-agent-host.service`. Review the resulting `ExecStart`, owner and only
writeable account-state path; run `systemd-analyze verify` before installation.
Do not use the new-account template pointing to `/var/lib/liquilens-agent-host`.
The attached template has no `StateDirectory` or `[Install]` directive and binds
only loopback. Record unit bytes and source identity before starting it manually.

Host startup holds the same process lock for its lifetime and initializes
`agent_host_identity`, `agent_assessments` and the adapter submission journal in
the existing state. This expected database migration is separate from attach;
retain a stopped-service backup beforehand. Keep `enabled=false`. An optional
STOP marker may be added **after attach**, before host startup, if the operator
wants that second disablement control.

Verify unauthenticated requests are rejected and the private read token can call
`GET /v1/capabilities`. Then run explicitly authorized source assessments and
retain refusals and source clocks. Tokens belong in private token files, never
URLs, process arguments, logs or prompts. The execution token has additional
scopes but cannot bypass disabled configuration, STOP, source policy or account
limits. A successful capabilities request is local access proof, not a broker
fill, current source admission or permission to activate execution.

Back up the new host database state while stopped. Recovery must use the same
state directory and qualified host source; do not restart the retired strategy
copilot against host-migrated state. Reconcile unknown outcomes using their saved
intent and assessment identifiers rather than creating replacement orders.
