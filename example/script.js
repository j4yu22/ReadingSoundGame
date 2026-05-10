import { getRandomExample, buildStagePrompt } from "./activity.js";

const playBtn = document.getElementById("playBtn");
const debugText = document.getElementById("debugText");
const volumeSlider = document.getElementById("volumeSlider");

const SpeechRecognition =
  window.SpeechRecognition || window.webkitSpeechRecognition;

let recognition = null;
let currentExample = null;
let currentStage = 0;
let isRunning = false;


function normalizeText(text) {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^a-z\s]/g, "")
    .replace(/\s+/g, " ");
}


function speakText(text, onEnd = null) {
  window.speechSynthesis.cancel();

  const utterance = new SpeechSynthesisUtterance(text);
  utterance.volume = Number(volumeSlider.value);
  utterance.rate = 0.95;
  utterance.pitch = 1;

  utterance.onend = () => {
    if (onEnd) {
      onEnd();
    }
  };

  window.speechSynthesis.speak(utterance);
}


function updateDebugText(promptText, heardText = "") {
  debugText.textContent = heardText
    ? `${promptText}\n\nHeard: ${heardText}`
    : promptText;
}


function getExpectedResponse() {
  if (!currentExample) {
    return "";
  }

  if (currentStage === 1) {
    return currentExample.activity.original;
  }

  if (currentStage === 2) {
    return currentExample.activity.solution;
  }

  return "";
}

function startStage() {
  const promptText = buildStagePrompt(currentExample, currentStage);

  updateDebugText(promptText);

  listenForStage();
  speakText(promptText);
}

function listenForStage() {
  const promptText = buildStagePrompt(currentExample, currentStage);

  updateDebugText(promptText);

  try {
    recognition.start();
  } catch (error) {
    // prevents errors if recognition is already starting/running
  }
}

function advanceStage() {
  if (currentStage === 1) {
    currentStage = 2;

    startStage();

    return;
  }

  updateDebugText("Complete.");
  speakText("Complete.");

  isRunning = false;
  currentStage = 0;
  currentExample = null;
  playBtn.disabled = false;
}


function handleTranscript(transcript) {
  const promptText = buildStagePrompt(currentExample, currentStage);
  const normalizedHeard = normalizeText(transcript);
  const normalizedExpected = normalizeText(getExpectedResponse());

  updateDebugText(promptText, transcript);

  if (normalizedHeard === normalizedExpected) {
    advanceStage();
    return;
  }

  updateDebugText(`${promptText}\n\nHeard: ${transcript}\n\nTry again.`);

  speakText("Try again.", () => {
    listenForStage();
  });
}


function setupRecognition() {
  if (!SpeechRecognition) {
    debugText.textContent = "Speech recognition is not supported in this browser.";
    playBtn.disabled = true;
    return;
  }

  recognition = new SpeechRecognition();
  recognition.lang = "en-US";
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;

  recognition.onresult = (event) => {
    const transcript = event.results[0][0].transcript;
    handleTranscript(transcript);
  };

  recognition.onerror = () => {
    if (!isRunning) {
      return;
    }

    const promptText = buildStagePrompt(currentExample, currentStage);

    updateDebugText(`${promptText}\n\nMicrophone error. Try again.`);

    setTimeout(() => {
      if (isRunning) {
        listenForStage();
      }
    }, 500);
  };

  recognition.onend = () => {
    if (isRunning) {
        // keep listening until user gets it right
        setTimeout(() => {
        try {
            recognition.start();
        } catch (e) {}
        }, 100);
    }
    };
}


function startExample() {
  if (isRunning) {
    return;
  }

  currentExample = getRandomExample();

  if (!currentExample) {
    debugText.textContent = "No activity found.";
    return;
  }

  isRunning = true;
  currentStage = 1;
  playBtn.disabled = true;

    startStage();
}


playBtn.addEventListener("click", startExample);

setupRecognition();