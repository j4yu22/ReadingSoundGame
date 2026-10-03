# Parent portal browser checks

Run from the repository root with Node.js 20+ and an available `playwright` installation:

```powershell
node test/parent-portal/check.cjs
```

By default the script imports `playwright` normally and launches its installed Chromium browser. If needed, install Chromium with `npx playwright install chromium` from the environment containing Playwright.

You can use an existing Playwright installation and local Chrome instead:

```powershell
$env:PLAYWRIGHT_MODULE = 'C:\path\to\node_modules\playwright'
$env:CHROME_PATH = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
node test/parent-portal/check.cjs
```

Set `SCREENSHOT_DIR` to an output directory to save optional mobile and desktop screenshots. Otherwise the checks create no screenshot files.

The harness starts a local static server on an ephemeral loopback port, mocks all API responses, and blocks external requests. Its parent accounts, child nicknames, catalog, and results are self-contained synthetic fixtures. Chrome uses a fake microphone; neither a live API nor `.env` files, AWS, or Azure are accessed. It closes the browser and server on completion and exits nonzero on a failure.

Checks cover guest practice and account layouts at 320, 390, 768, and 1440 pixels. Guest checks verify the inert sign-in/sign-up controls, mouse and keyboard tooltip access, an activity round, no account or progress requests, and microphone access only during speaking turns. Guest access must be explicitly configured; a missing or unavailable practice mode keeps practice locked.

Account checks cover closed/unverified/pending access, native nickname validation, safe text rendering, consent requests, child creation/deletion, export, withdrawal, same-origin CSRF headers, no persistent browser storage, neutral missing-speech outcomes, reauthentication, and microphone cancellation during account changes. Backend tests separately cover server-side authorization with mocked providers. Live Cognito redirects and Azure assessment still require verification in configured staging.
