let current = null;

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.target !== "offscreen") {
    return false;
  }

  handleMessage(message)
    .then((response) => sendResponse(response ?? { ok: true }))
    .catch((error) => {
      console.error("Offscreen recorder error", error);
      chrome.runtime.sendMessage({
        type: "GV_OFFSCREEN_ERROR",
        sessionId: message.sessionId,
        tabId: message.tabId,
        error: error.message || String(error)
      }).catch(() => undefined);
      sendResponse({ ok: false, error: error.message || String(error) });
    });

  return true;
});

async function handleMessage(message) {
  switch (message.type) {
    case "GV_CHECK_MICROPHONE":
      return checkMicrophone(message);

    case "GV_START_RECORDING":
      return startRecording(message);

    case "GV_STOP_RECORDING":
      return stopRecording(message.reason || "stop_requested");

    default:
      return { ok: false, error: `Unhandled offscreen message: ${message.type}` };
  }
}

async function checkMicrophone(options) {
  let stream = null;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: microphoneConstraints(options.preferredMicDeviceId),
      video: false
    });
    const track = stream.getAudioTracks()[0] || null;
    return {
      ok: Boolean(track),
      trackLabel: track?.label || ""
    };
  } catch (error) {
    return {
      ok: false,
      error: error.message || String(error)
    };
  } finally {
    stopTracks(stream);
  }
}

async function startRecording(options) {
  if (current) {
    return { ok: false, error: "Recorder is already active." };
  }

  const tabStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      mandatory: {
        chromeMediaSource: "tab",
        chromeMediaSourceId: options.streamId
      }
    },
    video: false
  });

  let micStream = null;
  let micError = null;

  if (options.captureMicrophone) {
    try {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: microphoneConstraints(options.preferredMicDeviceId),
        video: false
      });
    } catch (error) {
      micError = error.message || String(error);
      chrome.runtime.sendMessage({
        type: "GV_OFFSCREEN_WARNING",
        warning: "microphone_unavailable",
        error: micError,
        sessionId: options.sessionId,
        tabId: options.tabId
      }).catch(() => undefined);
    }
  }

  const audioContext = new AudioContext({ latencyHint: "interactive" });
  const silentOutput = audioContext.createGain();
  silentOutput.gain.value = 0;
  silentOutput.connect(audioContext.destination);

  const tabSource = audioContext.createMediaStreamSource(tabStream);
  const tabGain = audioContext.createGain();
  const tabAnalyser = audioContext.createAnalyser();
  tabGain.gain.value = 1;
  tabAnalyser.fftSize = 2048;
  tabSource.connect(tabGain);
  tabGain.connect(tabAnalyser);
  tabGain.connect(audioContext.destination);

  const mixGain = audioContext.createGain();
  const mixAnalyser = audioContext.createAnalyser();
  mixGain.gain.value = 1;
  mixAnalyser.fftSize = 2048;
  tabGain.connect(mixGain);
  mixGain.connect(mixAnalyser);

  let micSource = null;
  let micGain = null;
  let micAnalyser = null;
  if (micStream) {
    micSource = audioContext.createMediaStreamSource(micStream);
    micGain = audioContext.createGain();
    micAnalyser = audioContext.createAnalyser();
    micGain.gain.value = 1;
    micAnalyser.fftSize = 2048;
    micSource.connect(micGain);
    micGain.connect(micAnalyser);
    micGain.connect(mixGain);
  }

  const mixedProcessor = createTrackProcessor(audioContext);
  const calleeProcessor = createTrackProcessor(audioContext);
  const micProcessor = micStream ? createTrackProcessor(audioContext) : null;

  mixGain.connect(mixedProcessor);
  mixedProcessor.connect(silentOutput);
  tabGain.connect(calleeProcessor);
  calleeProcessor.connect(silentOutput);
  if (micGain && micProcessor) {
    micGain.connect(micProcessor);
    micProcessor.connect(silentOutput);
  }

  const state = {
    audioContext,
    tabStream,
    micStream,
    tabSource,
    tabGain,
    tabAnalyser,
    micSource,
    micGain,
    micAnalyser,
    mixGain,
    mixAnalyser,
    processors: {
      mixed: mixedProcessor,
      callee: calleeProcessor,
      mic: micProcessor
    },
    silentOutput,
    serviceUrl: options.serviceUrl.replace(/\/+$/, ""),
    sessionId: options.sessionId,
    tabId: options.tabId,
    sampleRate: audioContext.sampleRate,
    trackSequences: {
      mixed: 0,
      callee: 0,
      mic: 0
    },
    trackDroppedChunks: {
      mixed: 0,
      callee: 0,
      mic: 0
    },
    trackUploadErrors: {
      mixed: [],
      callee: [],
      mic: []
    },
    micCaptured: Boolean(micStream),
    micError,
    micTrackLabel: micStream?.getAudioTracks()[0]?.label || "",
    tabPeak: 0,
    micPeak: 0,
    mixedPeak: 0,
    uploadChain: Promise.resolve(),
    stopped: false
  };

  mixedProcessor.onaudioprocess = (event) => handleAudioProcess(state, "mixed", event);
  calleeProcessor.onaudioprocess = (event) => handleAudioProcess(state, "callee", event);
  if (micProcessor) {
    micProcessor.onaudioprocess = (event) => handleAudioProcess(state, "mic", event);
  }

  current = state;

  await chrome.runtime.sendMessage({
    type: "GV_OFFSCREEN_STARTED",
    sessionId: state.sessionId,
    tabId: state.tabId,
    sampleRate: state.sampleRate,
    micCaptured: state.micCaptured,
    micTrackLabel: state.micTrackLabel
  }).catch(() => undefined);

  return {
    ok: true,
    sessionId: state.sessionId,
    sampleRate: state.sampleRate,
    micCaptured: state.micCaptured,
    micError,
    micTrackLabel: state.micTrackLabel
  };
}

async function stopRecording(reason) {
  if (!current) {
    return { ok: true, stopped: false };
  }

  const state = current;
  current = null;
  state.stopped = true;

  for (const processor of Object.values(state.processors)) {
    if (processor) {
      processor.onaudioprocess = null;
      disconnectQuietly(processor);
    }
  }

  disconnectQuietly(state.silentOutput);
  disconnectQuietly(state.mixGain);
  disconnectQuietly(state.mixAnalyser);
  disconnectQuietly(state.tabGain);
  disconnectQuietly(state.tabAnalyser);
  disconnectQuietly(state.tabSource);
  disconnectQuietly(state.micGain);
  disconnectQuietly(state.micAnalyser);
  disconnectQuietly(state.micSource);

  stopTracks(state.tabStream);
  stopTracks(state.micStream);

  await state.uploadChain;
  await state.audioContext.close().catch(() => undefined);

  const finish = await finishSession(state, reason);

  await chrome.runtime.sendMessage({
    type: "GV_OFFSCREEN_STOPPED",
    sessionId: state.sessionId,
    tabId: state.tabId,
    finish
  }).catch(() => undefined);

  return {
    ok: true,
    stopped: true,
    sessionId: state.sessionId,
    finish
  };
}

function handleAudioProcess(state, track, event) {
  if (state.stopped) {
    return;
  }

  updatePeakLevels(state, event.inputBuffer);
  const pcm = audioBufferToPcm16(event.inputBuffer);
  const sequence = state.trackSequences[track]++;
  enqueueChunkUpload(state, track, sequence, pcm.buffer.slice(0));
}

function enqueueChunkUpload(state, track, sequence, body) {
  const url = new URL(`${state.serviceUrl}/sessions/${state.sessionId}/chunk`);
  url.searchParams.set("track", track);
  url.searchParams.set("sequence", String(sequence));
  url.searchParams.set("sample_rate", String(state.sampleRate));
  url.searchParams.set("channels", "1");

  state.uploadChain = state.uploadChain.then(async () => {
    const response = await fetch(url.toString(), {
      method: "POST",
      headers: {
        "Content-Type": "application/octet-stream"
      },
      body
    });

    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new Error(`${track} chunk upload failed: HTTP ${response.status} ${text}`);
    }
  }).catch((error) => {
    state.trackUploadErrors[track].push(error.message || String(error));
    state.trackDroppedChunks[track] += 1;
    console.error(`${track} chunk upload failed`, error);
  });
}

async function finishSession(state, reason) {
  const response = await fetch(`${state.serviceUrl}/sessions/${state.sessionId}/finish`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      reason,
      chunks: state.trackSequences.mixed,
      dropped_chunks: state.trackDroppedChunks.mixed,
      upload_errors: state.trackUploadErrors.mixed,
      track_chunks: state.trackSequences,
      track_dropped_chunks: state.trackDroppedChunks,
      track_upload_errors: state.trackUploadErrors,
      sample_rate: state.sampleRate,
      channels: 1,
      mic_captured: state.micCaptured,
      mic_error: state.micError,
      mic_track_label: state.micTrackLabel,
      mic_peak: state.micPeak,
      tab_peak: state.tabPeak,
      mixed_peak: state.mixedPeak,
      ended_at: new Date().toISOString()
    })
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || `Finish failed: HTTP ${response.status}`);
  }

  return payload;
}

function createTrackProcessor(audioContext) {
  return audioContext.createScriptProcessor(4096, 2, 1);
}

function microphoneConstraints(preferredMicDeviceId) {
  const audio = {
    echoCancellation: false,
    noiseSuppression: false,
    autoGainControl: false,
    channelCount: 1
  };

  if (preferredMicDeviceId) {
    audio.deviceId = { exact: preferredMicDeviceId };
  }

  return audio;
}

function updatePeakLevels(state, inputBuffer) {
  state.mixedPeak = Math.max(state.mixedPeak, analyserPeak(state.mixAnalyser) || peakForBuffer(inputBuffer));
  state.tabPeak = Math.max(state.tabPeak, analyserPeak(state.tabAnalyser));
  state.micPeak = Math.max(state.micPeak, analyserPeak(state.micAnalyser));
}

function analyserPeak(analyser) {
  if (!analyser) {
    return 0;
  }

  const data = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(data);
  let peak = 0;

  for (let i = 0; i < data.length; i += 1) {
    peak = Math.max(peak, Math.abs(data[i] || 0));
  }

  return Number(peak.toFixed(6));
}

function peakForBuffer(inputBuffer) {
  let peak = 0;
  const channelCount = inputBuffer.numberOfChannels || 1;

  for (let channel = 0; channel < channelCount; channel += 1) {
    const data = inputBuffer.getChannelData(channel);
    for (let i = 0; i < data.length; i += 1) {
      peak = Math.max(peak, Math.abs(data[i] || 0));
    }
  }

  return Number(peak.toFixed(6));
}

function audioBufferToPcm16(inputBuffer) {
  const length = inputBuffer.length;
  const channelCount = inputBuffer.numberOfChannels || 1;
  const pcm = new Int16Array(length);

  for (let i = 0; i < length; i += 1) {
    let sample = 0;
    for (let channel = 0; channel < channelCount; channel += 1) {
      sample += inputBuffer.getChannelData(channel)[i] || 0;
    }

    sample /= channelCount;
    sample = Math.max(-1, Math.min(1, sample));
    pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }

  return pcm;
}

function disconnectQuietly(node) {
  if (!node) {
    return;
  }

  try {
    node.disconnect();
  } catch {
    // Already disconnected.
  }
}

function stopTracks(stream) {
  if (!stream) {
    return;
  }

  for (const track of stream.getTracks()) {
    track.stop();
  }
}
