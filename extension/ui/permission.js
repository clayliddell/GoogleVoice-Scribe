const grantButton = document.getElementById("grant");
const stopButton = document.getElementById("stop");
const deviceSelect = document.getElementById("device");
const levelEl = document.getElementById("level");
const statusEl = document.getElementById("status");

let stream = null;
let audioContext = null;
let analyser = null;
let animationId = null;

init().catch((error) => {
  statusEl.textContent = `Microphone setup failed: ${error.message}`;
});

grantButton.addEventListener("click", () => {
  startMicTest().catch((error) => {
    statusEl.textContent = `Microphone test failed: ${error.message}`;
  });
});

stopButton.addEventListener("click", () => {
  stopMicTest();
  statusEl.textContent = "Microphone test stopped.";
});

deviceSelect.addEventListener("change", async () => {
  await chrome.storage.local.set({ preferredMicDeviceId: deviceSelect.value });
  if (stream) {
    await startMicTest();
  }
});

window.addEventListener("beforeunload", stopMicTest);

async function init() {
  await refreshDevices();
  const permission = await queryMicrophonePermission();
  statusEl.textContent = permission ? `Microphone permission: ${permission}.` : "Click the test button to grant microphone access.";
}

async function startMicTest() {
  stopMicTest();
  statusEl.textContent = "Requesting microphone access...";

  const selectedDeviceId = deviceSelect.value || "";
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: microphoneConstraints(selectedDeviceId),
      video: false
    });

    const track = stream.getAudioTracks()[0] || null;
    await refreshDevices(track?.getSettings()?.deviceId || selectedDeviceId);

    audioContext = new AudioContext();
    const source = audioContext.createMediaStreamSource(stream);
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 2048;
    source.connect(analyser);
    drawMeter();

    statusEl.innerHTML = `Recording will use <code>${escapeHtml(track?.label || "selected microphone")}</code>. Speak now; the meter should move.`;
  } catch (error) {
    stopMicTest();
    throw error;
  }
}

function stopMicTest() {
  if (animationId) {
    cancelAnimationFrame(animationId);
    animationId = null;
  }

  if (audioContext) {
    audioContext.close().catch(() => undefined);
    audioContext = null;
  }

  if (stream) {
    for (const track of stream.getTracks()) {
      track.stop();
    }
    stream = null;
  }

  analyser = null;
  levelEl.style.transform = "scaleX(0)";
}

async function refreshDevices(preferredDeviceId = "") {
  const { preferredMicDeviceId } = await chrome.storage.local.get("preferredMicDeviceId");
  const selected = preferredDeviceId || preferredMicDeviceId || "";
  const devices = await navigator.mediaDevices.enumerateDevices();
  const inputs = devices.filter((device) => device.kind === "audioinput");

  deviceSelect.textContent = "";
  for (const device of inputs) {
    const option = document.createElement("option");
    option.value = device.deviceId;
    option.textContent = device.label || `Microphone ${deviceSelect.length + 1}`;
    deviceSelect.appendChild(option);
  }

  if (inputs.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No microphones found";
    deviceSelect.appendChild(option);
    deviceSelect.disabled = true;
    return;
  }

  deviceSelect.disabled = false;
  if (selected && inputs.some((device) => device.deviceId === selected)) {
    deviceSelect.value = selected;
  }

  await chrome.storage.local.set({ preferredMicDeviceId: deviceSelect.value });
}

function drawMeter() {
  if (!analyser) {
    return;
  }

  const data = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(data);
  let peak = 0;

  for (let i = 0; i < data.length; i += 1) {
    peak = Math.max(peak, Math.abs(data[i] || 0));
  }

  const scale = Math.min(1, peak * 8);
  levelEl.style.transform = `scaleX(${scale})`;
  animationId = requestAnimationFrame(drawMeter);
}

function microphoneConstraints(deviceId) {
  const audio = {
    echoCancellation: false,
    noiseSuppression: false,
    autoGainControl: false,
    channelCount: 1
  };

  if (deviceId) {
    audio.deviceId = { exact: deviceId };
  }

  return audio;
}

async function queryMicrophonePermission() {
  if (!navigator.permissions?.query) {
    return "";
  }

  try {
    const status = await navigator.permissions.query({ name: "microphone" });
    return status.state;
  } catch {
    return "";
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
