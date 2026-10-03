# Bootstrap the account database from an operator session

Use the clean source bundle in AWS CloudShell under an authorized bootstrap
operator identity. Never give the EC2 application the master or migration
credentials. Install the API's locked dependencies in the operator environment,
and use its Python interpreter to run the helper.

Start a separate Session Manager port-forwarding session from CloudShell through
the approved EC2 instance to the RDS endpoint, remote port `5432`, local port
`5432`. Keep that session running. Obtain the official RDS CA bundle from
`https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem` and retain its
reviewed file path. Then, from the source bundle:

```bash
python deploy/bootstrap-account-database.py --ca /absolute/path/global-bundle.pem --test
```

The default instance identifier is `reading-sound-game-production-accounts` in
`us-west-2`; `--db-instance`, `--region`, and `--local-port` are explicit overrides.
The helper verifies that the described instance is private, encrypted,
IAM-enabled PostgreSQL with the expected database and master username.

It retrieves the RDS-managed master secret directly into memory, validates or
creates the two restricted database roles, connects as the IAM migrator, installs
its default runtime grants, and applies/checks Alembic migrations. A partial or
overly privileged role setup stops for operator review rather than silently
altering unknown roles. The runtime identity is then checked for TLS, table DML
permissions, and the absence of public-schema CREATE permission.

TLS uses `host=RDS_ENDPOINT` and `hostaddr=127.0.0.1`, so the tunnel does not weaken
certificate hostname verification. IAM tokens are signed for the actual RDS
endpoint and remote port, including when the local forwarded port differs.

`--test` also creates `reading_sound_game_test`, gives the migrator CREATE there
and both roles CONNECT, and runs the opt-in integration suite using a disposable
schema. The runtime receives no database CREATE privilege. Short-lived tokens
are supplied only in the subprocess environment, never in shell arguments or
persistent files. Tests use synthetic records, exercise real PostgreSQL locks and
cascades, and never call Cognito or SES. The dedicated test database remains for
later reviewed staging checks; each test run removes its temporary schema.

Without `--test`, the helper only bootstraps, migrates, and checks runtime access.
It is safe to rerun after successful completion. It refuses a source bundle
containing application `.env` files and forces all collection flags false in its
own process. It does not deploy the website, start services, or enable registration.
Output contains only success fields or a fixed stage and exception type; provider
messages, query parameters, credentials, and tracebacks are never printed.

The operator needs permission to describe this RDS instance, retrieve/decrypt its
managed master secret, and connect through IAM as both the migrator and runtime
database users. This helper intentionally does not add those permissions. After
bootstrap, remove any temporary bootstrap grants according to the deployment
procedure and keep the application on its existing restricted role.
