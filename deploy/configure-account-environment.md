# Prepare the EC2 account environment

`configure-account-environment.py` retrieves one JSON object from the SSM
SecureString `/reading-sound-game/accounts/config` using the host's existing AWS
role and AWS CLI. It merges only approved account keys into
`/opt/reading-sound-game/.env`, preserving Azure and unrelated settings. The write
is atomic with mode `0600`. It does not restart services, touch Git, or alter timers.

An authorized operator must first create the SecureString through a protected
workflow. Generate `SESSION_SECRET` once, store it in that parameter, and reuse it
for recovery. Never place secrets in shell arguments, user data, Git, or command
output. The host script does not create or update the parameter.

Run from the existing checkout on the EC2 host:

```bash
sudo python3 deploy/configure-account-environment.py --dry-run
sudo python3 deploy/configure-account-environment.py
```

Both commands print approved key names only. The dry run also checks the current
file for an accidental session-secret change. AWS output and error details are
captured and never forwarded. A mismatched existing nonempty `SESSION_SECRET`
stops the operation; use `--rotate-session-secret` only for an intentional session
reset. Repeated preparation with the same parameter is safe.

The parameter must be a JSON object of **string values**. Required keys:

```text
ACCOUNTS_ENABLED=false
ACCOUNT_REGISTRATION_OPEN=false
CHILD_DATA_COLLECTION_ENABLED=false
ALLOW_INSECURE_LOCALHOST=false
PUBLIC_ORIGIN
SESSION_SECRET
DATABASE_URL
DATABASE_IAM_AUTH=true
DATABASE_SSL_ROOT_CERT=/app/certs/rds-global-bundle.pem
AWS_REGION=us-west-2
COGNITO_USER_POOL_ID
COGNITO_CLIENT_ID
COGNITO_CLIENT_SECRET
COGNITO_DOMAIN
COGNITO_REDIRECT_URI
```

Use the exact HTTPS origin without a trailing slash, its `/api/auth/callback` URL,
the Cognito domain in the same AWS region, and the passwordless
`postgresql+psycopg://reading_sound_app@RDS_ENDPOINT:5432/reading_sound_game` URL.
The script also accepts the account retention, privacy-contact/notification,
session-hours, and Compose-profile keys documented in `.env.example`. Unknown
keys, malformed or duplicate JSON fields, multiline/control-character values,
unsafe quoting, non-IAM database URLs, or any enabled collection flag are rejected.
This utility intentionally cannot open registration or collection.

For an instance rebuild, restore the Azure/site environment through its normal
protected provisioning path, restore this checkout, then run this utility before
the separately reviewed image build, migrations, and service startup. Keeping the
SSM parameter independent of the instance preserves the Cognito client secret and
session secret. Do not regenerate either merely because an instance was replaced.

The current core Terraform role allows `ssm:GetParameter` and `ssm:GetParameters`
under `/reading-sound-game/*`, which covers the account parameter. It currently
also has broad `kms:Decrypt` permission. Review that separate policy for least
privilege and ensure the parameter's KMS key policy permits this role; this script
does not change IAM or KMS permissions.

For another explicitly selected host location, `--parameter`, `--destination`,
and `--region` are available. Apply requires Linux root; `--dry-run` can validate
elsewhere. The destination directory must already exist, and a symlink destination
is rejected. Do not run under shell tracing or print the resulting environment.
