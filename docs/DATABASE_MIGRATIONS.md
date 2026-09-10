# Database Migrations

**Status:** rewritten in Phase D (D3) of
`docs/superpowers/specs/2026-09-10-unified-admin-identity-design.md` to
match what `.github/workflows/supabase-migrations.yml` actually does today
-- the previous version of this doc described a staging/production
branch-split workflow with manual environment selection that no longer
exists in this repo. Related:
`docs/security/DATA_ACCESS.md`, `supabase/staged-migrations/README.md`.

## The one supported path: merge to `main`

There is exactly one supported way for a migration to reach production:

1. Write the migration as `supabase/migrations/<YYYYMMDDHHMMSS>_<description>.sql`.
2. Open a PR. On push, the `validate` job in
   `.github/workflows/supabase-migrations.yml` checks the filename format
   and scans for destructive operations (`DROP DATABASE`, `DROP SCHEMA
   public`, `TRUNCATE ... CASCADE`) -- these fail the PR outright and must
   be fixed or deliberately restructured, not bypassed.
3. Merge to `main`. The `sync` job runs automatically:
   - Links the Supabase project (`SUPABASE_PROJECT_REF` /
     `SUPABASE_DB_PASSWORD` / `SUPABASE_ACCESS_TOKEN` secrets).
   - Runs `supabase migration list` to check for **drift** (remote has a
     migration version the repo doesn't) and **pending** migrations (repo
     has a version the remote doesn't).
   - If drift is detected, it runs `supabase db pull` and opens a PR with
     the pulled SQL instead of pushing anything -- **review that SQL
     carefully**, it means someone changed the schema outside this
     workflow (see "Emergency hand-apply" below).
   - If there's no drift and there are pending migrations, it runs
     `supabase db push --include-all`, which applies every migration file
     the remote doesn't have a record of yet, then re-runs `supabase
     migration list` to confirm.

There is **no separate staging branch or environment-selection input** in
the current workflow -- it triggers only on pushes to `main` that touch
`supabase/migrations/*.sql`, plus a 6-hourly scheduled drift check and a
manual `workflow_dispatch` (with a `dry_run` toggle) for validation-only
runs. `gh run list --workflow supabase-migrations.yml` shows recent runs;
`gh run view <id> --log` shows exactly what was applied.

**This is the only path.** Don't add a new CI trigger or hand-roll a
different apply mechanism without updating this doc.

## The staged-migrations gate

`supabase/staged-migrations/` (see its own `README.md`) holds migrations
that are **correct SQL but not yet safe to auto-apply on merge** -- usually
because they drop a column/table that a very recent code change stopped
writing to, and the team wants that code change to soak in production
first. Files here are *not* under `supabase/migrations/`, so the CI
workflow above never touches them; nothing in `staged-migrations/` is
applied automatically by anything.

To apply one:

1. Confirm the code change it depends on has been deployed and has soaked
   (no fixed time requirement -- judgment call per migration; the `drop_*`
   ones are irreversible, so err slow).
2. Move the file from `supabase/staged-migrations/` to
   `supabase/migrations/`, adding a header note recording when/how it was
   actually applied if it was already run by hand (see below) -- **always
   commit the move as a PR**, even if the SQL was already run against
   production. `supabase db push` skips migrations the remote already has a
   record of, so re-adding an already-applied file to `supabase/migrations/`
   is safe as long as the file is idempotent (`DROP COLUMN IF EXISTS`,
   `CREATE TABLE IF NOT EXISTS`, `ON CONFLICT ... DO NOTHING`, etc.) --
   `supabase db push --include-all` may re-run it if its version isn't in
   the remote's migration-history table yet (this happens whenever a
   migration was hand-applied via the Management API instead of through the
   CLI/CI path -- see below).
3. Merge like any other migration PR; the normal `sync` job takes it from
   there.

Example: `supabase/migrations/20260903100000_drop_usage_records_api_key.sql`
was promoted this way in Phase D -- see its header comment for the exact
history (staged 2026-09-03, hand-applied 2026-09-10, promoted to
`migrations/` 2026-09-11).

## Emergency hand-apply

Sometimes a migration needs to land **before** a PR can be reviewed and
merged (an active incident, or -- as happened on 2026-09-10 -- a
`.gitignore` pattern silently excluding a migration file from a PR that had
already been reviewed and needed to ship). The only supported emergency
path is the **Supabase Management API**:

```
POST https://api.supabase.com/v1/projects/<project-ref>/database/query
Authorization: Bearer <personal access token>
```

with the migration's SQL as the request body. This runs immediately against
production, bypassing CI entirely.

**The rule: never apply a migration by hand without also committing the
file, in the same incident, before you consider the emergency closed.**
Concretely:

1. Apply the SQL via the Management API.
2. **Immediately** commit the exact SQL you ran to
   `supabase/migrations/<timestamp>_<description>.sql` (or promote it from
   `staged-migrations/` if that's where it came from), with a header
   comment noting it was hand-applied and the date. Open the PR even if the
   incident is already resolved -- an applied-but-uncommitted migration is
   schema drift waiting to be silently overwritten or re-run oddly, and
   makes `supabase migration list` disagree with the repo on the very next
   sync.
3. Expect the scheduled drift check (every 6 hours) to catch an
   uncommitted hand-apply and open an auto-generated "sync schema drift"
   PR if you miss step 2 -- treat that as a safety net, not the primary
   process. **Do not rely on it**; it means someone else now has to
   reconstruct what happened from a raw `db pull` diff instead of your own
   commit message and context.
4. If the migration wasn't tracked by the CLI's migration-history table
   (typical for a Management-API hand-apply), the next `supabase db push`
   will see it as "pending" and try to re-run it -- this is fine *only if
   the file is idempotent*. Write every migration, hand-applied or not, as
   if it might run twice.

This is exactly what happened with
`supabase/migrations/20260911000000_unified_identity_roles.sql` and
`20260911000001_audit_log_and_staff.sql`: both were applied by hand via the
Management API on 2026-09-10, then re-applied by `supabase db push` in CI
after merge (run `34534917992`, confirmed via
`gh run view 34534917992 --log | grep -iE 'push|applied|Applying|already'`)
because the CLI's migration-history table had no record of the hand-apply.
Both files are idempotent (`role_permissions` insert uses
`ON CONFLICT (role, resource, action) DO NOTHING` against a `UNIQUE(role,
resource, action)` constraint that has existed since
`20251009060000_add_user_roles.sql`; the rest is
`CREATE TABLE IF NOT EXISTS`/`ADD COLUMN IF NOT EXISTS`), so the re-run was
a safe no-op -- **no duplicate `role_permissions` rows resulted, and no
follow-up dedupe migration is needed.** If a future migration's INSERT
lacks an `ON CONFLICT` clause backed by a real unique constraint, add both
before merging, not after a duplicate shows up in production.

## Never

- **Never apply a migration by hand without also committing the file**
  (see "Emergency hand-apply" above) -- this is the rule this whole
  document exists to enforce.
- **Never modify an already-merged migration file.** Write a new one.
- **Never write a migration that isn't idempotent** (`CREATE ... IF NOT
  EXISTS`, `DROP ... IF EXISTS`, `INSERT ... ON CONFLICT ... DO NOTHING`
  backed by a real unique constraint) -- `supabase db push` may legitimately
  re-run any file the remote's migration-history table doesn't recognize,
  which is exactly what happens after every hand-apply.
- **Never bypass the `validate` job's destructive-operation check** by
  restructuring SQL specifically to dodge the grep pattern -- if a migration
  genuinely needs `DROP SCHEMA public` or similar, that's a sign it needs
  manual, reviewed, out-of-band execution, not a workflow change.
