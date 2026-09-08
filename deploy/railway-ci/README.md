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
Every successful run emits `RAILWAY_CI_PASS` with the source SHA and deployment
ID. A Railway deployment status alone is not evidence that its tests passed.

Setup actions are explicitly mapped to image dependencies. Unknown actions,
conditions, matrix axes or privilege-bearing job structures fail before any
workflow runs. These commands test synthetic/offline adapters and do not receive
broker credentials or submit paper/live orders.

Native Nix platform checks, Docker sandbox/multi-architecture checks, protected
release preflight, package/container publishing, GitHub attestations and Pages
deployment remain separate. This job does not mint their required OIDC identity
or claim their native-executor proof. PR validation needs an isolated PR-source
trigger before the existing PR workflows can be retired.
