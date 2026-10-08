# Back up and verify the disabled private paper host

`deploy/backup_agent_host.py` captures the existing disabled paper installation,
encrypts it, uploads it to private object storage, downloads it again, and
verifies an isolated decrypted snapshot. It makes no broker or source API calls,
initializes no account, enables no trading, and never restores into production.
This procedure requires the [same-state attachment](HOST-ATTACH.md) to have
completed and both retired copilot unit names to remain masked.

## Prepare the private recovery boundary

Qualify the exact source commit and `deploy/test_backup_agent_host.py` on Linux.
Keep the checkout root-owned and immutable. The host needs Python 3, Git, GPG,
rclone and systemd. Record the qualified source and dependency versions before
installation; do not install packages from the backup service itself.

As root, provision these existing, non-symlink paths without replacing any
already provisioned file:

| Path | Owner and mode | Purpose |
| --- | --- | --- |
| `/var/lib/liquilens-paper-backup` | root:root 0700 | Private operation intent, temporary snapshots and status |
| `/var/lib/liquilens-paper-backup/backup.lock` | root:root 0600 | Whole-operation lock, including upload and verification |
| `/etc/liquilens-paper-backup` | root:root 0700 | Private backup configuration |
| `/etc/liquilens-paper-backup/passphrase` | root:root 0600 | Dedicated encryption passphrase; excluded from the snapshot |
| `/etc/liquilens-paper-backup/backup.env` | root:root 0600 | Only `PAPER_BACKUP_BUCKET`, identifying the reviewed private bucket |

The passphrase is one newline-terminated line of 32–4096 bytes. Keep a separate,
secure recovery copy outside this host and outside the encrypted backup. Never
paste it into a command, log, source control or public evidence. Loss of the
passphrase makes the encrypted archive unrecoverable.

The service obtains existing S3 access from the root-only
`/root/.config/anchor/object-storage.env`. It uses the fixed remote name `anchor`
and object prefix `liquilens-paper-host/v1/snapshots/`. Bucket policy and uploaded
objects must provide COMPLIANCE retention of at least 90 days. The helper checks
retention and remotely downloaded bytes; a successful upload alone is insufficient.
It neither changes bucket policy nor deletes remote snapshots.

## Consistent capture and coordinated maintenance

The capture includes account configuration, broker/HMAC credentials, agent
authentication, both SQLite databases, attachment and retirement receipts,
the private MCP wrapper, source issuer configuration, source registry/tokens,
service definitions/drop-ins, retired-unit masks, and immutable Git source
archives. The issuer signing secret is sensitive recovery material and belongs
only inside the encrypted archive. Temporary decrypted content remains root-private.

The helper holds its whole-operation lock, then the source renewal and registry
locks in that order. It records whether the host was active before stopping it,
takes the existing account process lock, and snapshots the databases consistently.
It copies each settled database and any WAL/SHM sidecars to private staging before
opening SQLite. SQLite never opens the original journals, so capture cannot create
root-owned sidecars in the service account's state directory. Committed WAL data
is included in the snapshot.
An unsettled source-token renewal is refused. Never discard pending renewal
tokens, intent files, SQLite sidecars, account history or reserved order identities
to make capture pass.

Operator stops, configuration edits, unit changes, token rotation and future
activation must coordinate through the same existing `backup.lock`. Acquire it
before maintenance and retain it until the change is complete. Backup recovery
may resume only a previously active host whose disabled configuration, STOP
state and unit bytes still match its durable intent. Unexpected drift requires
review. A previously inactive host stays inactive; the helper never enables it.
Before recording a resumed host, recovery requires an authenticated loopback
capabilities response for the same agent, in paper mode with execution disabled,
and a still-active systemd process. A transient `active` state alone is insufficient;
failed startup retains the durable quiescing intent for reviewed recovery.

The installed host must retain `serve --require-disabled`. This backup mechanism
does not qualify an active trading service for automatic stop/restart. Before any
future execution activation, retire this automatic backup/resume arrangement and
design a separately reviewed execution-aware recovery procedure.

## Install and establish acceptance

Render `@RELEASE_DIR@` in the backup and recovery service templates to the exact
qualified absolute checkout. Review and run `systemd-analyze verify` on all three
rendered units before installing them under `/etc/systemd/system` and reloading
systemd. The backup unit reads protected files and needs local systemd control;
its sandbox permits writes only to private backup work, the existing account
process lock, and the two existing source lock files. The original journals and
their directory remain read-only inside the backup service.

Run one explicit backup before enabling recurrence:

```sh
systemctl start liquilens-paper-backup.service
systemctl show liquilens-paper-backup.service --property=Result,ExecMainStatus
```

Read `/var/lib/liquilens-paper-backup/status.json` privately. Match its run ID and
verification time to this invocation; an older success is not acceptance of a
failed new run. Require a verified archive and receipt upload, downloaded-byte
comparison, successful decryption, database checks and isolated snapshot
verification. Confirm the host retained its prior active/inactive state and
remains disabled. Retain only sanitized acceptance fields outside private storage.

After that succeeds, enable the recovery unit for future boots and start the
backup timer. The timer uses six-hour UTC slots, `Persistent=true`, and one-minute
accuracy; enabling it can trigger a catch-up run. Record the first naturally
scheduled successful backup separately from the initial manual run.

The recovery unit is ordered **after** any independently scheduled host startup;
it does not pull in or enable the host. Ordering it before the host would deadlock
if recovery synchronously needed to start that same host. On interrupted capture,
the backup unit's `ExecStopPost` and the boot recovery unit both invoke the same
guarded recovery path. A recovery failure remains a review condition.

## Recover and inspect without production restore

The installed recovery service, or this command using the same qualified source,
reconciles an interrupted operation:

```sh
/usr/bin/python3 -B /absolute/qualified-checkout/integrations/trading-copilot/deploy/backup_agent_host.py recover
```

Do not delete `intent.json` or replace its guards to force a restart. The helper
retains the original running-state decision across recovery. Review any
`last-failure.json` alongside the current intent and latest successful receipt.

An isolated, decrypted snapshot can be checked without starting a host:

```sh
/usr/bin/python3 -B /absolute/qualified-checkout/integrations/trading-copilot/deploy/backup_agent_host.py verify \
  --snapshot /absolute/root-private/isolated-snapshot
```

Keep extraction inside a new root-private directory. Validate safe archive paths,
exact file hashes, database integrity, identity/token bindings and captured
metadata. This verifies the snapshot; it does not execute the archived services,
prove current provider access, observe a broker fill or activate an account.

A production restore is a separate manual procedure with the host stopped,
execution disabled and STOP retained. Establish a single account owner, honor
the original lock and journal identities, restore original ownership/modes and
retired masks, and reconstruct only the pinned source/dependencies. Resolve
expired or revoked source access through its reviewed renewal procedure; never
reinterpret capture-time token validity as current authorization. Verify local
doctor/capabilities and consistent journals before considering a disabled restart.

Encrypted restore verification, a production restore exercise and observed
scheduled recurrence are separate evidence. Shipping these templates establishes
none of those acceptance results by itself.
