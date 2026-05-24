const voiceSelect = document.getElementById("voiceSelect");
const rateSlider = document.getElementById("rateSlider");
const pitchSlider = document.getElementById("pitchSlider");
const voicePreviewText = document.getElementById("voicePreviewText");
const previewVoiceBtn = document.getElementById("previewVoiceBtn");
const ttsInput = document.getElementById("ttsInput");
const sttOutput = document.getElementById("sttOutput");
const listenBtn = document.getElementById("listenBtn");
const volumeValue = document.getElementById("volumeValue");
const rateValue = document.getElementById("rateValue");
const pitchValue = document.getElementById("pitchValue");
const statusText = document.getElementById("status");


const SpeechRecognition =
  window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let isListening = false;
let voices = [];
const volumeSlider = document.getElementById("volumeSlider");
speechSynthesis.onvoiceschanged = () => {
  voices = speechSynthesis.getVoices();
};


function setStatus(message) {
  /**
   * Updates the visible app status text.
   *
   * Parameters:
   *        message (string): Status message to display
   *
   * Returns:
   *        None
   */
  statusText.textContent = `Status: ${message}`;
}


function speakText(text) {
  /**
   * Speaks the provided text using the user-selected voice settings.
   *
   * Parameters:
   *        text (string): Text to be spoken aloud
   *
   * Returns:
   *        None
   */
  const cleanedText = text.trim();

  if (!cleanedText) {
    return;
  }

  const utterance = new SpeechSynthesisUtterance(cleanedText);
  const selectedVoice = getSelectedVoice();

  if (selectedVoice) {
    utterance.voice = selectedVoice;
  }

  utterance.volume = Number(volumeSlider.value);
  utterance.rate = Number(rateSlider.value);
  utterance.pitch = Number(pitchSlider.value);

  utterance.onstart = () => {
    setStatus("speaking");
  };

  utterance.onend = () => {
    setStatus(isListening ? "listening" : "idle");
  };

  utterance.onerror = () => {
    setStatus("tts error");
  };

  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
}

function setupRecognition() {
  /**
   * Creates and configures the browser speech recognition instance.
   *
   * Parameters:
   *        None
   *
   * Returns:
   *        None
   */
  if (!SpeechRecognition) {
    sttOutput.textContent = "Speech recognition is not supported in this browser.";
    listenBtn.disabled = true;
    setStatus("speech recognition unsupported");
    return;
  }

  recognition = new SpeechRecognition();
  recognition.lang = "en-US";
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;

  recognition.onstart = () => {
    isListening = true;
    listenBtn.textContent = "Stop Listening";
    setStatus("listening");
  };

  recognition.onresult = (event) => {
    let transcript = "";

    for (let i = 0; i < event.results.length; i += 1) {
      transcript += event.results[i][0].transcript;

      if (!event.results[i].isFinal) {
        transcript += " ";
      }
    }

    sttOutput.textContent = transcript.trim() || "Listening...";
  };

  recognition.onerror = (event) => {
    setStatus(`recognition error: ${event.error}`);
  };

  recognition.onend = () => {
    isListening = false;
    listenBtn.textContent = "Start Listening";
    setStatus("idle");
  };
}

volumeSlider.addEventListener("input", () => {
  volumeValue.textContent = Number(volumeSlider.value).toFixed(2);
});

rateSlider.addEventListener("input", () => {
  rateValue.textContent = Number(rateSlider.value).toFixed(2);
});

pitchSlider.addEventListener("input", () => {
  pitchValue.textContent = Number(pitchSlider.value).toFixed(2);
});

ttsInput.addEventListener("keydown", (event) => {
  if (event.key !== "Enter") {
    return;
  }

  speakText(ttsInput.value);
});


listenBtn.addEventListener("click", () => {
  if (!recognition) {
    return;
  }

  if (isListening) {
    recognition.stop();
    return;
  }

  sttOutput.textContent = "Listening...";
  recognition.start();
});

function populateVoiceList() {
  /**
   * Loads available speech synthesis voices into the dropdown.
   *
   * Parameters:
   *        None
   *
   * Returns:
   *        None
   */
  voices = speechSynthesis.getVoices();

  voiceSelect.innerHTML = "";

  voices.forEach((voice, index) => {
    const option = document.createElement("option");
    option.value = index;
    option.textContent = `${voice.name} (${voice.lang})`;
    voiceSelect.appendChild(option);
  });

  const preferredIndex = voices.findIndex(
    (voice) =>
      voice.name.includes("Google") ||
      voice.name.includes("Microsoft") ||
      voice.name.includes("Natural")
  );

  if (preferredIndex >= 0) {
    voiceSelect.value = String(preferredIndex);
  }
}


function getSelectedVoice() {
  /**
   * Returns the currently selected speech synthesis voice.
   *
   * Parameters:
   *        None
   *
   * Returns:
   *        voice (SpeechSynthesisVoice | null): Selected voice or null if unavailable
   */
  const selectedIndex = Number(voiceSelect.value);

  if (Number.isNaN(selectedIndex) || !voices[selectedIndex]) {
    return null;
  }

  return voices[selectedIndex];
}

speechSynthesis.onvoiceschanged = () => {
  populateVoiceList();
};

previewVoiceBtn.addEventListener("click", () => {
  speakText(voicePreviewText.value);
});

setupRecognition();
populateVoiceList();