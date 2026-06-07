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
      sound: "sail"
    },
    {
      sound: "boat"
    }
  ],
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

async function playTokenClip(token, subtitleText = "") {
  const clip = await fetchTokenClipAudio(token);
  setSubtitle(subtitleText || clip.text);
  await playAudioBlob(clip.blob);
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
  const normalized = {
    ...fallbackActivity,
    ...activity,
    tokens: Array.isArray(activity.tokens) && activity.tokens.length
      ? activity.tokens
      : fallbackActivity.tokens
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
    token.dataset.currentY = "0";
    setTokenY(token, 0);
    return;
  }

  completeToken(token, maxY);
}

function completeToken(token, maxY) {
  token.classList.add("is-complete");
  token.classList.remove("is-ready");
  token.disabled = true;
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
    deleteSound: currentActivity.deleteSound || currentActivity.replaceFrom,
    fromSound: currentActivity.replaceFrom,
    toSound: currentActivity.replaceTo
  };
}

function getFinishPromptLineId() {
  return currentActivity.type === "substitution"
    ? "substitution_prompt"
    : "deletion_prompt";
}

function normalizeSound(sound) {
  return String(sound || "").trim().toLowerCase();
}

function getDeletedTokenIndex() {
  const deleteSound = normalizeSound(currentActivity.deleteSound);

  if (!deleteSound) {
    return -1;
  }

  return currentActivity.tokens.findIndex((token) => {
    return normalizeSound(token.sound) === deleteSound;
  });
}

function getSubstitutionTokenIndex() {
  const replaceFrom = normalizeSound(currentActivity.replaceFrom);

  if (!replaceFrom) {
    return -1;
  }

  return currentActivity.tokens.findIndex((token) => {
    return normalizeSound(token.sound) === replaceFrom;
  });
}

function findTokenBySound(sound) {
  const target = normalizeSound(sound);

  return currentActivity.tokens.find((token) => {
    return normalizeSound(token.sound) === target;
  });
}

function fadeDeletedToken() {
  const deletedIndex = getDeletedTokenIndex();
  const token = tokenElements()[deletedIndex];

  if (token) {
    token.classList.add("is-deleted");
  }
}

function markSubstitutionToken() {
  const tokenIndexToChange = getSubstitutionTokenIndex();
  const token = tokenElements()[tokenIndexToChange];

  if (token) {
    token.classList.add("is-substituted");
  }
}

function estimateSoundDelay(spokenText, sound, fallbackDelay = 2600) {
  const targetSound = normalizeSound(sound);
  const text = String(spokenText || "").toLowerCase();
  const soundIndex = text.lastIndexOf(targetSound);

  if (!targetSound || soundIndex < 0) {
    return fallbackDelay;
  }

  const textBeforeSound = spokenText.slice(0, soundIndex);
  const wordsBeforeSound = textBeforeSound.match(/[a-z0-9']+/gi) || [];
  const punctuationBeforeSound = textBeforeSound.match(/[,.!?;]/g) || [];
  const quoteMarksBeforeSound = textBeforeSound.match(/"/g) || [];

  return Math.min(
    4200,
    Math.max(
      1700,
      wordsBeforeSound.length * 330
        + punctuationBeforeSound.length * 230
        + quoteMarksBeforeSound.length * 60
    )
  );
}

function estimateDeletionFadeDelay(spokenText) {
  return estimateSoundDelay(spokenText, currentActivity.deleteSound, 2600);
}

async function playDeletionPrompt() {
  let didFade = false;
  let fadeTimer = null;
  const fadeOnce = () => {
    if (didFade) {
      return;
    }

    didFade = true;
    fadeDeletedToken();
  };

  await playArthurLine("deletion_prompt", activityVariables(), {
    onPlaybackStart: (spokenText) => {
      fadeTimer = window.setTimeout(
        fadeOnce,
        estimateDeletionFadeDelay(spokenText)
      );
    }
  });

  if (fadeTimer) {
    window.clearTimeout(fadeTimer);
  }

  fadeOnce();
}

async function playSubstitutionPrompt() {
  let didChange = false;
  let changeTimer = null;
  const changeOnce = () => {
    if (didChange) {
      return;
    }

    didChange = true;
    markSubstitutionToken();
  };

  await playArthurLine("substitution_prompt", activityVariables(), {
    onPlaybackStart: (spokenText) => {
      changeTimer = window.setTimeout(
        changeOnce,
        estimateSoundDelay(spokenText, currentActivity.replaceFrom, 2400)
      );
    }
  });

  if (changeTimer) {
    window.clearTimeout(changeTimer);
  }

  changeOnce();
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

  if (getFinishPromptLineId() === "deletion_prompt") {
    await playDeletionPrompt();
  } else {
    await playSubstitutionPrompt();
  }

  if (runId !== activityRunId) {
    return;
  }

  await wait(1400);

  if (runId !== activityRunId) {
    return;
  }

  await playActivityDone();
  startButton.disabled = false;
}

async function startActivity() {
  activityRunId += 1;
  const runId = activityRunId;
  startButton.disabled = true;
  await loadCurrentActivity();
  buildActivity(true);
  // await playArthurLine("welcome");
  showTokens();
  await playArthurLine("activity_start", activityVariables());

  if (runId !== activityRunId) {
    return;
  }

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
