$ErrorActionPreference = "Stop"

$ArthurRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvPath = Join-Path $ArthurRoot ".env"

if (Test-Path -LiteralPath $EnvPath) {
    Get-Content -LiteralPath $EnvPath | ForEach-Object {
        $Line = $_.Trim()
        if (-not $Line -or $Line.StartsWith("#") -or -not $Line.Contains("=")) {
            return
        }

        $Parts = $Line.Split("=", 2)
        $Name = $Parts[0].Trim()
        $Value = $Parts[1].Trim().Trim('"').Trim("'")
        if ($Name) {
            Set-Item -Path "Env:$Name" -Value $Value
        }
    }
}

if (-not $env:FCC_REPO_PATH) {
    $env:FCC_REPO_PATH = "O:\coding\python\gitstuff\free-claude-code"
}

if (-not $env:FCC_BASE_URL) {
    $env:FCC_BASE_URL = "http://127.0.0.1:8082"
}

if (-not $env:FCC_AUTH_TOKEN) {
    $env:FCC_AUTH_TOKEN = "freecc"
}

if (-not $env:FCC_MODEL) {
    $env:FCC_MODEL = "claude-3-5-sonnet-20241022"
}

if (-not $env:WHISPER_DEVICE) {
    $env:WHISPER_DEVICE = "cpu"
}

if (-not $env:WHISPER_MODEL) {
    $env:WHISPER_MODEL = "base"
}

if (-not $env:ARTHUR_TTS_PROVIDER) {
    $env:ARTHUR_TTS_PROVIDER = "browser"
}

if (-not $env:AZURE_SPEECH_VOICE) {
    $env:AZURE_SPEECH_VOICE = "en-US-GuyNeural"
}

$VoiceExtra = if ($env:WHISPER_DEVICE -eq "nvidia_nim") { "voice" } else { "voice_local" }

uv run --project $env:FCC_REPO_PATH --extra $VoiceExtra python "$ArthurRoot\server.py"
