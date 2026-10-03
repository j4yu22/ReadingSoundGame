# Parent accounts and child-data controls

Status: implementation for review, October 3, 2026. PostgreSQL and Cognito are
provisioned; see the [deployment record](account-services-deployment.md) for the
staged application and verification status. Use synthetic family records for testing.

This document describes the implementation and the work needed before real
children use it. It is not a legal opinion or a certification of COPPA compliance.
The public notice is a draft and the consent process requires legal review.

## What is enabled by this change

Parents sign in through Amazon Cognito. The browser receives an opaque session
cookie; Cognito tokens and the client secret remain on the server. A parent can
manage nicknamed child profiles, review progress, export records, request deletion,
and withdraw consent. Children do not receive separate email/password identities.
There are no teacher permissions, student directories, ads, or analytics exports.

All three launch flags default to false:

```dotenv
ACCOUNTS_ENABLED=false
ACCOUNT_REGISTRATION_OPEN=false
CHILD_DATA_COLLECTION_ENABLED=false
```

With `ACCOUNTS_ENABLED=false`, guest exercises and speaking activities remain
available without login or database storage. Audio still passes through the
server to Azure Speech; the application returns feedback without saving audio,
transcripts, results, or practice history. Guest mode does not establish parental
consent or remove the need for review before testing with real children.

With `ACCOUNTS_ENABLED=true`, profile creation, practice, and speech require a
configured account service, enabled child collection, and the parent's verified
consent for the current notice. Incomplete account configuration blocks practice;
it never switches to guest mode. The frontend follows the server's
`practice_mode` value: `guest`, `account`, or `unavailable`. An emailed sign-in
code, email verification, an adult checkbox, and browser microphone permission
do not activate consent.

Changing `PRIVACY_NOTICE_VERSION` suspends account-based practice until consent
for that version has been reviewed. Administrative approval is available only through the operator
CLI, not an HTTP endpoint. See [account operations](account-operations.md) for the
exact command.

## Data inventory

The account and progress records below apply to account mode. Guest mode uses
transient speech processing and returns feedback without writing those records.

| Data | Purpose and location | Removal |
| --- | --- | --- |
| Parent email, Cognito subject and username | Parent sign-in in Cognito; ownership/contact in PostgreSQL | Account deletion removes the app record and queues identity deletion in Cognito |
| Session token hash and expiry | Server-side login; raw token only in an HttpOnly cookie | Logout, expiry, withdrawal, or account deletion |
| Child nickname and random identifier | Distinguish profiles belonging to a parent | Child/account deletion or consent withdrawal |
| Exercise selection, UTC timestamps, outcome, speech-detection flag, final scores | Parent progress in PostgreSQL | Cascading deletion or the scheduled progress-retention sweep |
| Immutable curriculum snapshot and scoring version | Explain what an attempt measured | Shared nonpersonal curriculum, retained independently of families |
| Microphone WAV | Transient server memory, then Azure Speech recognition | Application writes no recording archive; the request releases its references |
| Recognition text and phoneme detail | Transient assessment processing | Excluded from API results, account tables, and application logging |
| Consent notice version, status, reviewer, evidence reference | Record the reviewed authorization | Account deletion; withdrawn/superseded records expire under retention |
| Deletion jobs and minimal deletion ledger | Retry failed work and prevent restoration of deleted records | Bounded expiry; ledger exports require the same controls |
| Notification reference and parent contact | Notify the two privacy operators through SES | Contact cleared after delivery or after seven days; sent records expire after 30 days |

Nicknames are still protected personal information in this design. Do not collect
full names, dates of birth, school identifiers, diagnoses, or demographic fields.
The app does not infer age, disability, identity, or emotion from speech. Scores
describe individual exercises and should not be presented as a clinical assessment.

The existing audio cache contains synthesized curriculum instructions and sound
clips. The browser-supplied token fallback bypasses persistent caching. Review and
retire any preexisting cache produced by earlier versions before child testing.
Cloud-provider logs, email inboxes, signed consent evidence, exports, and backups
are separate stores; the app's SQL cascade cannot erase them automatically.

## Consent process to finalize with counsel

The current workflow is an operator-reviewed signed parental consent process.
A parent's request creates a pending record and an operator notification. A trained
operator must deliver the reviewed direct notice, obtain and assess the signed
form through an approved channel, and then record the restricted evidence reference
and reviewer ID. Do not put a document, identity scan, personal details, or a public
download URL into the reference field. There is no document-upload endpoint.

Before opening registration, finalize:

- The operator's legal identity, mailing address, phone number, and notices.
- The intended launch jurisdictions and whether additional state or international
  child/student privacy rules apply.
- The signed form, secure return channel, verification instructions, parent direct
  notice, material-change notices, evidence retention, and staff responsibilities.
- Azure/AWS processing terms, subcontractors, processing regions, and whether the
  speech disclosures are integral to the service. Add any separate consent that
  the reviewed use requires.
- How to verify and handle requests from a parent who cannot sign in, disputes
  over account ownership, and requests received outside the website.

The [COPPA Rule](https://www.ecfr.gov/current/title-16/chapter-I/subchapter-C/part-312)
addresses notice, verifiable parental consent, parent access/deletion, reasonable
security, and limited retention. Its listed consent methods include a signed form
returned to the operator. This implementation does not establish that a particular
form, notice, provider arrangement, or operating practice satisfies those duties.
Have counsel review the actual service and operating procedures before launch.

## Retention and deletion

Proposed defaults, subject to review:

| Store | Implemented/default period |
| --- | --- |
| Practice sessions and dependent attempts | 90 days; daily sweep |
| Login sessions | Eight hours; sensitive privacy actions require a login within 15 minutes |
| Parent accounts that never obtained verified consent | Deletion queued 30 days after creation; signing in does not extend this |
| Cognito signups that never completed the app callback | Removed by the daily retention job after 30 days |
| Other inactive parent accounts | Deletion queued after 365 days of inactivity |
| Withdrawn/superseded consent records | 45 days from request creation |
| Completed deletion jobs and minimal ledger | 45 days, with ledger retention exceeding the backup horizon |
| Notification contact / delivered notification record | At most seven days / 30 days |
| RDS automated backups in the proposed stack | Seven days |
| Maximum separately managed backup horizon | 35 days; requires inventory and enforcement |

Deleting a child blocks that profile immediately and queues removal of its practice
records. Withdrawal blocks the family's account-based practice and revokes sessions; the parent can
sign in later to manage the account. Account deletion also removes the Cognito
identity. Child records, consent records, and local contact/session data are purged
before retrying a failed Cognito deletion; the disabled identity mapping remains
only so that operation can finish. The worker retries failures independently of
email delivery. Service
failures must trigger operator attention; a queued request is not completed erasure.

Maintain a restricted external deletion-ledger copy outside the database being
restored. Reconcile it into any restored database **before** reopening traffic.
Revoke restored sessions and reapply retention. A ledger stored only in a database
snapshot cannot describe deletions requested after that snapshot. The CLI supports
export and replay; the separate export schedule, backup expiry enforcement, and
restore rehearsal must be established before launch. Manual and final RDS snapshots
do not inherit an automatic expiry from the app's retention settings.

Signed evidence, contact inboxes, downloaded exports, and provider records require separate documented
retention and erasure procedures. Do not mark an external request fully complete
until its applicable stores have been handled.

## Operator notifications

`PRIVACY_CONTACT` and `PRIVACY_NOTIFICATION_EMAILS` are configured for both:

- gina_underwood@yahoo.com
- jay.e.underwood@gmail.com

The API queues consent-review and deletion notifications. The email includes only
event type, request reference, and parent contact when still available. Account
erasure clears queued contact details before notification delivery. It includes no child names,
recordings, or scores. Both recipients are in the same To list. Configure a verified
SES sender in `PRIVACY_NOTIFICATION_FROM`; sandbox accounts also require verified
recipients. No email has been sent by this change. Protect both mailboxes with MFA
and an agreed access/retention process. Parents should not send sensitive child
records or identity documents in ordinary email.

## Security and data-model decisions

- Exact parent ownership checks on every family-data operation. Teacher access
  will need explicit grants and its own authorization review.
- Secure, HttpOnly, SameSite cookies; OAuth code flow with PKCE and nonce checks;
  exact same-origin and CSRF checks for state-changing requests.
- Server-derived expected answers, unique request IDs, one submission per speaking
  step, and separate technical-error/abandonment outcomes. A failed microphone or
  speech service is not an incorrect answer.
- Bounded input bodies and recordings, daily account practice quotas, no wildcard CORS,
  no access-log request URLs, generic validation/provider error responses, and
  `no-store` for API responses.
- Private encrypted PostgreSQL, TLS verification and IAM database authentication,
  separate runtime/migration users, scoped Cognito/SES permissions, and protected
  infrastructure state. See [AWS setup](../infra/accounts/README.md).
- Normalized parent/child/session/attempt records with foreign keys and cascading
  deletion. Versioned curriculum preserves the meaning of historical results.
  A future warehouse should start with a reviewed purpose, minimum data, its own
  retention, and propagated deletion; it must not become an indefinite copy.

Before launch, assign security/privacy owners, complete provider review and a
written security/retention program, enable operational failure alerts, test backup
restoration and deletion, and perform an independent access-control review.
[AWS's shared-responsibility model](https://aws.amazon.com/compliance/shared-responsibility-model/)
does not transfer application permissions or privacy operations to AWS.

## Verification and limitations

Automated checks use synthetic records, synthetic WAVs, mocked providers, and an
isolated browser session. They exercise family isolation, consent gates, CSRF,
retry behavior, scores, deletion, retention, and the frontend lifecycle. Real
PostgreSQL integration and Cognito configuration checks are recorded in the
[deployment record](account-services-deployment.md). They do not verify the full
human sign-in flow, SES delivery, Azure contract, parental identity, or legal
compliance. Follow [the deployment runbook](../deploy/README.md) for verification
before a production rollout or testing with real children, including guest use.
