const SpeechRecognition =
  window.SpeechRecognition || window.webkitSpeechRecognition;

const els = {
  modeText: document.getElementById("modeText"),
  wordOrb: document.getElementById("wordOrb"),
  tokenTrack: document.getElementById("tokenTrack"),
  dropZone: document.getElementById("dropZone"),
  acceptedTokens: document.getElementById("acceptedTokens"),
  startBtn: document.getElementById("startBtn"),
  replayBtn: document.getElementById("replayBtn"),
  resetBtn: document.getElementById("resetBtn"),
  arthurLine: document.getElementById("arthurLine"),
  heardLine: document.getElementById("heardLine"),
  expectedLine: document.getElementById("expectedLine"),
  meterDots: [...document.querySelectorAll(".meter-dot")]
};

const API_BASE =
  window.location.port === "5177" ? "" : "http://127.0.0.1:5177";
const DEFAULT_AZURE_VOICE = "en-US-GuyNeural";
const AZURE_SPEECH_RATE = "-35%";
const BROWSER_SPEECH_RATE = 0.62;
const BROWSER_SPEECH_PITCH = 0.82;
const LISTEN_TIMEOUT_MS = 9000;
const NO_SPEECH_MIN_WAIT_MS = 5500;

let activities = [];
let currentActivity = null;
let currentTokenIndex = 0;
let lastArthurLine = "";
let lastArthurSsml = "";
let recognition = null;
let appConfig = { tts_provider: "browser", tts_ready: false };
let currentAudio = null;
let activeDrag = null;
let dragTranscript = "";
let dragReleased = false;
let dragDropped = false;
let dragStartedAt = 0;

function normalizeText(text) {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^a-z\s]/g, "")
    .replace(/\s+/g, " ");
}

function setMode(message) {
  els.modeText.textContent = message;
}

function setDev({ arthur, heard, expected } = {}) {
  if (arthur !== undefined) {
    els.arthurLine.textContent = arthur || "...";
  }

  if (heard !== undefined) {
    els.heardLine.textContent = heard || "...";
  }

  if (expected !== undefined) {
    els.expectedLine.textContent = expected || "...";
  }
}

function setProgress(index) {
  els.meterDots.forEach((dot, dotIndex) => {
    dot.classList.toggle("is-current", dotIndex === index);
    dot.classList.toggle("is-done", dotIndex < index);
  });
}

async function apiFetch(path, options = {}) {
  return fetch(`${API_BASE}${path}`, options);
}

function wait(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

async function waitForNoSpeechMinimum(startedAt) {
  const remaining = Math.max(0, NO_SPEECH_MIN_WAIT_MS - (Date.now() - startedAt));

  if (remaining > 0) {
    await wait(remaining);
  }
}

function escapeXml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function wrapSlowSsml(innerSsml) {
  const voiceName = appConfig.tts_voice || DEFAULT_AZURE_VOICE;

  return (
    '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" ' +
    'xml:lang="en-US">' +
    `<voice name="${escapeXml(voiceName)}">` +
    `<prosody rate="${AZURE_SPEECH_RATE}" pitch="-6%">` +
    innerSsml +
    "</prosody></voice></speak>"
  );
}

function textToSlowSsml(text) {
  return wrapSlowSsml(escapeXml(text));
}

function wordBreak(ms = 650) {
  return `<break time="${ms}ms"/>`;
}

function emphasized(text) {
  return `<emphasis level="moderate">${escapeXml(text)}</emphasis>`;
}

function sayWordSsml(word, leadText = "Say this word.") {
  return wrapSlowSsml(`${escapeXml(leadText)}${wordBreak()}${emphasized(word)}.`);
}

function tokenInstructionSsml(word) {
  return wrapSlowSsml(
    `Now pull down a token for each sound in ${wordBreak(450)}${emphasized(word)}.`
  );
}

function soundSsml(sound, leadText) {
  return wrapSlowSsml(`${escapeXml(leadText)}${wordBreak(500)}${emphasized(sound)}.`);
}

function omitPromptSsml(word, omitSound) {
  return wrapSlowSsml(
    `Now say ${wordBreak(450)}${emphasized(word)}.${wordBreak(350)}` +
      `But don't say ${wordBreak(450)}${emphasized(omitSound)}.`
  );
}

function finalCorrectionSsml(word, omitSound, answer, leadText) {
  return wrapSlowSsml(
    `${escapeXml(leadText)}${wordBreak(350)}` +
      `${emphasized(word)} without ${emphasized(omitSound)} is ${wordBreak(450)}` +
      `${emphasized(answer)}.`
  );
}

function getArthurVoice() {
  const voices = window.speechSynthesis.getVoices();
  const preferredNames = [
    "Microsoft David",
    "Microsoft Mark",
    "Microsoft Guy",
    "Google US English",
    "Alex",
    "Daniel"
  ];

  return (
    preferredNames
      .map((name) => voices.find((voice) => voice.name.includes(name)))
      .find(Boolean) ||
    voices.find((voice) => voice.lang && voice.lang.startsWith("en")) ||
    null
  );
}

function speakWithBrowser(text) {
  return new Promise((resolve) => {
    const utterance = new SpeechSynthesisUtterance(text);
    const voice = getArthurVoice();

    if (voice) {
      utterance.voice = voice;
    }

    utterance.volume = 1;
    utterance.rate = BROWSER_SPEECH_RATE;
    utterance.pitch = BROWSER_SPEECH_PITCH;

    utterance.onend = resolve;
    utterance.onerror = resolve;

    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
  });
}

async function speakWithServer(text, ssml = "") {
  const response = await apiFetch("/api/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, ssml: ssml || textToSlowSsml(text) })
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "Server TTS failed");
  }

  const audioBlob = await response.blob();
  const audioUrl = URL.createObjectURL(audioBlob);

  return new Promise((resolve, reject) => {
    currentAudio = new Audio(audioUrl);
    currentAudio.volume = 1;
    currentAudio.onended = () => {
      URL.revokeObjectURL(audioUrl);
      currentAudio = null;
      resolve();
    };
    currentAudio.onerror = () => {
      URL.revokeObjectURL(audioUrl);
      currentAudio = null;
      reject(new Error("Azure audio playback failed"));
    };
    currentAudio.play().catch(() => {
      URL.revokeObjectURL(audioUrl);
      currentAudio = null;
      reject(new Error("Azure audio playback was blocked"));
    });
  });
}

async function speakText(text, ssml = "") {
  lastArthurLine = text;
  lastArthurSsml = ssml;
  setMode("Arthur speaking");
  setDev({ arthur: text });
  els.wordOrb.classList.add("is-speaking");

  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
  window.speechSynthesis.cancel();

  try {
    if (appConfig.tts_provider !== "browser" && appConfig.tts_ready) {
      await speakWithServer(text, ssml);
      return;
    }

    await speakWithBrowser(text);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    setMode(`Browser voice fallback: ${message}`);
    await speakWithBrowser(text);
  } finally {
    els.wordOrb.classList.remove("is-speaking");
  }
}

function transcriptFromEvent(event) {
  let transcript = "";

  for (let i = 0; i < event.results.length; i += 1) {
    transcript += event.results[i][0].transcript;
    transcript += " ";
  }

  return transcript.trim();
}

function matchesAny(transcript, accepted) {
  const heard = normalizeText(transcript);

  if (!heard) {
    return false;
  }

  return accepted.some((answer) => {
    const expected = normalizeText(answer);
    return heard === expected || heard.includes(expected);
  });
}

function listenOnce(accepted, expectedLabel, timeoutMs = LISTEN_TIMEOUT_MS) {
  return new Promise((resolve) => {
    if (!SpeechRecognition) {
      setMode("Speech recognition unavailable");
      resolve({ ok: false, transcript: "" });
      return;
    }

    let transcript = "";
    let settled = false;
    let settleTimer = null;
    const startedAt = Date.now();
    const localRecognition = new SpeechRecognition();

    recognition = localRecognition;
    setMode("Listening");
    setDev({ heard: "...", expected: expectedLabel });

    localRecognition.lang = "en-US";
    localRecognition.continuous = false;
    localRecognition.interimResults = false;
    localRecognition.maxAlternatives = 1;

    const timeout = window.setTimeout(() => {
      try {
        localRecognition.stop();
      } catch (error) {
        // Browser speech recognition can already be stopped here.
      }
    }, timeoutMs);

    function finish() {
      if (settled) {
        return;
      }

      settled = true;
      window.clearTimeout(timeout);
      window.clearTimeout(settleTimer);
      setDev({ heard: transcript || "No speech" });
      resolve({
        ok: matchesAny(transcript, accepted),
        transcript
      });
    }

    function settle() {
      if (settled) {
        return;
      }

      const elapsed = Date.now() - startedAt;
      const waitBeforeNoSpeech = transcript
        ? 0
        : Math.max(0, NO_SPEECH_MIN_WAIT_MS - elapsed);

      if (waitBeforeNoSpeech > 0) {
        if (!settleTimer) {
          settleTimer = window.setTimeout(finish, waitBeforeNoSpeech);
        }
        return;
      }

      finish();
    }

    localRecognition.onresult = (event) => {
      transcript = transcriptFromEvent(event);
    };

    localRecognition.onerror = () => {
      settle();
    };

    localRecognition.onend = () => {
      settle();
    };

    try {
      localRecognition.start();
    } catch (error) {
      settle();
    }
  });
}

function resetStage() {
  window.speechSynthesis.cancel();

  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }

  if (recognition) {
    try {
      recognition.stop();
    } catch (error) {
      // Recognition may not be running.
    }
  }

  currentActivity = null;
  currentTokenIndex = 0;
  activeDrag = null;
  dragTranscript = "";
  dragReleased = false;
  dragDropped = false;
  dragStartedAt = 0;

  els.tokenTrack.innerHTML = "";
  els.acceptedTokens.innerHTML = "";
  els.wordOrb.classList.remove("is-hidden", "is-speaking");
  els.dropZone.classList.remove("is-ready");
  els.startBtn.disabled = false;
  setProgress(0);
  setMode("Ready");
  setDev({ arthur: "...", heard: "...", expected: "..." });
}

function randomActivity() {
  const index = Math.floor(Math.random() * activities.length);
  return activities[index];
}

function createToken(index) {
  const token = document.createElement("button");
  token.className = "token";
  token.type = "button";
  token.dataset.index = String(index);
  token.setAttribute("aria-label", `Sound token ${index + 1}`);

  token.addEventListener("pointerdown", handleTokenPointerDown);
  token.addEventListener("pointermove", handleTokenPointerMove);
  token.addEventListener("pointerup", handleTokenPointerUp);
  token.addEventListener("pointercancel", handleTokenPointerUp);

  return token;
}

function renderTokens() {
  els.tokenTrack.innerHTML = "";
  els.acceptedTokens.innerHTML = "";

  currentActivity.tokens.forEach((_, index) => {
    els.tokenTrack.appendChild(createToken(index));
  });

  activateCurrentToken();
}

function activateCurrentToken() {
  [...els.tokenTrack.querySelectorAll(".token")].forEach((token) => {
    const isActive = Number(token.dataset.index) === currentTokenIndex;
    token.classList.toggle("is-active", isActive);
    token.disabled = !isActive;
  });

  els.dropZone.classList.add("is-ready");
}

function isInsideDropZone(x, y) {
  const rect = els.dropZone.getBoundingClientRect();
  return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
}

function startDragRecognition() {
  if (!SpeechRecognition) {
    return;
  }

  const localRecognition = new SpeechRecognition();
  recognition = localRecognition;
  dragTranscript = "";

  localRecognition.lang = "en-US";
  localRecognition.continuous = false;
  localRecognition.interimResults = false;
  localRecognition.maxAlternatives = 1;

  localRecognition.onresult = (event) => {
    dragTranscript = transcriptFromEvent(event);
    setDev({ heard: dragTranscript });
  };

  localRecognition.onerror = () => {};

  localRecognition.onend = () => {
    window.setTimeout(evaluateDrag, 80);
  };

  try {
    localRecognition.start();
  } catch (error) {
    // Start can fail if the browser has not finished closing a prior session.
  }
}

function handleTokenPointerDown(event) {
  const token = event.currentTarget;

  if (token.disabled || !currentActivity) {
    return;
  }

  event.preventDefault();
  token.setPointerCapture(event.pointerId);

  const rect = token.getBoundingClientRect();
  activeDrag = {
    token,
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    originX: rect.left + rect.width / 2,
    originY: rect.top + rect.height / 2
  };
  dragReleased = false;
  dragDropped = false;
  dragStartedAt = Date.now();

  const expected = currentActivity.tokens[currentTokenIndex].voice;
  setMode("Listening");
  setDev({ heard: "...", expected });
  token.classList.add("is-dragging");
  startDragRecognition();
}

function handleTokenPointerMove(event) {
  if (!activeDrag || event.pointerId !== activeDrag.pointerId) {
    return;
  }

  const x = event.clientX - activeDrag.startX;
  const y = event.clientY - activeDrag.startY;

  activeDrag.token.style.setProperty("--drag-x", `${x}px`);
  activeDrag.token.style.setProperty("--drag-y", `${y}px`);
}

function handleTokenPointerUp(event) {
  if (!activeDrag || event.pointerId !== activeDrag.pointerId) {
    return;
  }

  const token = activeDrag.token;
  dragReleased = true;
  dragDropped = isInsideDropZone(event.clientX, event.clientY);

  token.classList.remove("is-dragging");

  if (recognition) {
    try {
      recognition.stop();
    } catch (error) {
      // Recognition may already be stopped.
    }
  }

  window.setTimeout(evaluateDrag, 360);
}

async function evaluateDrag() {
  if (!activeDrag || !dragReleased) {
    return;
  }

  const token = activeDrag.token;
  const tokenData = currentActivity.tokens[currentTokenIndex];
  const isCorrect = dragDropped && matchesAny(dragTranscript, tokenData.accept);
  const hadSpeech = Boolean(normalizeText(dragTranscript));

  activeDrag = null;

  if (!isCorrect) {
    if (!hadSpeech) {
      await waitForNoSpeechMinimum(dragStartedAt);
    }

    token.classList.add("is-missed");
    token.style.setProperty("--drag-x", "0px");
    token.style.setProperty("--drag-y", "0px");

    window.setTimeout(() => {
      token.classList.remove("is-missed");
    }, 240);

    await speakText(
      `Follow along. This sound is "${tokenData.voice}".`,
      soundSsml(tokenData.voice, "Follow along. This sound is")
    );
    setMode("Pull the token");
    activateCurrentToken();
    return;
  }

  token.classList.remove("is-active");
  token.classList.add("is-correct");
  token.style.removeProperty("--drag-x");
  token.style.removeProperty("--drag-y");
  token.disabled = true;
  els.acceptedTokens.appendChild(token);

  currentTokenIndex += 1;

  if (currentTokenIndex < currentActivity.tokens.length) {
    activateCurrentToken();
    setMode("Pull the token");
    return;
  }

  els.dropZone.classList.remove("is-ready");
  await beginFinalAnswer();
}

async function askForOriginalWord() {
  setProgress(0);
  els.wordOrb.classList.remove("is-hidden");

  await speakText(
    `Say "${currentActivity.word}".`,
    sayWordSsml(currentActivity.word)
  );
  await listenForOriginalWord();
}

async function listenForOriginalWord() {
  const result = await listenOnce(
    currentActivity.wordAccept,
    currentActivity.word
  );

  if (!result.ok) {
    const correction = result.transcript
      ? `Follow along. Say "${currentActivity.word}".`
      : `I didn't hear anything. Say "${currentActivity.word}".`;
    const leadText = result.transcript
      ? "Follow along. Say this word."
      : "I didn't hear anything. Say this word.";
    await speakText(correction, sayWordSsml(currentActivity.word, leadText));
    return listenForOriginalWord();
  }

  await beginTokenPhase();
}

async function beginTokenPhase() {
  setProgress(1);
  els.wordOrb.classList.add("is-hidden");
  renderTokens();

  await speakText(
    `Now pull down a token for each sound in "${currentActivity.word}".`,
    tokenInstructionSsml(currentActivity.word)
  );
  setMode("Pull the token");
}

function removeOmittedToken() {
  const tokenToRemove = [...els.acceptedTokens.querySelectorAll(".token")].find(
    (token) => Number(token.dataset.index) === currentActivity.omitIndex
  );

  if (!tokenToRemove) {
    return Promise.resolve();
  }

  tokenToRemove.classList.add("is-vanishing");

  return new Promise((resolve) => {
    window.setTimeout(() => {
      tokenToRemove.remove();
      resolve();
    }, 420);
  });
}

async function beginFinalAnswer() {
  setProgress(2);
  const omitSound = currentActivity.tokens[currentActivity.omitIndex].voice;

  await removeOmittedToken();
  await speakText(
    `Now say "${currentActivity.word}", but don't say "${omitSound}".`,
    omitPromptSsml(currentActivity.word, omitSound)
  );
  await listenForFinalAnswer(omitSound);
}

async function listenForFinalAnswer(omitSound) {
  const result = await listenOnce(
    currentActivity.answerAccept,
    currentActivity.answer
  );

  if (!result.ok) {
    const correction = result.transcript
      ? `Follow along. "${currentActivity.word}" without "${omitSound}" is "${currentActivity.answer}".`
      : `I didn't hear anything. "${currentActivity.word}" without "${omitSound}" is "${currentActivity.answer}".`;
    const leadText = result.transcript ? "Follow along." : "I didn't hear anything.";
    await speakText(
      correction,
      finalCorrectionSsml(
        currentActivity.word,
        omitSound,
        currentActivity.answer,
        leadText
      )
    );
    return listenForFinalAnswer(omitSound);
  }

  await speakText("Good job.");
  setProgress(3);
  setMode("Complete");
  els.startBtn.disabled = false;
}

async function startActivity() {
  if (!activities.length) {
    setMode("No activities found");
    return;
  }

  resetStage();
  els.startBtn.disabled = true;
  currentActivity = randomActivity();
  await askForOriginalWord();
}

async function loadActivities() {
  try {
    const response = await fetch("activity.json");

    if (!response.ok) {
      throw new Error("activity.json failed to load");
    }

    const data = await response.json();
    activities = data.activities || [];
  } catch (error) {
    setMode("Could not load activity data");
  }
}

async function loadConfig() {
  try {
    const response = await apiFetch("/api/config");

    if (!response.ok) {
      throw new Error("Arthur backend offline");
    }

    appConfig = await response.json();
    const voiceLabel =
      appConfig.tts_provider === "browser"
        ? "Browser voice"
        : `${appConfig.tts_voice || "Azure voice"}`;
    setMode(appConfig.tts_ready ? `Ready: ${voiceLabel}` : "Ready");
  } catch {
    appConfig = { tts_provider: "browser", tts_ready: false };
    setMode("Ready: browser voice fallback");
  }
}

els.startBtn.addEventListener("click", startActivity);

els.replayBtn.addEventListener("click", () => {
  if (lastArthurLine) {
    speakText(lastArthurLine, lastArthurSsml);
  }
});

els.resetBtn.addEventListener("click", resetStage);

window.speechSynthesis.onvoiceschanged = () => {
  getArthurVoice();
};

resetStage();
loadActivities();
loadConfig();
