# Parent accounts: separate AWS stack

This stack adds Cognito and RDS to the existing website VPC. It does not manage
the EC2 instance, DNS, the existing security group, or the existing IAM role's
other policies. It adds narrowly scoped policies to that role.

## Deployment status — 2026-10-03

The production stack was applied in account `029099142578`, region `us-west-2`:
all 19 Terraform resources were created. RDS PostgreSQL 17.9 is available on a
private encrypted instance, and Cognito is configured with public signup off.
Master bootstrap, IAM-authenticated migrations, verified TLS connections,
runtime database permission checks, and nine real PostgreSQL integration tests
passed. Cognito operator configuration and public OIDC endpoint checks passed.

The public website still runs the older `main` application. The new account/privacy
source is staged as a candidate; it has not replaced the public application.
Account configuration is stored at SSM `/reading-sound-game/accounts/config`,
with `ACCOUNTS_ENABLED`, `ACCOUNT_REGISTRATION_OPEN`, and
`CHILD_DATA_COLLECTION_ENABLED` all false. Infrastructure readiness does not mean
public accounts or child collection are enabled. See the
[deployment record](../../docs/account-services-deployment.md) for rollout details.

Keep parent registration and child collection disabled until the actual notice,
parental-consent verification process, retention periods, vendor arrangements,
and access/deletion flow have completed legal and operational review. Creating
a Cognito account and verifying an email are not verifiable parental consent.
There is no child signup, warehouse, recording archive, or mentor portal here.

## Resources and defaults

- Cognito Essentials parent pool, exact HTTPS callback `/api/auth/callback`,
  authorization code flow, confidential server client, token revocation, and
  optional authenticator-app MFA. Self-registration defaults off.
- PostgreSQL 17 on private encrypted RDS `db.t4g.small`, 20 GB gp3 with autoscaling
  capped at 100 GB, Single-AZ by default, seven-day automated backups, and deletion
  protection. A reviewed retirement takes a final snapshot.
- Port 5432 accepts connections only from the existing application security
  group. No public database endpoint, SSH change, NAT gateway, RDS Proxy, or new
  web server is created.
- IAM database authentication: `reading_sound_app` has application data access;
  `reading_sound_migrator` belongs to a separate deployment role. The website
  cannot retrieve the RDS master secret or modify the database schema.
- The runtime role can list and delete parent identities only in this Cognito
  pool, including abandoned signup identities that never created an app record.
- Optional SES permission sends privacy-request notifications from one verified
  identity and exact sender to Gina and Jay only. No student results or recordings
  should appear in these messages. Sending failure does not block deletion.

Budget for the RDS instance, storage, KMS key/requests, managed master secret,
email, backups beyond included allowances, and existing EC2/Azure bills.
Multi-AZ and storage autoscaling add cost. Review the current Oregon quote in
AWS Pricing Calculator before future changes and maintain a billing budget; this stack does
not impose a hard spending cap. Local development needs no hosted RDS instance.

## Prerequisites for another environment or future changes

1. Identify the existing VPC, website security group and EC2 instance role.
2. Provide **private** database subnet IDs in that VPC across at least two AZs,
   OR use `private_db_subnets` to create dedicated subnets. Never provide both.
   New subnets use your reviewed unused CIDRs, disable public IP assignment, and
   associate with a dedicated route table containing only the VPC-local route.
   No NAT gateway or Internet gateway is created. For existing subnet IDs, review
   their route tables: this stack verifies VPC/AZ membership but does not change
   existing routes or infer whether those subnets are private. The old website
   stack's default/public subnets must not be assumed appropriate.
3. Provide an existing separate migration role, OR set
   `trusted_migration_operator_arn` to one exact IAM user ARN and let this stack
   create the named migration role with MFA-required trust. The new role has only
   IAM connection permission for the migrator database user. Do not give the
   website permission to assume it. The operator must be able to obtain an
   MFA-authenticated role session; a root CloudShell session does not satisfy
   this exact-user trust. Migration jobs also need network access through the
   allowed application security group. Bootstrap access to the master secret is
   separate and temporary; it is not granted to the runtime or migrator role.
4. Use distinct production and staging stacks, Cognito pools, databases, domains,
   IAM roles, and state keys. Synthetic records only in development/staging.
5. Prepare an access-restricted, encrypted S3 Terraform-state bucket with versioning,
   public-access blocking, and state locking. Never commit state, plans containing
   sensitive values, credentials, or real `.tfvars` files.
6. For real authentication emails, verify an SES sender/domain, configure its
   sending authorization for Cognito as required by AWS, and request production
   access. Cognito's default email sender has small test limits. Privacy emails
   need `privacy_email_identity_arn`, `privacy_email_from`, and the matching
   `PRIVACY_NOTIFICATION_FROM`. In the SES sandbox, the two recipient addresses
   also need verification. Terraform creates no mailboxes, DNS records, or emails.

The database password is generated and managed by RDS in Secrets Manager, rather
than supplied to Terraform. **The Cognito client secret is stored in Terraform
state**, even though it is intentionally omitted from outputs. Encrypt and
restrict state access accordingly. The client secret belongs only in server
configuration, never JavaScript, a screenshot, an issue, or an application log.

## Validate and review a plan

Use Terraform >=1.10 and the checked-in provider lockfile. Run from this directory:

```powershell
Copy-Item terraform.tfvars.example terraform.tfvars
# Fill in non-secret resource IDs and the exact HTTPS origin.
terraform init -backend-config="bucket=YOUR-STATE-BUCKET" -backend-config="key=reading-sound-game/accounts/staging.tfstate" -backend-config="region=us-west-2" -backend-config="encrypt=true" -backend-config="use_lockfile=true"
terraform fmt -check
terraform validate
terraform plan
```

Review network IDs, costs, signup state, IAM policies, backups, and the resource
list before a separate deployment. Never reuse the `infra/aws` state for this
stack. Do not upload plan output containing secrets to a PR.

`tfvars.production.example` records the verified non-secret production VPC,
website resources, database subnet ranges, and Jay's IAM user. The Cognito domain
prefix is now assigned to this deployment. The existing protected state bucket
uses the separate key `reading-sound-game/accounts/production.tfstate`; never
reuse the website stack's key. Recheck the live network and IAM identities when
using this example later, and use the existing state rather than trying to
recreate these resources in a new state.

For that reviewed production target, initialize with
`terraform init -backend-config=backend.production.hcl.example` and plan with
`terraform plan -var-file=tfvars.production.example`. The production input sets
`expected_account_id` so the AWS provider rejects credentials for another account.

Initial local schema validation used Terraform 1.16.5 and AWS provider 6.61.0;
formatting and validation passed. The authenticated production plan and apply
subsequently succeeded, followed by the live service checks listed above. Parent
browser login and the candidate application's public deployment remain separate
release steps; OIDC endpoint checks do not establish that full user flow.

## Database bootstrap and migrations

The initial production bootstrap and migrations have completed. The following
procedure is for a new database, not a command to rerun on the existing roles:

1. Retrieve the managed master secret with a separate bootstrap operator identity.
   Use a secure password prompt, never a password embedded in a shell command.
2. From a network location allowed by the database security group, connect to
   `reading_sound_game` as `reading_sound_admin` with `sslmode=verify-full` and the
   official RDS CA bundle. Run `bootstrap.sql` once. It creates the two IAM DB
   users and grants database/schema access. It does not alter another role's
   default privileges or grant `rds_iam` back to the master indirectly.
   It revokes creator memberships and verifies the master is not a member of
   `rds_iam` before committing. This revoke and assertion succeeded on the actual
   RDS PostgreSQL 17.9 instance during the initial rollout, and subsequent master
   password authentication still worked. Keep the guards: if a future engine or
   role configuration refuses the revoke or assertion, roll back the transaction.
   Do not remove the assertion or commit around it.
3. Connect using IAM as `reading_sound_migrator`, in `reading_sound_game`, and
   run `migrator-privileges.sql` to establish runtime privileges on future tables.
   Then, as that same user, run `python -m alembic upgrade head` in `src/api`
   with IAM authentication and the migration role's temporary AWS credentials.
   Never grant the website the master/migration credentials. Inspect each
   migration in its PR before deployment; migrations do not run on every startup.
4. Run the website with the **runtime** passwordless URL from
   `terraform output application_configuration`, `DATABASE_IAM_AUTH=true`, and
   the bundled RDS certificate file. The backend generates a fresh IAM token for
   new connections. Do not append a fixed 15-minute token to a persistent `.env`.

Do not use the bootstrap script as an idempotent migration: its `CREATE ROLE`
commands intentionally fail if these users already exist. The runtime has DML
access to application tables, not schema ownership. Separate databases and
parent-level authorization tests are still necessary; IAM is not row-level
authorization between families.

When creating private subnets, the input has this shape (replace placeholders
only after checking the VPC's actual allocated ranges and available AZs):

```hcl
private_db_subnet_ids = []
private_db_subnets = {
  a = { cidr_block = "REVIEWED-UNUSED-CIDR-A", availability_zone = "VERIFIED-AZ-A" }
  b = { cidr_block = "REVIEWED-UNUSED-CIDR-B", availability_zone = "VERIFIED-AZ-B" }
}
```

The plan must show only the dedicated database subnets and their private route
table, with no changes to existing website routes. AWS validates CIDR containment
and overlap when creating subnets; inspect these before applying, not afterward.

## Configure the existing EC2 deployment

Follow [the deployment runbook](../../deploy/README.md). Keep the three account
collection flags false until the review is complete. Securely set:

- Database and Cognito values from the non-secret Terraform output.
- `COGNITO_CLIENT_SECRET`, retrieved by an authorized operator from the app client.
- A unique high-entropy `SESSION_SECRET`; rotate with an intentional session reset.
- `PUBLIC_ORIGIN` to the exact HTTPS site origin; `ALLOW_INSECURE_LOCALHOST=false`.
- `PRIVACY_NOTIFICATION_FROM` to the verified sender, and the two configured
  recipients. Review the published privacy contacts and actual notice version.
- `COMPOSE_PROFILES=accounts` to run durable deletion/email retries and retention.

Configure EC2 instance metadata to require IMDSv2 and allow the container hop
(typically response hop limit 2). Test IAM token generation from the actual app
container before enabling accounts. Do not fall back to permanent AWS access keys.
The existing core stack does not configure this setting. The application and
worker share the instance role; IAM policies here do not isolate containers from
each other. A later ECS/task-role deployment can separate them if needed.

Account configuration has been provisioned separately in SSM at
`/reading-sound-game/accounts/config`. The existing `infra/aws` boot script only
writes Azure/site settings, so preserve and restore account configuration during
an instance rebuild. Retrieve secrets through a reviewed policy and render a
root-readable `0600` environment file; do not put them into Terraform variables
or user data. Preparing that configuration does not deploy the staged candidate
or activate its account routes on the older public application.

**Existing-stack replacement caveat:** `infra/aws/main.tf` has
`user_data_replace_on_change=true`. Its template now removes unsafe shell tracing
and restricts the environment-file mode. Applying that existing stack can replace
EC2 because the user data changed. This separate accounts stack avoids that
replacement. Review the old stack's plan separately, preserve the current host's
configuration, and rotate any credential known to have appeared in old logs.

## Backups, erasure, and recovery

The candidate's deletion worker is only part of deletion. Keep a restricted external
copy of the minimal deletion ledger outside the database restoration target,
with a reviewed expiry longer than the oldest recoverable backup. A ledger in
the restored database alone cannot remember requests made after that snapshot.
Never copy children’s names, audio, or scores into the ledger.

RDS automated backups expire after seven days here. Manual/final snapshots, AWS
Backup plans, database exports, Terraform state versions containing Cognito
secrets, and any cross-account copies need a separately enforced inventory and
expiry policy. The application defaults allow a maximum 35-day backup horizon
and a 45-day deletion ledger; these are proposed engineering settings, not an
approved legal retention policy. If any copy lives longer, update both policy
and enforcement before collecting real data. Final snapshots do not expire
automatically, so a retirement is incomplete until their expiry is scheduled.

Before reopening a restored database:

1. Restore into an isolated environment with child collection disabled, no public
   application, and outbound privacy emails disabled.
2. Import/reconcile deletion requests from the current external ledger, including
   requests newer than the restored snapshot. Apply account/child erasures and
   consent withdrawals, invalidate recovered sessions, and run retention.
3. Verify zero recovered records for deleted subjects and run family-isolation
   checks. Never rely on the backup's old consent/session state.
4. Obtain operational signoff before switching traffic. Record only necessary
   completion evidence and let the isolated restore expire under the policy.

Rehearse this on synthetic data. Automatic export/reconciliation of the external
ledger, backup-expiry alarms, dead-letter alerts, incident-response ownership,
and a scheduled restore drill remain launch requirements; this Terraform stack
does not claim to configure those operational controls automatically.

## References

- [RDS IAM database users](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.IAMDBAuth.DBAccounts.html)
- [RDS TLS certificates](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html)
- [Cognito managed login](https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools-managed-login.html)
- [AWS provider](https://registry.terraform.io/providers/hashicorp/aws/latest)
