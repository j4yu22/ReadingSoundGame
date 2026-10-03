# Account services deployment

Status on 2026-10-03: **PostgreSQL and Cognito are deployed and verified in AWS**.
The account-aware application is built and staged on EC2. The existing public
site is still running its previous `main` release; no Git changes were pushed.
Login, registration, and saved child progress are disabled in the prepared settings.

## Target

- AWS account: `029099142578`; region: `us-west-2`.
- Site: `https://readingsoundgames.com`.
- Existing EC2: `i-054cf1b29aac38041`, `/opt/reading-sound-game`, branch `main`.
- Existing VPC: `vpc-03514939d27a9189c`.
- Existing application role: `reading-sound-game-ec2-role`.
- Terraform inputs: `infra/accounts/tfvars.production.example`.
- Separate state key: `reading-sound-game/accounts/production.tfstate` in the
  existing `reading-sound-game-tfstate-029099142578-1783030773` bucket.

Read-only inspection confirmed that this bucket has versioning, encryption, and
all four S3 public-access blocks enabled. The existing EC2 server is healthy,
SSM is online, and IMDSv2 is required with container hop limit 2.

## Deployed services

- Private PostgreSQL 17.9 RDS, `db.t4g.small`, Single-AZ, 20 GB encrypted gp3,
  seven-day backups, deletion protection, and IAM database authentication.
- Database subnets `172.31.64.0/24` and `172.31.65.0/24`, in AZs `a` and `b`,
  using an explicit route table with only the VPC-local route.
- Database connections allowed only from the existing application security group.
- Cognito Essentials parent pool and server-only OAuth client, exact HTTPS
  callback, managed login, and signup disabled.
- Runtime access limited to application database operations and identities in
  this Cognito pool. Separate migration role trusting IAM user `Jay` with MFA.
- Encrypted SSM configuration at `/reading-sound-game/accounts/config` and a
  protected host environment file. Master credentials stay out of the application.

| Resource | Identifier |
| --- | --- |
| RDS instance | `reading-sound-game-production-accounts` |
| Database | `reading_sound_game` |
| Database host | `reading-sound-game-production-accounts.czswuei8uonq.us-west-2.rds.amazonaws.com` |
| Runtime / migration users | `reading_sound_app` / `reading_sound_migrator` |
| Cognito pool | `us-west-2_6LRbcye3P` |
| Cognito client | `330ljdnuoh9i27veqd2kiqmjub` |
| Cognito domain | `https://reading-sound-game-029099142578.auth.us-west-2.amazoncognito.com` |
| Migration IAM role | `reading-sound-game-migrations` |

The callback is exactly `https://readingsoundgames.com/api/auth/callback`;
logout returns to `https://readingsoundgames.com/`. No real parent accounts or
child records were created during verification.

The database instance and 20 GB storage were quoted by the AWS Pricing API at
`$0.032/hour` and `$0.115/GB-month`: **$25.66/month** using 730 hours.
Allow roughly **$27–30/month** with the KMS key, managed master secret, and small
request charges. This is an estimate, not a spending cap. Storage can grow to
100 GB, adding up to $9.20/month over the initial storage; backups, traffic,
authentication usage, and taxes can add charges. Existing EC2/Azure costs are extra.

## Verification completed

- Terraform applied 19 resources without replacing EC2 or changing its public routes.
- Database roles and Alembic revision `20261003_01` installed; schema comparison passed.
- All 9 PostgreSQL integration tests passed against a separate synthetic test database.
- All 56 original backend tests and 4 updater tests passed locally (the same
  9 PostgreSQL tests skip locally and passed in the separate real-database run).
- The staged container passed runtime readiness using the actual EC2 IAM role:
  verified TLS, current schema, required table privileges, no schema-change privileges,
  and Cognito discovery/signing keys.
- Candidate startup, public-page/health responses, the disabled login gate, and
  nonroot execution passed. Empty deletion and retention runs completed without
  errors, including the runtime role's Cognito access. Privacy email delivery is
  not configured and was not tested.
- A separate operator check verified the real Cognito client secret, callback,
  logout, scopes, code flow, and managed login. The sign-in page opens correctly.
- SecureString settings were merged into `/opt/reading-sound-game/.env`, preserving
  existing Azure settings. The file is root-owned with mode `0600`.
- The updated Git deployment helper was installed with a backup at
  `/usr/local/bin/reading-sound-game-update.before-accounts-20261003`.
  An isolated fixture verified failed-build retries and cache-migration order;
  a real unchanged-commit run left the public container and checkout untouched.

A complete human sign-in/callback/logout test is still needed before launch.
Readiness checks do not substitute for that test or the consent/legal review.

## Application rollout

The candidate image is `reading-sound-game:accounts-20261003`; its source is staged
at `/opt/reading-sound-game-releases/accounts-20261003`. The running site remains
unchanged and the five-minute Git update timer remains enabled. Its updated helper
now retries failed deployments and prepares cache ownership for the nonroot app.

The current source keeps guest practice available when `ACCOUNTS_ENABLED=false`,
without login or saved progress. Guest audio still passes through the server to
Azure Speech, and real child testing still needs review. When accounts are
enabled, practice requires configured services, enabled collection, and verified
parental consent; incomplete account configuration never enables guest fallback.
Rebuild and verify this updated source before rollout: the previously staged
candidate image predates the guest-mode change.

1. Commit and push the reviewed changes when ready. The current deployment tracks
   `main`, so pushing only the development branch does not update the public site.
2. Follow `deploy/README.md`. The installed updater handles the cache-volume
   ownership transition; manual deployments must perform that step themselves.
   Use the already installed host settings.
3. The `accounts` Compose profile is prepared in `.env`; the new Compose file
   starts deletion and retention workers when deployed.
4. Run the runtime readiness command in `account-operations.md` and check the
   application and both workers. Keep collection disabled during final review.

## Before enabling accounts

- Complete the planned parental-consent and legal review.
- Verify the real parent login/logout flow with a test parent account.
- Configure MFA for IAM user `Jay` before using the migration role.
- Cognito's built-in sender is suitable for limited testing. No SES identities
  currently exist; production email and privacy-notification delivery need setup.
- Finish the backup/deletion recovery and monitoring steps in the operations guide.

Enabling parent signup requires both the Cognito `parent_signup_enabled` setting
and the application's registration flag. Child collection has its own flag and
per-child consent checks; enabling authentication alone does not enable collection.
