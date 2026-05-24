const recordBtn = document.getElementById("recordBtn");
const recordLabel = document.getElementById("recordLabel");
const resetBtn = document.getElementById("resetBtn");
const subtitle = document.getElementById("subtitle");
const transcript = document.getElementById("transcript");
const turnCount = document.getElementById("turnCount");
const proxyStatus = document.getElementById("proxyStatus");
const whisperStatus = document.getElementById("whisperStatus");
const ttsStatus = document.getElementById("ttsStatus");
const statusText = document.getElementById("statusText");
const voiceSelect = document.getElementById("voiceSelect");
const volumeSlider = document.getElementById("volumeSlider");
const rateSlider = document.getElementById("rateSlider");
const pitchSlider = document.getElementById("pitchSlider");
const volumeValue = document.getElementById("volumeValue");
const rateValue = document.getElementById("rateValue");
const pitchValue = document.getElementById("pitchValue");
const previewVoiceBtn = document.getElementById("previewVoiceBtn");
const micLevel = document.getElementById("micLevel");
const micHint = document.getElementById("micHint");

const sessionId = crypto.randomUUID();
const API_BASE =
  window.location.port === "5500" ? "http://127.0.0.1:5177" : "";
const MIN_SIGNAL_PEAK = 0.0005;
const MAX_RECORDING_MS = 45000;

let audioContext = null;
let mediaStream = null;
let recorderNode = null;
let sourceNode = null;
let silenceNode = null;
let recordingStartedAt = 0;
let recordingTimer = null;
let recordedChunks = [];
let voices = [];
let turnTotal = 0;
let busy = false;
let recording = false;
let appConfig = { tts_provider: "browser", tts_ready: false };
let currentAudio = null;
let lastMeterUpdate = 0;

function setStatus(message) {
  statusText.textContent = message;
}

function setSubtitle(message) {
  subtitle.textContent = message;
}

function setPill(element, message, state) {
  element.textContent = message;
  element.classList.remove("ok", "bad");
  if (state) {
    element.classList.add(state);
  }
}

function appendLine(role, text) {
  const line = document.createElement("div");
  line.className = `line ${role}`;

  const speaker = document.createElement("div");
  speaker.className = "speaker";
  speaker.textContent = role === "user" ? "You" : "Arthur";

  const body = document.createElement("div");
  body.className = "text";
  body.textContent = text;

  line.append(speaker, body);
  transcript.appendChild(line);
  transcript.scrollTop = transcript.scrollHeight;

  if (role === "assistant") {
    turnTotal += 1;
    turnCount.textContent = `${turnTotal} ${turnTotal === 1 ? "turn" : "turns"}`;
  }
}

async function apiFetch(path, options = {}) {
  return fetch(`${API_BASE}${path}`, options);
}

function updateRanges() {
  volumeValue.textContent = Number(volumeSlider.value).toFixed(2);
  rateValue.textContent = Number(rateSlider.value).toFixed(2);
  pitchValue.textContent = Number(pitchSlider.value).toFixed(2);
}

function populateVoices() {
  voices = window.speechSynthesis.getVoices();
  voiceSelect.innerHTML = "";

  voices.forEach((voice, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = `${voice.name} (${voice.lang})`;
    voiceSelect.appendChild(option);
  });

  const preferredIndex = voices.findIndex((voice) => {
    const name = voice.name.toLowerCase();
    return name.includes("natural") || name.includes("aria") || name.includes("guy");
  });

  if (preferredIndex >= 0) {
    voiceSelect.value = String(preferredIndex);
  }
}

function selectedVoice() {
  const index = Number(voiceSelect.value);
  return Number.isNaN(index) ? null : voices[index] || null;
}

function updateMicMeter(peak) {
  const normalized = Math.min(100, Math.round(peak * 240));
  micLevel.style.width = `${normalized}%`;
  micHint.textContent = normalized > 2 ? `Mic level ${normalized}%` : "Mic input is very quiet.";
}

function speakWithBrowser(text) {
  const clean = text.trim();
  if (!clean) {
    return;
  }

  const utterance = new SpeechSynthesisUtterance(clean);
  const voice = selectedVoice();
  if (voice) {
    utterance.voice = voice;
  }
  utterance.volume = Number(volumeSlider.value);
  utterance.rate = Number(rateSlider.value);
  utterance.pitch = Number(pitchSlider.value);

  utterance.onstart = () => setStatus("Speaking");
  utterance.onend = () => setStatus("Idle");
  utterance.onerror = () => setStatus("Speech output failed");

  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
}

async function speak(text, ssml = "") {
  const clean = text.trim();
  const speechMarkup = ssml.trim();
  if (!clean && !speechMarkup) {
    return;
  }

  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
  window.speechSynthesis.cancel();

  if (appConfig.tts_provider !== "browser" && appConfig.tts_ready) {
    try {
      setStatus("Generating speech");
      const response = await apiFetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: clean, ssml: speechMarkup })
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "TTS failed");
      }

      const audioBlob = await response.blob();
      const audioUrl = URL.createObjectURL(audioBlob);
      currentAudio = new Audio(audioUrl);
      currentAudio.volume = Number(volumeSlider.value);
      currentAudio.onplay = () => setStatus("Speaking");
      currentAudio.onended = () => {
        URL.revokeObjectURL(audioUrl);
        currentAudio = null;
        setStatus("Idle");
      };
      currentAudio.onerror = () => {
        URL.revokeObjectURL(audioUrl);
        currentAudio = null;
        setStatus("Speech output failed");
      };
      await currentAudio.play();
      return;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setStatus(`TTS fallback: ${message}`);
    }
  }

  speakWithBrowser(clean);
}

function mergeChunks(chunks) {
  const totalLength = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const merged = new Float32Array(totalLength);
  let offset = 0;

  chunks.forEach((chunk) => {
    merged.set(chunk, offset);
    offset += chunk.length;
  });

  return merged;
}

function encodeWav(samples, sampleRate) {
  const bytesPerSample = 2;
  const buffer = new ArrayBuffer(44 + samples.length * bytesPerSample);
  const view = new DataView(buffer);

  function writeString(offset, value) {
    for (let i = 0; i < value.length; i += 1) {
      view.setUint8(offset + i, value.charCodeAt(i));
    }
  }

  writeString(0, "RIFF");
  view.setUint32(4, 36 + samples.length * bytesPerSample, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * bytesPerSample, true);
  view.setUint16(32, bytesPerSample, true);
  view.setUint16(34, 8 * bytesPerSample, true);
  writeString(36, "data");
  view.setUint32(40, samples.length * bytesPerSample, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    offset += bytesPerSample;
  }

  return new Blob([view], { type: "audio/wav" });
}

function getSignalPeak(samples) {
  if (!samples.length) {
    return 0;
  }

  let peak = 0;
  const stride = Math.max(1, Math.floor(samples.length / 12000));
  for (let i = 0; i < samples.length; i += stride) {
    peak = Math.max(peak, Math.abs(samples[i]));
  }

  return peak;
}

async function startRecording() {
  if (busy || recording) {
    return;
  }

  window.speechSynthesis.cancel();
  mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true
    }
  });

  audioContext = new AudioContext();
  await audioContext.audioWorklet.addModule("recorder-worklet.js");
  sourceNode = audioContext.createMediaStreamSource(mediaStream);
  recorderNode = new AudioWorkletNode(audioContext, "arthur-recorder", {
    numberOfInputs: 1,
    numberOfOutputs: 1,
    outputChannelCount: [1]
  });
  silenceNode = audioContext.createGain();
  silenceNode.gain.value = 0;
  recordedChunks = [];
  updateMicMeter(0);

  recorderNode.port.onmessage = (event) => {
    const input = event.data;
    recordedChunks.push(new Float32Array(input));
    const now = Date.now();
    if (now - lastMeterUpdate > 90) {
      lastMeterUpdate = now;
      updateMicMeter(getSignalPeak(input));
    }
  };

  sourceNode.connect(recorderNode);
  recorderNode.connect(silenceNode);
  silenceNode.connect(audioContext.destination);
  recordingStartedAt = Date.now();
  recording = true;
  recordBtn.classList.add("recording");
  recordLabel.textContent = "Stop";
  setSubtitle("Listening...");
  setStatus("Recording");

  recordingTimer = setTimeout(() => {
    if (recording) {
      stopRecording();
    }
  }, MAX_RECORDING_MS);
}

async function stopRecording() {
  if (!recording) {
    return;
  }

  recording = false;
  busy = true;
  recordBtn.disabled = true;
  resetBtn.disabled = true;
  recordBtn.classList.remove("recording");
  recordLabel.textContent = "Record";
  clearTimeout(recordingTimer);

  const sampleRate = audioContext ? audioContext.sampleRate : 48000;

  if (recorderNode) {
    recorderNode.port.onmessage = null;
    recorderNode.disconnect();
  }
  if (sourceNode) {
    sourceNode.disconnect();
  }
  if (silenceNode) {
    silenceNode.disconnect();
  }
  if (mediaStream) {
    mediaStream.getTracks().forEach((track) => track.stop());
  }
  if (audioContext) {
    await audioContext.close();
  }

  const duration = Date.now() - recordingStartedAt;
  const samples = mergeChunks(recordedChunks);
  recordedChunks = [];
  const peak = getSignalPeak(samples);
  updateMicMeter(0);

  if (duration < 350 || samples.length === 0 || peak < MIN_SIGNAL_PEAK) {
    busy = false;
    recordBtn.disabled = false;
    resetBtn.disabled = false;
    setSubtitle("No microphone input captured.");
    micHint.textContent = "Check the browser microphone permission and selected input device.";
    setStatus("Idle");
    return;
  }

  const audioBlob = encodeWav(samples, sampleRate);
  await sendTurn(audioBlob);
}

async function sendTurn(audioBlob) {
  setSubtitle("Transcribing...");
  setStatus("Transcribing");

  try {
    const response = await apiFetch("/api/turn", {
      method: "POST",
      headers: {
        "Content-Type": "audio/wav",
        "X-Arthur-Session": sessionId
      },
      body: audioBlob
    });

    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || payload.error || "Request failed");
    }

    appendLine("user", payload.user_text);
    appendLine("assistant", payload.assistant_text);
    setSubtitle(payload.assistant_text);
    void speak(payload.assistant_text, payload.assistant_ssml || "");
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    setSubtitle(message);
    setStatus("Error");
  } finally {
    busy = false;
    recordBtn.disabled = false;
    resetBtn.disabled = false;
  }
}

async function resetConversation() {
  if (busy) {
    return;
  }

  window.speechSynthesis.cancel();
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
  transcript.innerHTML = "";
  turnTotal = 0;
  turnCount.textContent = "0 turns";
  setSubtitle("Press Record.");
  setStatus("Idle");

  await apiFetch("/api/reset", {
    method: "POST",
    headers: { "X-Arthur-Session": sessionId }
  });
}

async function loadConfig() {
  try {
    const response = await apiFetch("/api/config");
    const config = await response.json();
    appConfig = config;
    setPill(proxyStatus, config.proxy_ok ? "Proxy online" : "Proxy offline", config.proxy_ok ? "ok" : "bad");
    setPill(whisperStatus, `${config.whisper_device}/${config.whisper_model}`, "ok");
    setPill(
      ttsStatus,
      config.tts_provider === "browser"
        ? "Browser TTS"
        : `${config.tts_provider} TTS`,
      config.tts_ready ? "ok" : "bad"
    );
  } catch {
    setPill(proxyStatus, "Backend offline", "bad");
    setPill(whisperStatus, "Whisper", "bad");
    setPill(ttsStatus, "TTS", "bad");
    setSubtitle("Start Arthur backend with .\\run.ps1, then refresh.");
  }
}

recordBtn.addEventListener("click", async () => {
  try {
    if (recording) {
      await stopRecording();
    } else {
      await startRecording();
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    setSubtitle(message);
    setStatus("Microphone failed");
    busy = false;
    recording = false;
    recordBtn.disabled = false;
    resetBtn.disabled = false;
    recordBtn.classList.remove("recording");
    recordLabel.textContent = "Record";
  }
});

resetBtn.addEventListener("click", resetConversation);
previewVoiceBtn.addEventListener("click", () => {
  void speak("This is Arthur's voice.");
});
volumeSlider.addEventListener("input", updateRanges);
rateSlider.addEventListener("input", updateRanges);
pitchSlider.addEventListener("input", updateRanges);
window.speechSynthesis.onvoiceschanged = populateVoices;

updateRanges();
populateVoices();
loadConfig();
