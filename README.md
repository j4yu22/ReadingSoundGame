# Reading Sound Games

Phonological practice with a FastAPI backend, browser frontend, and Azure Speech.

## Parent accounts (under review)

The account implementation uses Cognito for parent sign-in and PostgreSQL for
child profiles and practice history. With accounts disabled, guest exercises and
speech remain available without saved progress. Sign in and Sign up are inactive
placeholders until account setup is complete. Account registration and saved child
data remain closed until the consent process, privacy notice, and deployment are reviewed.
No AWS services are created by starting the app locally.

- [Privacy design and review requirements](docs/privacy-design.md)
- [Consent, retention and deletion operations](docs/account-operations.md)
- [Deployment, local setup, consent and deletion operations](deploy/README.md)
- [Separate Cognito/RDS infrastructure](infra/accounts/README.md)

From `src/api`, run `python -m uv sync` and `.\run.ps1`, then visit
`http://127.0.0.1:5178`. Guest practice needs Azure Speech settings but no database or
Cognito configuration. Use synthetic records for account tests; do not turn on
child collection to bypass missing consent verification.

Backend checks: from `src/api`, run
`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`.

Browser checks: see [the isolated parent portal tests](test/parent-portal/README.md).

## Earlier static demos

These GitHub Pages links do not provide the authenticated account API:

main menu link: https://j4yu22.github.io/ReadingSoundGame/

activity link: https://j4yu22.github.io/ReadingSoundGame/src/web/

tts test link: https://j4yu22.github.io/ReadingSoundGame/ttsTest/

example link: https://j4yu22.github.io/ReadingSoundGame/example/
