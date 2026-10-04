# Reading Sound Game Deployment

This deployment runs the FastAPI backend and static web app in one app container,
with Caddy as the public reverse proxy.

## Local container run

Copy the sample environment file and fill in your Azure Speech values:

```powershell
Copy-Item .env.example .env
```

Then run:

```powershell
docker compose up --build
```

Open `http://localhost:8080` if `HTTP_PORT=8080` is set in `.env`.

## AWS notes

The browser microphone API requires a secure context. `localhost` works for local
testing, but the deployed site needs HTTPS for speech-to-text in normal browsers.
Use a real domain for `SITE_ADDRESS` so Caddy can request and renew a certificate.

If you set `SITE_ADDRESS=:80`, the site can load over plain HTTP, but microphone
recording will not work in production browsers.

## Parent accounts and student data

Account collection is off by default. Keep `ACCOUNTS_ENABLED`,
`ACCOUNT_REGISTRATION_OPEN`, and `CHILD_DATA_COLLECTION_ENABLED` false until
the legal/consent and operational launch review is complete. Authentication alone
does not establish parental consent. Use synthetic students for development.

With `ACCOUNTS_ENABLED=false`, guest exercises and speech remain available without
login or saved progress. Audio still passes through the server to Azure Speech;
the application saves no guest recordings, transcripts, results, or history.
Testing with real children still requires review. Setting `ACCOUNTS_ENABLED=true`
switches to parent-managed practice: configured services, enabled child collection,
and verified consent are required. An incomplete account setup blocks practice;
it does not silently fall back to guest mode.

The separate [accounts infrastructure stack](../infra/accounts/README.md)
documents Cognito, private PostgreSQL, IAM database authentication, email setup,
secret storage, migrations, and backup/deletion recovery.

### Current status — 2026-10-03

The accounts stack is deployed in AWS account `029099142578`, Oregon
(`us-west-2`): all 19 Terraform resources were created, private encrypted RDS
PostgreSQL 17.9 is available, and Cognito is configured. Database bootstrap and
migrations, runtime TLS/permission checks, and nine real PostgreSQL integration
tests passed. Cognito operator configuration and public OIDC endpoint checks
also passed; these do not replace a full browser login test.

The public website continues to run the older `main` application. The new
account/privacy source is a staged candidate and has not replaced that live
application. Its account configuration is stored at SSM
`/reading-sound-game/accounts/config`, with all three collection flags false.
Public registration and saved child progress remain disabled in that configuration.
The candidate's application release and later login enablement are separate
steps. See the [deployment record](../docs/account-services-deployment.md).

### Local PostgreSQL

From the repository root:

```powershell
docker compose -f compose.dev.yaml up -d
```

This exposes a PostgreSQL 17 development database on **127.0.0.1:5432 only**.
The dummy credentials in that file are for local synthetic data only. In
`src/api/.env`, set:

```dotenv
DATABASE_URL=postgresql+psycopg://reading_sound_dev:local-development-only@127.0.0.1:5432/reading_sound_game
DATABASE_IAM_AUTH=false
PUBLIC_ORIGIN=http://localhost:5178
ALLOW_INSECURE_LOCALHOST=true
```

Run explicit migrations from `src/api`:

```powershell
python -m uv run python -m alembic upgrade head
```

Then use the existing `run.ps1`. This database does not enable login or child
collection by itself; Cognito and consent settings still need their own setup.
The localhost cookie exception must not be used on a deployed origin. Do not
load production data into development. `compose.dev.yaml` is a standalone DB
service, not an override to the production Compose file.

### Deployment order

Initial production infrastructure and schema setup are complete. Use this
sequence for the candidate application release or a new environment; do not
rerun initial role creation against the existing production database.

1. Review the PR, infrastructure plan, notice/consent procedure, and proposed
   retention periods. Disable the existing five-minute Git auto-update timer
   while coordinating database migrations and a reviewed release.
2. For a new environment, provision the accounts stack and run master bootstrap,
   then `migrator-privileges.sql` as the IAM migration user before Alembic. For
   existing production, review only pending migrations, take an appropriate
   backup, and apply them using the separate migration role.
3. Put account settings and server secrets in the host's protected environment
   file. Keep `.env` out of Git, image builds, support logs, and screenshots. The
   old user-data script does not restore these new account settings automatically.
4. Build the image: `docker compose build app`. The image copies all of `src`,
   including Alembic configuration/migrations. It uses an unprivileged user and
   an official RDS CA bundle pinned by SHA-256. An upstream certificate-bundle
   change deliberately fails the build until reviewed; do not disable TLS.
5. On an existing host, once only, migrate the cache volume's owner before
   switching from the older root container:

   ```bash
   docker compose stop app
   docker compose run --rm --no-deps --user root app chown -R 10001:10001 /app/src/api/.cache
   ```

   This is the application-generated speech cache, not a student audio store.
   The updated Git updater below performs this transition automatically.
6. Set `COMPOSE_PROFILES=accounts` in the host `.env`, then
   `docker compose up -d`. This starts the deletion/notification worker every
   60 seconds and retention every 24 hours. Both restart after process/host
   failure. Migrations remain a separate deployment step.
7. Verify parent login/logout, consent gating, two-family isolation, speech
   authorization, export, deletion/retries, and request emails with synthetic
   records. Confirm the worker and retention services are healthy and alerts
   exist for failures/backlogs. Then complete the launch review before enabling
   any real student data collection.

The website must use the runtime IAM database user with certificate verification,
not the RDS master secret or migration role. The EC2 role also needs the scoped
Cognito deletion and optional SES permissions supplied by the accounts stack.

### Existing Git updater and file ownership

The systemd update service runs as root, so it can read the root-owned `0600`
host `.env`. Docker Compose injects the selected environment values into the
container; the nonroot app does not need to read the host file. Keep that file out
of the image and do not relax its permissions for UID 10001.

Install the reviewed updater once on an existing host without replacing EC2:

```bash
sudo install -o root -g root -m 0755 deploy/update-from-git.sh /usr/local/bin/reading-sound-game-update
```

The existing service/timer can keep their configuration. The script defaults to
`/opt/reading-sound-game` and `main`; explicit path/ref arguments support other
deployments. An unchanged Git HEAD with no pending deployment exits before any
Docker operation, so installing the updater does not switch the running website.
The script fetches only on invocation; this installation does not start, stop, or
disable the timer.

On a new commit it builds before stopping the app, migrates the shared cache to
UID/GID 10001, and waits for the deployed app to become healthy. Only the first
root-to-nonroot transition briefly stops the old app before the cache migration,
preventing newly created root-owned cache files. A cache-migration failure attempts
to restart that old app. Later deployments also verify cache ownership without
stopping the existing nonroot process first.

A pending marker inside `.git` survives failed builds or startup. The next timer
invocation retries even when Git HEAD already equals the target; the marker is
removed only after health checks pass. This is retry protection, not an automatic
image or schema rollback. Database migrations remain an explicit operator step.
The installed updater is a root-owned copy; later changes to its logic require
the same reviewed installation command. Source files inside the image are made
readable by the nonroot user even when deployment archives use a private umask.

### Privacy-request notifications

`PRIVACY_NOTIFICATION_EMAILS` is configured for
`gina_underwood@yahoo.com,jay.e.underwood@gmail.com`. Set
`PRIVACY_NOTIFICATION_FROM` to an SES-verified sender and match the infrastructure
sender identity/address. No messages are sent merely by adding this configuration.
In the SES sandbox, recipients must also be verified; request production access
before real use. The persistent worker retries notices separately from erasure.
Notifications do not replace the parent portal or justify keeping student data.

### Logs and operational follow-through

The candidate disables Uvicorn access logging so paths and authorization callback
queries do not enter routine logs. Caddy access logging is not enabled. Container diagnostic
logs are size-rotated; size limits are **not** time-based retention. Before real
use, implement a documented time-based log expiry policy, access auditing, worker
backlog alerts, backup/snapshot expiry, secret rotation, and a restore rehearsal.
Never enable request-body, SQL parameter, audio, or transcript logging in production.

The existing AWS user-data template was also changed to stop shell tracing and
write its `.env` with mode restricted by `umask 077`. Because that stack uses
`user_data_replace_on_change=true`, review its plan carefully: applying it may
replace EC2. The existing website Terraform stack was not applied during this
rollout. The separate accounts stack has been applied successfully; the candidate
application has not been deployed publicly.
