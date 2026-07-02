const voiceLine = document.getElementById("voiceLine");
const startButton = document.getElementById("startButton");
const activityStage = document.getElementById("activityStage");
const tokenRow = document.getElementById("tokenRow");
const dropRow = document.getElementById("dropRow");
const subtitleBox = document.getElementById("subtitleBox");
const statusText = document.getElementById("statusText");
const exerciseSelect = document.getElementById("exerciseSelect");
const volumeSlider = document.getElementById("volumeSlider");
const volumeValue = document.getElementById("volumeValue");

const API_BASE = window.location.port === "5178" ? "" : "http://127.0.0.1:5178";
const APP_VERSION = "stt-checkpoints-20260702c";
const TOKEN_PLAYBACK_RATE = 0.5;
const LISTEN_SAMPLE_RATE = 16000;
const WORD_LISTEN_MS = 2200;
const FINAL_LISTEN_MS = 2400;
const TOKEN_LISTEN_ENABLED = false;
let activityType = new URLSearchParams(window.location.search).get("type") || "deletion";
const voiceFlatPath = "M20 60 H500";
const voiceRestPath = "M20 60 H150 L174 18 L220 100 L270 32 L308 94 L348 16 L386 84 L404 52 H500";
const voiceSpikePaths = [
  voiceRestPath,
  "M20 60 H132 L154 24 L184 92 L220 32 L252 100 L292 28 L326 88 L354 20 L392 78 L414 56 H500",
  "M20 60 H118 L146 92 L172 18 L206 104 L240 38 L284 92 L306 48 L346 16 L382 100 L416 58 H500",
  "M20 60 H142 L166 34 L202 92 L232 50 L270 104 L300 24 L334 86 L370 44 L410 64 H500",
  "M20 60 H128 L156 18 L188 82 L222 104 L266 30 L304 96 L340 22 L378 70 L418 58 H500"
];

const fallbackActivity = {
  type: "deletion",
  word: "sailboat",
  tokens: [
    {
      id: "sound1",
      sound: "sail",
      role: "match",
      action: "none"
    },
    {
      id: "sound2",
      sound: "boat",
      role: "deletion",
      action: "delete"
    }
  ],
  deleteTokenIds: ["sound2"],
  deleteSound: "boat",
  answer: "sail"
};

let currentActivity = fallbackActivity;
let activeToken = null;
let activePointerId = null;
let tokenIndex = 0;
let slidersUnlocked = false;
let speakingTimer = null;
let voiceInterval = null;
let voicePathIndex = 0;
let voiceFrame = null;
let audioContext = null;
let currentAudio = null;
let currentVolume = Number(volumeSlider.value);
let activityRunId = 0;
let micStream = null;
let activeTokenCapture = null;

console.info("[Arthur app]", {
  version: APP_VERSION,
  apiBase: API_BASE,
  script: document.currentScript?.src || "unknown"
});

function setSubtitle(text) {
  subtitleBox.textContent = text;
}

function setStatus(text) {
  statusText.textContent = `Voice: ${text}`;
}

function updateVolumeLabel() {
  currentVolume = Number(volumeSlider.value);
  volumeValue.textContent = `${Math.round(currentVolume * 100)}%`;

  if (currentAudio) {
    currentAudio.volume = currentVolume;
  }
}

function setVoicePath(pathData) {
  voiceLine.querySelectorAll("path").forEach((path) => {
    path.setAttribute("d", pathData);
  });
}

function setVoiceSpeaking(isSpeaking, pathData = voiceRestPath) {
  window.clearInterval(voiceInterval);
  voiceLine.classList.toggle("is-speaking", isSpeaking);
  setVoicePath(isSpeaking ? pathData : voiceFlatPath);

  if (!isSpeaking) {
    return;
  }

  voiceInterval = window.setInterval(() => {
    voicePathIndex = (voicePathIndex + 1) % voiceSpikePaths.length;
    setVoicePath(voiceSpikePaths[voicePathIndex]);
  }, 120);
}

function wait(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function apiUrl(path) {
  if (!path) {
    return "";
  }

  if (/^https?:\/\//i.test(path)) {
    return path;
  }

  if (path.startsWith("/api")) {
    return `${API_BASE}${path}`;
  }

  return path;
}

async function arthurSpeaks(duration = 1800) {
  window.clearTimeout(speakingTimer);
  setVoiceSpeaking(true);
  speakingTimer = window.setTimeout(() => setVoiceSpeaking(false), duration);
  await wait(duration);
}

async function ensureAudioContext() {
  if (!audioContext) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    audioContext = new AudioContextClass();
  }

  if (audioContext.state === "suspended") {
    await audioContext.resume();
  }

  return audioContext;
}

async function getMicStream() {
  if (micStream) {
    return micStream;
  }

  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("Microphone recording is not supported in this browser.");
  }

  micStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true
    },
    video: false
  });

  return micStream;
}

function mergeAudioBuffers(buffers, totalLength) {
  const merged = new Float32Array(totalLength);
  let offset = 0;

  buffers.forEach((buffer) => {
    merged.set(buffer, offset);
    offset += buffer.length;
  });

  return merged;
}

function downsampleBuffer(buffer, inputSampleRate, outputSampleRate) {
  if (outputSampleRate === inputSampleRate) {
    return buffer;
  }

  const sampleRateRatio = inputSampleRate / outputSampleRate;
  const outputLength = Math.round(buffer.length / sampleRateRatio);
  const output = new Float32Array(outputLength);
  let inputOffset = 0;

  for (let outputIndex = 0; outputIndex < outputLength; outputIndex += 1) {
    const nextInputOffset = Math.round((outputIndex + 1) * sampleRateRatio);
    let accumulator = 0;
    let count = 0;

    for (
      let inputIndex = inputOffset;
      inputIndex < nextInputOffset && inputIndex < buffer.length;
      inputIndex += 1
    ) {
      accumulator += buffer[inputIndex];
      count += 1;
    }

    output[outputIndex] = count ? accumulator / count : 0;
    inputOffset = nextInputOffset;
  }

  return output;
}

function writeAscii(view, offset, text) {
  for (let index = 0; index < text.length; index += 1) {
    view.setUint8(offset + index, text.charCodeAt(index));
  }
}

function encodeWav(samples, sampleRate) {
  const bytesPerSample = 2;
  const channelCount = 1;
  const buffer = new ArrayBuffer(44 + samples.length * bytesPerSample);
  const view = new DataView(buffer);

  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + samples.length * bytesPerSample, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channelCount, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * channelCount * bytesPerSample, true);
  view.setUint16(32, channelCount * bytesPerSample, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, samples.length * bytesPerSample, true);

  let offset = 44;
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    offset += bytesPerSample;
  }

  return new Blob([view], { type: "audio/wav" });
}

async function startSpeechCapture(label) {
  const context = await ensureAudioContext();
  const stream = await getMicStream();
  const source = context.createMediaStreamSource(stream);
  const processor = context.createScriptProcessor(4096, 1, 1);
  const mutedOutput = context.createGain();
  const buffers = [];
  let totalLength = 0;
  let stopped = false;

  mutedOutput.gain.value = 0;
  processor.onaudioprocess = (event) => {
    if (stopped) {
      return;
    }

    const input = event.inputBuffer.getChannelData(0);
    buffers.push(new Float32Array(input));
    totalLength += input.length;
  };

  source.connect(processor);
  processor.connect(mutedOutput);
  mutedOutput.connect(context.destination);
  setStatus(`listening ${label}`);

  return {
    label,
    discard() {
      stopped = true;
      source.disconnect();
      processor.disconnect();
      mutedOutput.disconnect();
    },
    stop() {
      stopped = true;
      source.disconnect();
      processor.disconnect();
      mutedOutput.disconnect();

      const merged = mergeAudioBuffers(buffers, totalLength);
      const downsampled = downsampleBuffer(
        merged,
        context.sampleRate,
        LISTEN_SAMPLE_RATE
      );

      return encodeWav(downsampled, LISTEN_SAMPLE_RATE);
    }
  };
}

function defaultListenResult(label, expected, mode, error = "") {
  return {
    label,
    mode,
    expected,
    recognizedText: "",
    speechDetected: false,
    correct: false,
    scores: {},
    words: [],
    error
  };
}

async function postListenCheck(audioBlob, { label, expected = "", mode = "presence" }) {
  const params = new URLSearchParams({
    expected,
    mode
  });
  const startedAt = performance.now();

  console.info("[Arthur listen] upload", {
    label,
    expected,
    mode,
    bytes: audioBlob.size
  });

  const response = await fetch(`${API_BASE}/api/speech/listen-check?${params}`, {
    method: "POST",
    headers: { "Content-Type": "audio/wav" },
    body: audioBlob
  });

  const elapsedMs = Math.round(performance.now() - startedAt);

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(
      payload.detail || `Listen check failed: ${response.status} after ${elapsedMs}ms`
    );
  }

  const result = await response.json();
  console.info("[Arthur listen] result", {
    label,
    expected,
    mode,
    elapsedMs,
    ...result
  });

  const heard = result.recognizedText || "nothing";
  setStatus(result.speechDetected ? `heard ${heard}` : "no speech heard");
  return result;
}

async function listenForWindow({
  label,
  expected = "",
  mode = "presence",
  durationMs = WORD_LISTEN_MS
}) {
  try {
    const session = await startSpeechCapture(label);
    setSubtitle(`Listening...`);
    await wait(durationMs);
    const audioBlob = session.stop();
    return await postListenCheck(audioBlob, { label, expected, mode });
  } catch (error) {
    console.warn("[Arthur listen]", label, error);
    setStatus("listen unavailable");
    return defaultListenResult(
      label,
      expected,
      mode,
      error instanceof Error ? error.message : String(error)
    );
  }
}

function beginTokenSpeechCapture(index, expected) {
  if (!TOKEN_LISTEN_ENABLED) {
    return;
  }

  if (activeTokenCapture) {
    activeTokenCapture.canceled = true;
    activeTokenCapture.sessionPromise
      .then((session) => session.discard())
      .catch(() => {});
  }

  const label = `token ${index + 1}`;
  const capture = {
    index,
    expected,
    label,
    canceled: false,
    sessionPromise: startSpeechCapture(label)
  };

  activeTokenCapture = capture;
  capture.sessionPromise.catch((error) => {
    console.warn("[Arthur listen]", label, error);
  });
}

function cancelTokenSpeechCapture(index) {
  if (!TOKEN_LISTEN_ENABLED) {
    return;
  }

  const capture = activeTokenCapture;

  if (!capture || capture.index !== index) {
    return;
  }

  activeTokenCapture = null;
  capture.canceled = true;
  capture.sessionPromise
    .then((session) => session.discard())
    .catch(() => {});
}

async function finishTokenSpeechCapture(index, expected) {
  if (!TOKEN_LISTEN_ENABLED) {
    return defaultListenResult(`token ${index + 1}`, expected, "presence");
  }

  const capture = activeTokenCapture;

  if (!capture || capture.index !== index) {
    return defaultListenResult(`token ${index + 1}`, expected, "presence");
  }

  activeTokenCapture = null;

  try {
    const session = await capture.sessionPromise;

    if (capture.canceled) {
      session.discard();
      return defaultListenResult(capture.label, expected, "presence");
    }

    const audioBlob = session.stop();
    return await postListenCheck(audioBlob, {
      label: capture.label,
      expected,
      mode: "presence"
    });
  } catch (error) {
    console.warn("[Arthur listen]", capture.label, error);
    setStatus("listen unavailable");
    return defaultListenResult(
      capture.label,
      expected,
      "presence",
      error instanceof Error ? error.message : String(error)
    );
  }
}

function buildLiveWavePath(timeData, frequencyData) {
  const centerY = 60;
  const startX = 20;
  const endX = 500;
  const points = 42;
  const timeStride = Math.max(1, Math.floor(timeData.length / points));
  const frequencyStride = Math.max(1, Math.floor(frequencyData.length / points));
  const waveGain = 5.8;
  let pathData = `M${startX} ${centerY}`;

  for (let index = 0; index < points; index += 1) {
    const sample = timeData[index * timeStride] || 128;
    const frequency = frequencyData[index * frequencyStride] || 0;
    const timeOffset = ((sample - 128) / 128) * waveGain;
    const frequencyOffset = (frequency / 255) * 0.7;
    const direction = index % 2 === 0 ? 1 : -1;
    const normalized = Math.max(
      -1,
      Math.min(1, timeOffset + direction * frequencyOffset)
    );
    const x = startX + ((endX - startX) * index) / (points - 1);
    const y = centerY + normalized * 54;
    pathData += ` L${x.toFixed(1)} ${y.toFixed(1)}`;
  }

  return pathData;
}

function drawLiveVoice(analyser) {
  window.clearInterval(voiceInterval);
  const timeData = new Uint8Array(analyser.fftSize);
  const frequencyData = new Uint8Array(analyser.frequencyBinCount);

  function draw() {
    analyser.getByteTimeDomainData(timeData);
    analyser.getByteFrequencyData(frequencyData);
    voiceLine.classList.add("is-speaking");
    setVoicePath(buildLiveWavePath(timeData, frequencyData));
    voiceFrame = window.requestAnimationFrame(draw);
  }

  window.cancelAnimationFrame(voiceFrame);
  draw();
}

function stopLiveVoice() {
  window.cancelAnimationFrame(voiceFrame);
  voiceFrame = null;
  setVoiceSpeaking(false);
}

async function fetchDialogueAudio(lineId, variables = {}) {
  setStatus(`requesting ${lineId}`);

  const response = await fetch(`${API_BASE}/api/speech/line`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ line_id: lineId, variables })
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Arthur voice failed: ${response.status}`);
  }

  return {
    blob: await response.blob(),
    text: response.headers.get("X-Arthur-Text") || lineId
  };
}

function activityTokenSounds() {
  return currentActivity.tokens
    .map((token) => token.sound)
    .filter(Boolean);
}

function tokenClipSourcePhrase(token) {
  return `Now say "${currentActivity.word}", but don't say "${token.sound}".`;
}

async function fetchTokenClipAudio(token) {
  if (token.clipUrl) {
    setStatus(`requesting ${token.id || token.sound}`);

    const response = await fetch(apiUrl(token.clipUrl));

    if (!response.ok) {
      throw new Error(`Token clip failed: ${response.status}`);
    }

    return {
      blob: await response.blob(),
      text: token.sound || token.id || "sound"
    };
  }

  setStatus(`requesting clip ${token.sound}`);

  const response = await fetch(`${API_BASE}/api/speech/token-clip`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      token: token.sound,
      tokens: activityTokenSounds(),
      source_phrase: tokenClipSourcePhrase(token),
      occurrence: -1
    })
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Token clip failed: ${response.status}`);
  }

  return {
    blob: await response.blob(),
    text: response.headers.get("X-Arthur-Text") || token.sound
  };
}

async function fetchReplacementClipAudio(token) {
  if (!token.replacementClipUrl) {
    throw new Error("No replacement clip is available for this token.");
  }

  setStatus(`requesting replacement ${token.id || token.sound}`);

  const response = await fetch(apiUrl(token.replacementClipUrl));

  if (!response.ok) {
    throw new Error(`Replacement clip failed: ${response.status}`);
  }

  return {
    blob: await response.blob(),
    text: `${token.sound || token.id || "sound"} replacement`
  };
}

async function playAudioBlob(audioBlob, options = {}) {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }

  const context = await ensureAudioContext();
  const audioUrl = URL.createObjectURL(audioBlob);
  const audio = new Audio(audioUrl);
  const source = context.createMediaElementSource(audio);
  const analyser = context.createAnalyser();

  currentAudio = audio;
  audio.volume = currentVolume;
  audio.playbackRate = options.playbackRate || 1;
  audio.preservesPitch = options.preservesPitch !== false;
  audio.mozPreservesPitch = options.preservesPitch !== false;
  audio.webkitPreservesPitch = options.preservesPitch !== false;
  analyser.fftSize = 1024;
  analyser.smoothingTimeConstant = 0.28;
  source.connect(analyser);
  analyser.connect(context.destination);

  return new Promise((resolve, reject) => {
    audio.onplay = () => {
      setStatus("playing Azure TTS");
      drawLiveVoice(analyser);
      options.onPlaybackStart?.();
    };
    audio.onended = () => {
      URL.revokeObjectURL(audioUrl);
      source.disconnect();
      analyser.disconnect();
      currentAudio = null;
      stopLiveVoice();
      setStatus("idle");
      resolve();
    };
    audio.onerror = () => {
      URL.revokeObjectURL(audioUrl);
      source.disconnect();
      analyser.disconnect();
      currentAudio = null;
      stopLiveVoice();
      setStatus("playback failed");
      reject(new Error("Arthur audio playback failed"));
    };

    audio.play().catch((error) => {
      URL.revokeObjectURL(audioUrl);
      source.disconnect();
      analyser.disconnect();
      currentAudio = null;
      stopLiveVoice();
      setStatus("playback blocked");
      reject(error);
    });
  });
}

async function playArthurLine(lineId, variables = {}, options = {}) {
  try {
    const audio = await fetchDialogueAudio(lineId, variables);
    setSubtitle(audio.text);
    await playAudioBlob(audio.blob, {
      onPlaybackStart: () => options.onPlaybackStart?.(audio.text)
    });
  } catch (error) {
    console.warn(error);
    setSubtitle(error instanceof Error ? error.message : String(error));
    setStatus("fallback animation");
    await arthurSpeaks(1500);
  }
}

async function playTokenClip(token, subtitleText = "", options = {}) {
  const clip = await fetchTokenClipAudio(token);
  setSubtitle(subtitleText || clip.text);
  await playAudioBlob(clip.blob, {
    playbackRate: TOKEN_PLAYBACK_RATE,
    preservesPitch: true,
    ...options
  });
}

async function playReplacementClip(token, subtitleText = "", options = {}) {
  const clip = await fetchReplacementClipAudio(token);
  setSubtitle(subtitleText || clip.text);
  await playAudioBlob(clip.blob, {
    playbackRate: TOKEN_PLAYBACK_RATE,
    preservesPitch: true,
    ...options
  });
}

async function playTokenSound(token) {
  try {
    await playTokenClip(token);
  } catch (error) {
    console.warn(error);
    await playArthurLine("token_sound", tokenVariables(token));
  }
}

function normalizeActivity(activity) {
  const rawTokens = Array.isArray(activity.tokens) && activity.tokens.length
    ? activity.tokens
    : fallbackActivity.tokens;
  const normalized = {
    ...fallbackActivity,
    ...activity,
    tokens: rawTokens.map((token, index) => ({
      id: token.id || `sound${index + 1}`,
      sound: token.sound || token.id || `sound${index + 1}`,
      role: token.role || "match",
      action: token.action || "none",
      ...token
    }))
  };

  return normalized;
}

async function loadCurrentActivity() {
  try {
    const response = await fetch(
      `${API_BASE}/api/activities/current?type=${encodeURIComponent(activityType)}`
    );

    if (!response.ok) {
      throw new Error(`Activity load failed: ${response.status}`);
    }

    currentActivity = normalizeActivity(await response.json());
  } catch (error) {
    console.warn(error);
    currentActivity = fallbackActivity;
  }
}

function updateExerciseUrl() {
  const url = new URL(window.location.href);

  if (activityType === "deletion") {
    url.searchParams.delete("type");
  } else {
    url.searchParams.set("type", activityType);
  }

  window.history.replaceState({}, "", url);
}

async function changeExerciseType(nextType) {
  activityRunId += 1;
  activityType = nextType;
  updateExerciseUrl();
  setSubtitle("Press Start.");
  startButton.disabled = false;

  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }

  stopLiveVoice();
  await loadCurrentActivity();
  buildActivity(false);
}

function tokenElements() {
  return [...tokenRow.querySelectorAll(".token")];
}

function targetElements() {
  return [...dropRow.querySelectorAll(".drop-target")];
}

function buildActivity(isInteractive = true) {
  activityStage.classList.remove("tokens-visible");
  tokenRow.innerHTML = "";
  dropRow.innerHTML = "";
  tokenIndex = 0;
  slidersUnlocked = false;

  currentActivity.tokens.forEach((_, index) => {
    const token = document.createElement("button");
    token.className = "token";
    token.type = "button";
    token.dataset.index = String(index);
    token.dataset.sound = currentActivity.tokens[index].sound || "";
    token.dataset.tokenId = currentActivity.tokens[index].id || "";
    token.dataset.action = currentActivity.tokens[index].action || "none";
    token.setAttribute("aria-label", `Sound slider ${index + 1}`);
    token.addEventListener("pointerdown", handlePointerDown);
    token.addEventListener("pointermove", handlePointerMove);
    token.addEventListener("pointerup", handlePointerUp);
    token.addEventListener("pointercancel", handlePointerUp);
    tokenRow.appendChild(token);

    const target = document.createElement("div");
    target.className = "drop-target";
    target.dataset.index = String(index);
    dropRow.appendChild(target);
  });

  if (isInteractive) {
    updateActiveToken();
    return;
  }

  tokenElements().forEach((token) => {
    token.disabled = true;
  });
}

function updateActiveToken() {
  const tokens = tokenElements();
  const targets = targetElements();

  tokens.forEach((token) => {
    const index = Number(token.dataset.index);
    const isNext = slidersUnlocked && index === tokenIndex;
    const isDone = index < tokenIndex;

    token.classList.toggle("is-ready", isNext);
    token.disabled = !isNext || isDone;
  });

  targets.forEach((target) => {
    const index = Number(target.dataset.index);
    target.classList.toggle("is-ready", slidersUnlocked && index === tokenIndex);
    target.classList.toggle("is-filled", index < tokenIndex);
  });
}

function getCurrentTarget() {
  return dropRow.querySelector(`[data-index="${tokenIndex}"]`);
}

function getTokenMaxY(token) {
  const target = getCurrentTarget();

  if (!target) {
    return 0;
  }

  const tokenRect = token.getBoundingClientRect();
  const targetRect = target.getBoundingClientRect();
  return targetRect.top + targetRect.height / 2 - (tokenRect.top + tokenRect.height / 2);
}

function setTokenY(token, y) {
  token.style.setProperty("--drag-y", `${Math.max(0, y)}px`);
}

function handlePointerDown(event) {
  const token = event.currentTarget;

  if (token.disabled || Number(token.dataset.index) !== tokenIndex) {
    return;
  }

  event.preventDefault();
  activeToken = token;
  activePointerId = event.pointerId;
  activeToken.setPointerCapture(activePointerId);
  activeToken.classList.add("is-dragging", "is-user-active");
  activeToken.dataset.startY = String(event.clientY);
  activeToken.dataset.maxY = String(getTokenMaxY(activeToken));
  activeToken.dataset.currentY = activeToken.dataset.currentY || "0";
  beginTokenSpeechCapture(tokenIndex, token.dataset.sound || "");
}

function handlePointerMove(event) {
  if (!activeToken || event.pointerId !== activePointerId) {
    return;
  }

  const startY = Number(activeToken.dataset.startY);
  const startOffset = Number(activeToken.dataset.currentY || "0");
  const maxY = Number(activeToken.dataset.maxY || "0");
  const y = Math.min(maxY, Math.max(0, startOffset + event.clientY - startY));

  activeToken.dataset.currentY = String(y);
  setTokenY(activeToken, y);
}

function handlePointerUp(event) {
  if (!activeToken || event.pointerId !== activePointerId) {
    return;
  }

  const token = activeToken;
  const y = Number(token.dataset.currentY || "0");
  const maxY = Number(token.dataset.maxY || "0");
  const reachedBottom = maxY > 0 && y >= maxY * 0.92;

  token.classList.remove("is-dragging", "is-user-active");
  activeToken = null;
  activePointerId = null;

  if (!reachedBottom) {
    cancelTokenSpeechCapture(Number(token.dataset.index));
    token.dataset.currentY = "0";
    setTokenY(token, 0);
    return;
  }

  void completeToken(token, maxY);
}

async function completeToken(token, maxY) {
  const completedIndex = Number(token.dataset.index);

  token.disabled = true;
  await finishTokenSpeechCapture(completedIndex, token.dataset.sound || "");
  token.classList.add("is-complete");
  token.classList.remove("is-ready", "is-user-active");
  token.dataset.currentY = String(maxY);
  setTokenY(token, maxY);

  tokenIndex += 1;
  updateActiveToken();

  if (tokenIndex >= currentActivity.tokens.length) {
    void finishActivity();
  }
}

function showTokens() {
  activityStage.classList.add("tokens-visible");
}

function tokenVariables(token) {
  return {
    sound: token.sound
  };
}

function activityVariables() {
  return {
    word: currentActivity.word,
    answer: currentActivity.answer,
    deleteSound: currentActivity.deleteSound || currentActivity.deleteSounds?.[0] || "",
    fromSound: currentActivity.fromSound || currentActivity.replaceFrom || "",
    toSound: currentActivity.toSound || currentActivity.replaceTo || ""
  };
}

function normalizeSound(sound) {
  return String(sound || "").trim().toLowerCase();
}

function getDeletedTokenIndexes() {
  if (Array.isArray(currentActivity.deleteTokenIds) && currentActivity.deleteTokenIds.length) {
    return currentActivity.tokens
      .map((token, index) => currentActivity.deleteTokenIds.includes(token.id) ? index : -1)
      .filter((index) => index >= 0);
  }

  return currentActivity.tokens
    .map((token, index) => token.action === "delete" || token.role === "deletion" ? index : -1)
    .filter((index) => index >= 0);
}

function getSubstitutionTokenIndexes() {
  if (Array.isArray(currentActivity.changeTokenIds) && currentActivity.changeTokenIds.length) {
    return currentActivity.tokens
      .map((token, index) => currentActivity.changeTokenIds.includes(token.id) ? index : -1)
      .filter((index) => index >= 0);
  }

  return currentActivity.tokens
    .map((token, index) => token.action === "substitute" || token.role === "discrepancy" ? index : -1)
    .filter((index) => index >= 0);
}

function findTokenBySound(sound) {
  const target = normalizeSound(sound);

  return currentActivity.tokens.find((token) => {
    return normalizeSound(token.sound) === target;
  });
}

function fadeDeletedTokens() {
  const tokens = tokenElements();

  getDeletedTokenIndexes().forEach((deletedIndex) => {
    const token = tokens[deletedIndex];

    if (token) {
      token.classList.add("is-deleted");
    }
  });
}

function markSubstitutionTokens() {
  const tokens = tokenElements();

  getSubstitutionTokenIndexes().forEach((tokenIndexToChange) => {
    const token = tokens[tokenIndexToChange];

    if (token) {
      token.classList.add("is-substituted");
    }
  });
}

async function playDeletionPrompt() {
  const deletedTokens = getDeletedTokenIndexes()
    .map((index) => currentActivity.tokens[index])
    .filter(Boolean);
  let didFade = false;

  const fadeOnce = () => {
    if (didFade) {
      return;
    }

    didFade = true;
    fadeDeletedTokens();
  };

  if (!deletedTokens.length) {
    fadeOnce();
    return;
  }

  for (const token of deletedTokens) {
    await playArthurLine("deletion_prompt_intro", activityVariables());
    await playTokenClip(token, "", {
      onPlaybackStart: fadeOnce
    });
    await wait(120);
  }
}

async function playSubstitutionPrompt() {
  const changedTokens = getSubstitutionTokenIndexes()
    .map((index) => currentActivity.tokens[index])
    .filter(Boolean);
  let didMark = false;

  const markOnce = () => {
    if (didMark) {
      return;
    }

    didMark = true;
    markSubstitutionTokens();
  };

  if (!changedTokens.length) {
    markOnce();
    return;
  }

  for (const token of changedTokens) {
    await playArthurLine("substitution_prompt_intro", activityVariables());
    await playTokenClip(token);
    await wait(120);
    await playArthurLine("substitution_prompt_to");
    await wait(160);
    await playReplacementClip(token, "", {
      onPlaybackStart: markOnce
    });
    await wait(120);
  }
}

async function demoTokenSound(index) {
  const token = tokenElements()[index];
  const tokenData = currentActivity.tokens[index];

  if (!token || !tokenData) {
    return;
  }

  token.classList.add("is-demo", "is-shaking");
  setTokenY(token, 30);
  await playTokenSound(tokenData);
  await wait(260);
  token.classList.remove("is-demo", "is-shaking");
  setTokenY(token, 0);
}

async function demoAllTokenSounds() {
  for (let index = 0; index < currentActivity.tokens.length; index += 1) {
    await demoTokenSound(index);
    await wait(180);
  }

  slidersUnlocked = true;
  tokenIndex = 0;
  updateActiveToken();
}

async function playActivityDone() {
  const answerToken = findTokenBySound(currentActivity.answer);

  if (answerToken) {
    try {
      await playTokenClip(answerToken, `${currentActivity.answer}! very good.`);
      await wait(180);
      await playArthurLine("very_good");
      return;
    } catch (error) {
      console.warn(error);
    }
  }

  await playArthurLine("activity_done", { answer: currentActivity.answer });
}

async function finishActivity() {
  const runId = activityRunId;
  slidersUnlocked = false;
  updateActiveToken();

  if (currentActivity.type === "deletion") {
    await playDeletionPrompt();
  } else {
    await playSubstitutionPrompt();
  }

  if (runId !== activityRunId) {
    return;
  }

  await wait(350);

  if (runId !== activityRunId) {
    return;
  }

  const finalCheck = await listenForWindow({
    label: "final answer",
    expected: currentActivity.answer,
    mode: "final",
    durationMs: FINAL_LISTEN_MS
  });

  if (runId !== activityRunId) {
    return;
  }

  if (finalCheck.correct) {
    await playActivityDone();
  } else {
    await playArthurLine("answer_correction", {
      ...activityVariables(),
      heard: finalCheck.recognizedText || "nothing"
    });
  }

  startButton.disabled = false;
}

async function startActivity() {
  activityRunId += 1;
  const runId = activityRunId;
  startButton.disabled = true;
  await loadCurrentActivity();
  buildActivity(true);
  // await playArthurLine("welcome");
  await playArthurLine("activity_start", activityVariables());

  if (runId !== activityRunId) {
    return;
  }

  await listenForWindow({
    label: "initial word",
    expected: currentActivity.word,
    mode: "presence",
    durationMs: WORD_LISTEN_MS
  });

  if (runId !== activityRunId) {
    return;
  }

  await playArthurLine("tokens_prompt", activityVariables());

  if (runId !== activityRunId) {
    return;
  }

  showTokens();
  await wait(450);

  if (runId !== activityRunId) {
    return;
  }

  await demoAllTokenSounds();
}

startButton.addEventListener("click", startActivity);
exerciseSelect.addEventListener("change", () => {
  void changeExerciseType(exerciseSelect.value);
});
volumeSlider.addEventListener("input", updateVolumeLabel);

exerciseSelect.value = activityType;
setVoiceSpeaking(false);
void changeExerciseType(activityType);
updateVolumeLabel();
setStatus("idle");
