# Account and privacy operations

These commands operate on the configured environment. Keep production accounts,
registration, and child collection disabled until the actual notices, consent
method, operating procedures, and provider agreements have been reviewed. Use
synthetic families in development. This runbook is not a compliance certification.

With `ACCOUNTS_ENABLED=false`, guest practice remains available without saved
progress. It uses no account database, but audio still goes through the server to
Azure Speech for transient processing. The application does not save guest audio,
transcripts, results, or history. Real child testing still needs review. When
accounts are enabled, incomplete configuration blocks practice instead of
falling back to guest mode; account consent and collection controls apply.

## Database setup and configuration

Run commands from `src/api`. Install the locked dependencies:

```powershell
python -m uv sync --locked
```

Use a separate PostgreSQL database and Cognito pool for development and production.
SQLite is accepted only by isolated test fixtures, never by the application.
Configure settings using the reviewed deployment runbook and a protected local
environment file. Never put real credentials in example files or Git commits.

The web process uses `DATABASE_URL`. With `DATABASE_IAM_AUTH=true`, connections
request a fresh RDS IAM token using the current AWS role and enforce TLS hostname
and CA verification using `DATABASE_SSL_ROOT_CERT`. The database URL contains the
runtime database username, not an IAM token or administrative password. Use
`MIGRATION_DATABASE_URL` with the separate migration role for schema changes:

```powershell
python -m uv run --locked alembic upgrade head
```

Schema changes never run automatically when the website starts. See
[the AWS account stack](../infra/accounts/README.md) for network, role, and initial
database privilege setup. Ensure runtime and migration permissions remain separate.

Required authentication settings are `PUBLIC_ORIGIN`, `COGNITO_REGION` or
`AWS_REGION`, `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID`, `COGNITO_CLIENT_SECRET`,
`COGNITO_DOMAIN`, and a cryptographically random `SESSION_SECRET` of at least 32
characters. Register exactly `PUBLIC_ORIGIN/api/auth/callback` as the callback and
`PUBLIC_ORIGIN/` as the logout destination. Use Cognito managed login with support
for `prompt=login`. The callback requires an ID token representing interactive
authentication within the previous five minutes; a stale hosted SSO session is
insufficient.

Production cookies require HTTPS. Only local `localhost`, `127.0.0.1`, or `::1`
origins can opt into HTTP using `ALLOW_INSECURE_LOCALHOST=true`; this cannot disable
Secure cookies for a remote host. Parent sessions expire after eight hours by
default. Export, child deletion, withdrawal, and account deletion require a login
within the last 15 minutes. Sign out and sign in again if the privacy action asks
for fresh authentication. Logout revokes the app session and redirects to Cognito
logout to clear the hosted login session.

## Check service readiness without opening registration

After provisioning and migration, run this inside the deployed app container
using its usual configuration and runtime AWS role:

```sh
docker compose exec app .venv/bin/python -m app.account_readiness
```

Or, from `src/api`, use `python -m uv run --locked python -m app.account_readiness`.
The image installs dependencies in `/app/src/api/.venv`, so container commands
must use `.venv/bin/python`. Run Docker Compose commands from the repository root.
The command reads
connection and schema metadata only. It checks PostgreSQL connectivity, verified
TLS, restricted runtime privileges, required table permissions, the current
Alembic revision, and Cognito's public discovery document and signing keys. It
does not read family records, run migrations, send email, or change feature flags.
Exit code 0 means those runtime checks passed; any failed check returns 1.
Disabled account, registration, and child-collection flags are expected and do
not fail the check.

Verify Cognito's private client settings separately on an authorized operator
terminal with the same application configuration:

```sh
python -m uv run --locked python -m app.account_readiness --cognito-client
```

This mode skips PostgreSQL and needs scoped `cognito-idp:DescribeUserPoolClient`
and `cognito-idp:DescribeUserPoolDomain` permissions. Keep these operator
permissions separate from the website role. It verifies the configured client
secret in memory, exact callback and logout destinations, code flow and scopes,
and managed login version 2. Output contains only status fields and booleans;
secrets, connection URLs, signing keys, and exception details are never printed.
The default check explicitly reports that this private-client check is separate.
Both modes must pass before activation. They do not replace an interactive login
test, deletion/restore tests, or the required review before real users.

## Review parental consent

A parent's checkbox and consent-review request create a pending record only.
In account mode, no child profile or practice recording is accepted while consent is pending.
There is no HTTP endpoint or development-login shortcut to approve consent.

1. Receive the consent-review notification. On an authorized terminal, inspect
   its reference to locate the parent and notice version:

   ```powershell
   python -m uv run --locked python -m app.account_cli inspect-request --reference REQUEST_UUID
   ```

2. Follow the legally reviewed parent direct-notice and signed-consent process.
   Verify the parent using that process. Keep the signed evidence in the approved
   restricted evidence store, with its own deletion and retention procedure.
   Index that store by the random parent UUID so an operator can still locate and
   delete the evidence after the SQL consent reference has been purged. Do not put
   signed documents or personal details into the deletion ledger.

3. Record the reviewed result using the parent ID from the request:

   ```powershell
   python -m uv run --locked python -m app.account_cli approve-consent --parent-id PARENT_UUID --notice-version REVIEWED_VERSION --evidence-reference restricted:record-123 --operator-id reviewer-id --reviewed-signed-consent
   ```

The evidence reference must identify the restricted record, not include a child
name, document, identity scan, or public download URL. The command rejects missing
pending requests, a different notice version, or unresolved deletion work. It does
not change `CHILD_DATA_COLLECTION_ENABLED`; only explicitly reviewed deployment
configuration can open collection. A new `PRIVACY_NOTICE_VERSION` blocks account-based practice
until each parent has consent reviewed for that version.

## Run deletion, notifications, and retention

Run the worker every minute and the retention command daily using the deployment
scheduler. Both commands execute one pass and exit:

```powershell
python -m uv run --locked python -m app.account_cli worker
python -m uv run --locked python -m app.account_cli retention
python -m uv run --locked python -m app.account_cli status
```

For the deployed container, use its virtual-environment interpreter. For example,
run this from the repository root to inspect queue counts:

```sh
docker compose exec app .venv/bin/python -m app.account_cli status
```

Deletion immediately denies access to the target child. Withdrawal blocks the
family's account-based practice and revokes parent sessions; the parent can sign in later to manage
the account. Account deletion also disables the entire parent account. The worker
cascades child data deletion. For full account deletion it commits removal of
children, progress, consent, login sessions, parent email, and queued contact data
before asking Cognito to delete the login identity. A Cognito outage therefore
leaves only the disabled identity mapping and retry record, not child history.

Work is retried in fair attempt-count order so an older failing AWS operation does
not starve database-only child deletions. A remote timeout after a successful
Cognito deletion is safe to retry. Each job records its purge and completion times
and an error class only. Email failure never rolls back successful deletion.

Configure a verified SES sender using `PRIVACY_NOTIFICATION_FROM`. Both
`PRIVACY_NOTIFICATION_EMAILS` and the displayed `PRIVACY_CONTACT` default to
`gina_underwood@yahoo.com,jay.e.underwood@gmail.com`. Notifications contain event
type, request reference, and parent contact when still retained. They contain no
child nickname, recording, transcript, or scores. Pending contact is removed after
seven days, delivered contact immediately, and sent queue entries after 30 days.
Mail delivery is at least once: a crash after SES accepts a message can cause a
duplicate notification on retry. Request references allow deduplication.

The retention pass:

- Deletes practice sessions and their attempts after `PROGRESS_RETENTION_DAYS`
  (90 days by default), and expires old login sessions.
- Queues deletion of never-verified registrations 30 days after creation even if
  the parent repeatedly signs in; verified accounts expire after 365 inactive days.
- Deletes abandoned/unconfirmed Cognito accounts with no matching app parent after
  30 days. This requires scoped Cognito `ListUsers` and `AdminDeleteUser` permissions.
- Expires withdrawn/superseded consent, completed jobs, and deletion-ledger entries
  according to their bounded periods.

Monitor worker/retention exit status, `status` queue counts, failure counts, oldest
pending requests, and notification delivery. A pending or failed request is not a
completed erasure. External consent evidence, operator inboxes, downloaded exports,
provider records, and separately managed backups still require documented action.

## Backup restore and deletion replay

Database backups are separate copies of personal data. The app cannot selectively
erase a row inside an RDS snapshot. Enforce the inventory and expiry of automated,
manual, final, and copied snapshots. `BACKUP_MAX_RETENTION_DAYS` defaults to 35;
`DELETION_LEDGER_DAYS` defaults to 45 and must exceed that horizon by at least seven
days. Changing an environment setting does not change AWS snapshot retention.

Schedule protected ledger exports to storage outside the database being restored.
Export frequently enough for the reviewed recovery objective, and retain the
newest complete ledger independently of the database backup:

```powershell
python -m uv run --locked python -m app.account_cli export-ledger --output RESTRICTED_UNIQUE_EXPORT_PATH.json
```

The command refuses to overwrite an existing file. Exports contain only deletion
references, random parent/child IDs, a hashed Cognito subject for parent deletion,
and timestamps. They remain pseudonymous personal information: restrict access,
encrypt storage, and expire old export files. An export on the same machine as the
database is not a resilient independent backup. A ledger captured only before a
deletion cannot prevent that later deletion from being resurrected.

After restoring a database, keep account and collection flags false and public
traffic blocked, replay the newest trusted ledger, revoke all restored login
sessions, and run retention before reopening:

```powershell
python -m uv run --locked python -m app.account_cli replay-ledger --input RESTRICTED_LEDGER_PATH.json
python -m uv run --locked python -m app.account_cli retention
```

Ledger replay automatically revokes every restored login session, rejects expired
entries, and is idempotent for repeated IDs. Restore
testing must also reconcile the current Cognito pool, pending privacy jobs, and any
external stores. Do not restore a backup outside the supported ledger horizon.

## Synthetic verification

The automated account tests create isolated in-memory SQLite schemas and mocked
Cognito/SES clients. They use no real AWS calls or student records:

```powershell
python -m uv run --locked python -m unittest tests.test_accounts_privacy -v
python -m uv run --locked python -m unittest discover -s tests -v
```

Before real users, verify in isolated PostgreSQL/Cognito staging: migration and
runtime privilege separation; managed-login/PKCE/nonce and fresh sign-in behavior;
two-parent isolation; pending and withdrawn consent; simultaneous deletion during
speech recognition; network-failure retries; both SES recipients; real PostgreSQL
cascades and row locks; full streamed export; retained backup expiry; and a complete
restore with ledger replay. The current local automated checks do not establish
those deployed behaviors or legal compliance.
