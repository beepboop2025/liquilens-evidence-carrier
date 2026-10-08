# Upgrade an inactive private installation

This procedure installs qualified source and a locked Python environment. It
does not provision an account, edit keys, admit evidence, start a service, enable
a timer, or establish a broker fill. Use a separate explicitly approved procedure
for activation. The live connector is not a system service in this deployment.

The existing layout is `/opt/liquilens-trading-copilot/releases/<commit>` with an
atomic `current` symlink and durable `/var/lib/liquilens-trading-copilot` state.
Retain the old release, configuration, HMAC, STOP files and all journals.

## 1. Qualify and capture

Choose the complete 40-character commit that passed the hosted native checks and
local execution tests. Record its tree, lockfile hashes and qualification URLs
in the release evidence. Do not substitute a moving `main` or a package version.

Run the reviewed `deploy/disabled_rollout.py` with the host's system Python, not
an executable from the release being replaced. First inspect its bytes against
the qualified source and put the copy in a root-owned operator directory. These
commands are for a root shell on the host, during an exclusive maintenance window:

```sh
umask 077
export CARRIER_COMMIT=REPLACE_WITH_QUALIFIED_40_CHARACTER_COMMIT
export CARRIER_RELEASE=/opt/liquilens-trading-copilot/releases/$CARRIER_COMMIT
export CARRIER_RECEIPTS=/opt/liquilens-trading-copilot/receipts/$CARRIER_COMMIT
test ! -e "$CARRIER_RECEIPTS"
install -d -m 700 /opt/liquilens-trading-copilot/receipts
mkdir -m 700 "$CARRIER_RECEIPTS"
python3 /root/reviewed-disabled-rollout.py capture \
  --receipt "$CARRIER_RECEIPTS/before.json"
```

The guard requires inactive paper service/timer, a disabled timer, disabled
configuration, no account ID, blank paper API/secret keys and an existing HMAC.
It fingerprints the complete existing release, private state contents and file
ownership/modes, current symlink, loaded unit identities and unit/drop-in bytes.
The optional agent-host unit must also be inactive and not enabled. Receipts
contain hashes, never secret values; keep them mode 0600. If a check fails,
investigate the specific fixed diagnostic code. Do not silently recapture a
changed installation to force a switch.

## 2. Stage exact source and locked dependencies

Run each command only after the previous command succeeds. Use the host's
already approved Python 3.11–3.14 installation. Dependency download
is the only network activity in installation, apart from retrieving source;
no broker configuration is passed to a build or test process.

```sh
test ! -e "$CARRIER_RELEASE"
git -c core.hooksPath=/dev/null clone --no-checkout --filter=blob:none \
  --single-branch --branch main \
  https://github.com/beepboop2025/liquilens-evidence-carrier.git "$CARRIER_RELEASE"
git -C "$CARRIER_RELEASE" -c core.hooksPath=/dev/null \
  checkout --detach "$CARRIER_COMMIT"
test "$(git -C "$CARRIER_RELEASE" rev-parse HEAD)" = "$CARRIER_COMMIT"
test "$(uname -m)" = x86_64
python3 -m venv "$CARRIER_RECEIPTS/bootstrap"
"$CARRIER_RECEIPTS/bootstrap/bin/python" -m pip install \
  --disable-pip-version-check --only-binary=:all: --no-deps --require-hashes \
  -r "$CARRIER_RELEASE/integrations/trading-copilot/deploy/uv-bootstrap-requirements.txt"
export CARRIER_UV="$CARRIER_RECEIPTS/bootstrap/bin/uv"
"$CARRIER_UV" --version
"$CARRIER_UV" sync --project "$CARRIER_RELEASE/integrations/trading-copilot" \
  --locked --extra test --python /usr/bin/python3 --link-mode copy
chmod -R go+rX,go-w "$CARRIER_RELEASE"
```

The isolated bootstrap pins uv 0.12.5, matching CI, and the SHA-256 of its Linux
glibc x86_64 wheel from [PyPI metadata](https://pypi.org/pypi/uv/0.12.5/json).
It never installs into the host's system Python or depends on an ambient `uv`
command. The bootstrap directory belongs to root and stays outside
the service's source/state directories. If the host lacks `venv`/pip or has a
different architecture, stop and prepare an equivalent reviewed tool artifact;
do not run a remote installer or change global packages as part of this upgrade.
Explicit copy mode prevents the permission adjustment from changing cache or
older-release files through shared hardlinks. Preserve it on subsequent uv runs
that might synchronize the environment.

Use a permanent versioned directory before creating the virtual environment:
console-script shebangs and editable sibling-package paths reference that exact
directory. Never build in a temporary path and rename the environment afterward.
Do not initialize anything in the existing production state directory.

## 3. Validate offline without account state

Tests use synthetic source responses and broker doubles. Initialization below
uses a separate temporary directory and never changes production configuration.
The capability command does not contact a broker. The service-user smoke is
mandatory: it confirms that the actual systemd account can read and execute the
new environment. Retain full test output outside the source checkout.

```sh
cd "$CARRIER_RELEASE"
"$CARRIER_UV" run --project integrations/trading-copilot --locked --extra test \
  --link-mode copy \
  pytest integrations/trading-copilot/tests integrations/alpaca-paper/tests
python3 integrations/trading-copilot/deploy/test_disabled_rollout.py
chmod -R go+rX,go-w "$CARRIER_RELEASE"
export CARRIER_SMOKE=$(mktemp -d /tmp/liquilens-disabled-install.XXXXXX)
chown liquilens-copilot:liquilens-copilot "$CARRIER_SMOKE"
runuser -u liquilens-copilot -- test ! -w "$CARRIER_RELEASE"
runuser -u liquilens-copilot -- env PYTHONDONTWRITEBYTECODE=1 \
  "$CARRIER_RELEASE/integrations/trading-copilot/.venv/bin/liquilens-agent-host" init \
  --state-dir "$CARRIER_SMOKE/agent"
runuser -u liquilens-copilot -- env PYTHONDONTWRITEBYTECODE=1 \
  "$CARRIER_RELEASE/integrations/trading-copilot/.venv/bin/liquilens-live" init \
  --state-dir "$CARRIER_SMOKE/live"
runuser -u liquilens-copilot -- env PYTHONDONTWRITEBYTECODE=1 \
  "$CARRIER_RELEASE/integrations/trading-copilot/.venv/bin/liquilens-live" capabilities \
  --state-dir "$CARRIER_SMOKE/live"
```

Check the temporary config files show disabled initialization and blank account
credentials without printing secrets. Retain only sanitized results. The private
agent doctor's exit 2 for missing account credentials is expected for an empty
installation; it is not a reason to fill keys or change enablement.

## 4. Switch while preserving inactive state

```sh
python3 /root/reviewed-disabled-rollout.py check \
  --receipt "$CARRIER_RECEIPTS/before.json"
python3 /root/reviewed-disabled-rollout.py switch \
  --receipt "$CARRIER_RECEIPTS/before.json" --source-sha "$CARRIER_COMMIT" \
  --result "$CARRIER_RECEIPTS/switched.json"
```

The guard checks exact detached source identity, a clean source checkout and
the installed CLI, rechecks the complete baseline immediately before replacement,
then switches `current` atomically and fsyncs its directory. It verifies private
state and unit fingerprints again afterward. It does not call `start`, `enable`,
`restart`, `daemon-reload`, order commands or initialization against real state.

The lock serializes this installer only. Other administrators must honor the
maintenance window: no filesystem compare can prevent an unrelated root writer
changing a file after its final check. An unexpected post-switch difference
causes failure without automatic rollback. The prewritten `.intent` receipt
retains the old and intended target identities for investigation. Determine the
actual symlink and state before another action; do not replay blindly.

## 5. Roll back without resetting state

For a successful installation with no intervening changes:

```sh
python3 /root/reviewed-disabled-rollout.py rollback \
  --receipt "$CARRIER_RECEIPTS/switched.json" \
  --result "$CARRIER_RECEIPTS/rolled-back.json"
```

Rollback verifies the former release bytes and requires the entire post-switch
snapshot to remain unchanged. It atomically restores the old source symlink;
configuration, secrets and journals remain intact. Any state or service change
requires review. This is not a database downgrade/migration procedure.

## Optional private agent-host unit

`deploy/liquilens-agent-host.service` is a separately reviewed template. It uses
the existing unprivileged service user, loopback port 8766, a distinct private
`/var/lib/liquilens-agent-host` directory and explicit conflicts with the paper
copilot/timer. It has no install target and no restart policy. Merely shipping
the template does not install, enable or start the service.

Initialize a distinct installation only after choosing its owner, account and
single-runner boundaries. Do not point two state directories at one broker
account. Do not copy the paper copilot's configuration/keys into the agent host
as an upgrade step. Unit installation and any daemon reload change the captured
unit fingerprint, so they belong in a separately reviewed operation, not between
capture and switch. Remote exposure additionally needs private network admission
and an explicitly configured TLS proxy; the template does not provide either.
