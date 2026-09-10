# Deployment Policy

**Status:** current as of Phase D (D4) of
`docs/superpowers/specs/2026-09-10-unified-admin-identity-design.md`.
For the older platform-by-platform setup guide (Vercel/Railway/Docker
config, env var lists), see `docs/deployment/DEPLOYMENT.md` -- this file is
the policy layer on top of that: **how** deploys are allowed to happen, not
how to configure a platform for the first time.

## Railway: GitHub-only (already the case)

Railway's `api` service deploys only from GitHub pushes to `main` -- there is
no separate policy needed here, this has always been the deploy path for
this backend.

## Vercel: Git-only

**Production deploys to any Vercel project (`gatewayz-frontend`,
`gatewayz-admin`) happen only from git pushes.** `vercel deploy --prod` (or
plain `vercel deploy` against a project whose production branch is checked
out) run from a local checkout is **forbidden**.

### Why

Running `vercel deploy --prod` uploads whatever is on disk in that checkout
right now -- not what's on `main`. In a shared working environment (multiple
agents/developers on the same machine, or just an uncommitted local edit),
that means production can end up running someone else's in-progress branch,
or a stale checkout. This already happened once: two local `vercel deploy
--prod` runs put the wrong builds on `admin.gatewayz.ai`, and deleting one
of them detached the production alias entirely (~5 minutes of
`DEPLOYMENT_NOT_FOUND` for the whole admin panel). See the
`Gatewayz - Admin Panel Accounts & Service-Role Leak (Sep 10, 2026)` vault
note for the full incident.

### The rule

- **To ship a change:** commit it, push to the branch Vercel has configured
  as that project's production branch (normally `main`), and let Vercel's
  GitHub integration build and deploy it. This is identical in spirit to
  Railway's existing GitHub-only path above.
- **To rebuild an existing deployment** (e.g. to pick up a new env var
  without a code change, or to retry a flaky build): use
  `vercel redeploy <deployment-url>`, which rebuilds a deployment Vercel
  already knows about from git -- it does not upload a local working tree.
- **`vercel deploy` / `vercel deploy --prod` from a local checkout is not
  to be run against these projects**, for any reason, by a human or an
  agent.
- If a deployment alias is ever accidentally pointed at the wrong build,
  recover with `vercel alias set <git-built-deployment-url> <alias>` and
  `vercel cache purge` -- do **not** "fix" it with another local
  `vercel deploy --prod`.
- Vercel's deployment-protection SSO redirect means a direct
  `*.vercel.app` URL will 302 to a login page -- always test through the
  production alias (`admin.gatewayz.ai`, `beta.gatewayz.ai`, etc.), not the
  raw deployment URL, or a protection redirect can look like an outage.

### Is there a Vercel setting that enforces this technically?

Not a clean one. Vercel's `vercel.json` `git.deploymentEnabled` /
`github.enable` settings control whether **git-triggered** deployments
happen at all (the opposite direction from what we want -- we want git
deploys to keep working, and only CLI-from-local-checkout deploys blocked).
There is no first-party Vercel project setting that says "accept
deployments from git pushes only, reject the CLI/API for the same target."
Team-level RBAC also does not distinguish "may deploy via git" from "may
deploy via CLI" -- anyone with project access and a valid `vercel` CLI
login can run `vercel deploy --prod` today.

Given that, **this is enforced as team policy, not a platform control**:

- This document is the source of truth for the rule.
- `docs/security/SECRET_ROTATION.md`'s "Never" list repeats it at the point
  where it's most likely to be violated (mid-rotation, wanting to "just
  push the env var change now").
- If a future Vercel release adds a real per-project toggle for this
  (e.g. restricting deployment creation to the GitHub integration), revisit
  this section and prefer the platform control over the policy-only one.

## What's still open

- No automated check currently blocks a local `vercel deploy --prod` before
  it runs -- this is a process rule enforced by documentation and review,
  not tooling. A pre-flight wrapper script (refusing to run `vercel deploy
  --prod` outside CI) would close this gap if the manual rule proves
  insufficient.
