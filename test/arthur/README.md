# Arthur Voice Chat

Local browser voice chat for the Free Claude Code proxy.

## Run

Start Free Claude Code first:

```powershell
fcc-server
```

Then start Arthur:

```powershell
cd O:\coding\python\gitstuff\ReadingSoundGame\test\arthur
.\run.ps1
```

Open:

```text
http://127.0.0.1:5177
```

VS Code Live Server also works for the static files, but it does not start the
Python API. Keep `.\run.ps1` running, then open `arthur.html` with Go Live. The
page will call the backend at `http://127.0.0.1:5177`.

## Configuration

Arthur expects the Free Claude Code repo at:

```text
O:\coding\python\gitstuff\free-claude-code
```

For local development, copy `.env.example` to `.env` and put your real keys there.
The real `.env` file is ignored by git.

Override from PowerShell when needed:

```powershell
$env:FCC_REPO_PATH = "O:\coding\python\gitstuff\free-claude-code"
$env:FCC_BASE_URL = "http://127.0.0.1:8082"
$env:FCC_AUTH_TOKEN = "freecc"
$env:FCC_MODEL = "claude-3-5-sonnet-20241022"
```

Local Whisper transcription:

```powershell
$env:WHISPER_DEVICE = "cpu"
$env:WHISPER_MODEL = "base"
```

NVIDIA NIM transcription:

```powershell
$env:WHISPER_DEVICE = "nvidia_nim"
$env:WHISPER_MODEL = "openai/whisper-large-v3"
$env:NVIDIA_NIM_API_KEY = "..."
```

Whisper is speech-to-text only. Arthur uses the browser's built-in speech synthesis
for text-to-speech playback by default.

Azure Neural Speech TTS with SSML phonemes:

```powershell
$env:ARTHUR_TTS_PROVIDER = "azure"
$env:AZURE_SPEECH_KEY = "..."
$env:AZURE_SPEECH_REGION = "eastus"
$env:AZURE_SPEECH_VOICE = "en-US-GuyNeural"
```

For exact phonetic output, send SSML like:

```xml
<phoneme alphabet="ipa" ph="æ">a</phoneme>
```

If you already have `fastapi`, `uvicorn`, `httpx`, and the Whisper dependencies in
your active Python environment, `python server.py` also works.
