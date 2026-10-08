# Run the source observatory continuously

The observer collects Seiche funding, LiquiLens corporate funding and Undertow
hypothetical exit context without opening trading state or loading broker
credentials. Its report explains source admission and policy holds. It never
issues a receipt or authorizes an order. See the README `observe` command for
one-shot JSON/Markdown and the separate optional account readback.

## Separate installation and identity

Use a qualified immutable Carrier commit in
`/opt/liquilens-execution-observer/releases/<40-character-commit>`. Create its
locked environment at that permanent path, with copy link mode:

```sh
uv sync --project integrations/trading-copilot --locked --extra test --link-mode copy
uv run --project integrations/trading-copilot --locked --extra test \
  pytest integrations/trading-copilot/tests integrations/alpaca-paper/tests \
    integrations/trading-copilot/deploy/test_capture_observatory.py
```

Retain the source/tree, unit-file and lockfile hashes and test output before
installing. Keep source and dependencies root-owned, readable/executable by the
service user, and not writable by that user. Point only the observer's `current`
symlink at the qualified release. Preserve the trading copilot's separate
`current`, configuration, keys, HMAC, STOP state, journals and service/timer.

Install the reviewed `deploy/liquilens-execution-observer.service` and `.timer`
templates in `/etc/systemd/system`. Replace the service's source placeholder
with the qualified full commit and its `current` path references with that
exact versioned observer release. Check the rendered unit with
`systemd-analyze verify` before daemon reload. Retain previous unit bytes if
upgrading; do not overwrite another installation's unit without reconciliation.

The dynamic service identity owns only
`/var/lib/liquilens-execution-observer` (mode 0700). The sandbox explicitly hides
the trading copilot and agent-host state directories, as well as home
directories, and supplies no environment/credential file. The collector launches
only `observe --format json`, using a fixed small environment and no account
flags. Its source transport accepts only the three configured public source
operations. A healthy observer cannot enable or repair a trading engine.

## Capture and recurrence

Start the observer once, inspect its report, then enable the observer timer.
Record the first successful scheduled recurrence separately from the manual
run. The timer schedules a new observation fifteen minutes after the prior run
finishes, with up to fifteen seconds of jitter; it never sends notifications.
This is owned verification traffic, not customer usage. The steady-state schedule
uses about 96 MCP calls per day, plus startup/manual checks. Undertow's anonymous
endpoint currently returns a 200-call daily limit shared with other usage from
the same client. Verify the current quota before increasing cadence; do not
rotate identities or retry repeatedly to bypass it. A quota refusal remains an
unavailable source until the provider's allowance resets or changes.

A completed capture can still contain refused sources. `source_clock_in_future`
means an upstream response clock exceeded the observer's trusted evaluation
clock; verify both clocks without granting a tolerance that the execution
profile does not allow. `source_quota_exhausted` identifies a validated MCP quota
error. Neither reason admits source facts or qualifies trading. Monitoring
snapshots do not replace fresh evidence collection for an actual order.

`latest.json` and up to 288 history records contain source clocks, source hashes,
denial reasons and the exact implementation identity. They are private mode 0600.
Always check `capture_status`, `completed_at`, the report evaluation clock and
native expiry when reading; these are dated observations, not a permanent ready
flag. There is no public endpoint in this deployment.

The collector takes its own exclusive lock. Before launching the child it
durably publishes `in_progress` with no report, so a crash cannot leave an old
passing record marked complete for the new attempt. Child output, stderr and
runtime are bounded. Failed collection publishes a failed record with fixed
diagnostics, never raw stderr. Source denials are valid completed observations.

History uses fsynced temporary files and exclusive atomic publication. On restart
the collector removes only owned temporary files in its reserved namespace.
Retention runs before the next history publication. Corrupt or unexpected
retained files require operator review and prevent further history growth;
they are not silently deleted. An interrupted attempt may leave `in_progress`;
the next timer attempt can collect again because no orders or intents exist in
this service.

## Recovery

To stop observation, disable/stop only `liquilens-execution-observer.timer` and
stop an active observer service. The financial engine is unaffected. Preserve
private observation history for incident review. To roll back code, pin the
unit to the previously qualified observer release, verify unit bytes, reload
systemd and run/read back one observation before resuming recurrence. Do not
change trading credentials or reset execution journals as part of recovery.

Provider rights and source freshness remain independently enforced. A weekly
source can be the newest published value and still exceed the execution
profile's age ceiling. Show that denial, preserve its observation date and
recheck after the upstream publication; do not convert a new retrieval into a
new observation or increase thresholds just to make the observer pass.
