# Native Railway CI

The image runs the checked-in portable commands from `ci.yml`, `alpaca-paper.yml`,
`trading-copilot.yml`, `openbb.yml`, `vscode.yml`, `notebook.yml`, `agent-skill.yml`
and the browser verification job in `pages.yml`. Core Python 3.11–3.13 and OpenBB
Python 3.11–3.14 matrix entries get separate checkouts and seeded environments.
The OpenBB public-install check uses the exact Railway source SHA for candidates
as well as main. GitHub-named variables are only command compatibility inputs;
they do not assert GitHub execution or OIDC identity.

Configure `deploy/railway-ci/Dockerfile`, restart policy `NEVER`, one replica,
no public domain, no application/deployment credentials and no wait for Actions.
The complete gate runs during image construction, so failed checks fail the
Railway build and PR deployment check. The image retains an exact-source pass
receipt; startup verifies it against the runtime source SHA and emits
`RAILWAY_CI_PASS` with source and deployment IDs. Detailed job results remain in
the build logs. A Railway deployment status alone is not test evidence.

Setup actions are explicitly mapped to image dependencies. Unknown actions,
conditions, matrix axes or privilege-bearing job structures fail before any
workflow runs. These commands test synthetic/offline adapters and do not receive
broker credentials or submit paper/live orders.

Native Nix platform checks, Docker sandbox/multi-architecture checks, protected
release preflight, package/container publishing, GitHub attestations and Pages
deployment remain separate. This job does not mint their required OIDC identity
or claim their native-executor proof. Owner pull requests use a focused Railway environment copied from a base
containing only CI services and no credentials or volumes. The seven portable
GitHub workflows retain a fallback for pull requests from other authors. Browser
verification runs here before the exact-source native CI result can pass.
Manual validation uses a Railway redeployment of the desired source.

The Pages issuer workflow follows the successful Railway status event for current
main, or an explicit main dispatch. It checks the authenticated Railway bot, exact
CI context/service/environment and latest result for that commit, then rechecks
before deployment. A changed main, pending/failed result, malformed target or PR
environment cannot reuse an earlier main pass. Pages still uses its original
GitHub origin and OIDC deployment; it does not repeat the portable browser tests.
