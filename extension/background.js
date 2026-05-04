const DEFAULT_SERVICE_URL = "http://127.0.0.1:8765";
const VOICE_ORIGIN = "https://voice.google.com/";

chrome.runtime.onInstalled.addListener(async () => {
  await chrome.storage.local.set({ serviceUrl: DEFAULT_SERVICE_URL });
  await chrome.action.setBadgeBackgroundColor({ color: "#0f766e" });
});

chrome.action.onClicked.addListener((tab) => {
  handleActionClick(tab).catch((error) => {
    console.error("Action click failed", error);
    setBadge("ERR", "#b91c1c", tab?.id);
  });
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || !message.type) {
    return false;
  }

  runMessageHandler(message, sender)
    .then((response) => sendResponse(response ?? { ok: true }))
    .catch((error) => {
      console.error("Message handler failed", message.type, error);
      sendResponse({ ok: false, error: error.message || String(error) });
    });

  return true;
});

async function handleActionClick(tab) {
  if (!tab?.id || !isGoogleVoiceUrl(tab.url)) {
    await setBadge("VOICE", "#92400e", tab?.id);
    return;
  }

  await ensureContentScript(tab.id);
  const activeSession = await getActiveSession();
  const armedTab = await getArmedTab();
  const contentState = await getContentState(tab.id);

  if (activeSession?.tabId === tab.id || contentState.recording) {
    await stopRecording("manual_action_click", tab.id);
    await disarmTab(tab.id);
    return;
  }

  if (armedTab?.tabId === tab.id || contentState.armed) {
    await disarmTab(tab.id);
    return;
  }

  const micCheck = await checkMicrophoneReady();
  if (!micCheck.ok) {
    await openMicrophonePermissionPage();
    await setBadge("MIC", "#92400e", tab.id);
    await ensureContentScript(tab.id);
    await chrome.tabs.sendMessage(tab.id, {
      type: "GV_ERROR",
      error: `Microphone access is required before arming: ${micCheck.error}`
    }).catch(() => undefined);
    return;
  }

  if (armedTab?.tabId && armedTab.tabId !== tab.id) {
    await chrome.tabs.sendMessage(armedTab.tabId, { type: "GV_DISARM_TAB" }).catch(() => undefined);
  }

  await chrome.storage.session.set({
    armedTab: {
      tabId: tab.id,
      armedAt: new Date().toISOString(),
      micTrackLabel: micCheck.trackLabel || ""
    }
  });

  await chrome.tabs.sendMessage(tab.id, { type: "GV_ARM_TAB" });

  await setBadge("ARM", "#0f766e", tab.id);
}

async function runMessageHandler(message, sender) {
  switch (message.type) {
    case "GV_CONTENT_READY":
      await syncContentState(sender.tab?.id);
      return { ok: true };

    case "GV_CALL_BUTTON_CLICKED":
      return startRecording(sender.tab?.id, sender.tab, message);

    case "GV_CALL_ENDED":
      return stopRecording(message.reason || "call_ended", sender.tab?.id);

    case "GV_OFFSCREEN_STARTED":
      await noteOffscreenStarted(message);
      return { ok: true };

    case "GV_OFFSCREEN_STOPPED":
      await noteOffscreenStopped(message);
      return { ok: true };

    case "GV_OFFSCREEN_WARNING":
      console.warn("Offscreen warning", message.warning);
      if (message.warning === "microphone_unavailable") {
        await openMicrophonePermissionPage();
      }
      return { ok: true };

    case "GV_OFFSCREEN_ERROR":
      console.error("Offscreen error", message.error);
      await setBadge("ERR", "#b91c1c", message.tabId);
      return { ok: true };

    default:
      return { ok: false, error: `Unhandled message type: ${message.type}` };
  }
}

async function startRecording(tabId, tab, details) {
  if (!tabId || !isGoogleVoiceUrl(tab?.url)) {
    return { ok: false, error: "Recording can only start from a Google Voice tab." };
  }

  const armedTab = await getArmedTab();
  if (armedTab?.tabId !== tabId) {
    return { ok: false, error: "This Google Voice tab is not armed." };
  }

  const current = await getActiveSession();
  if (current) {
    return { ok: true, alreadyRecording: true, sessionId: current.sessionId };
  }

  const serviceUrl = await getServiceUrl();
  await assertServiceHealthy(serviceUrl);
  const preferredMicDeviceId = await getPreferredMicDeviceId();

  const startedAt = new Date().toISOString();
  const session = await postJson(serviceUrl, "/sessions/start", {
    source: "google_voice",
    tab_id: tabId,
    tab_url: tab.url,
    page_title: tab.title || "",
    started_at: startedAt,
    trigger_label: details.label || "",
    callee_label: "",
    transcript_mode: "speaker_attributed_asr",
    audio_mode: "mixed_tab_and_microphone_pcm",
    mic_required: true,
    mic_device_id: preferredMicDeviceId || ""
  });

  const sessionId = session.session_id;

  try {
    await ensureOffscreenDocument();
    const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
    const response = await chrome.runtime.sendMessage({
      target: "offscreen",
      type: "GV_START_RECORDING",
      serviceUrl,
      sessionId,
      streamId,
      startedAt,
      tabId,
      captureMicrophone: true,
      preferredMicDeviceId
    });

    if (!response?.ok) {
      throw new Error(response?.error || "Offscreen recorder did not start.");
    }

    if (!response.micCaptured) {
      throw new Error(response.micError || "Microphone capture did not start.");
    }

    await chrome.storage.session.set({
      activeSession: {
        sessionId,
        serviceUrl,
        tabId,
        startedAt,
        tabUrl: tab.url
      }
    });

    await setBadge("REC", "#b91c1c", tabId);
    await chrome.tabs.sendMessage(tabId, {
      type: "GV_RECORDING_STARTED",
      sessionId,
      micCaptured: response.micCaptured,
      micError: response.micError || null,
      micTrackLabel: response.micTrackLabel || ""
    });

    return { ok: true, sessionId, micCaptured: response.micCaptured };
  } catch (error) {
    await safePostJson(serviceUrl, `/sessions/${sessionId}/abort`, {
      reason: error.message || String(error)
    });
    await setBadge("ERR", "#b91c1c", tabId);
    await chrome.tabs.sendMessage(tabId, {
      type: "GV_ERROR",
      error: error.message || String(error)
    }).catch(() => undefined);
    throw error;
  }
}

async function stopRecording(reason, tabId) {
  const activeSession = await getActiveSession();
  if (!activeSession) {
    await refreshBadge(tabId);
    return { ok: true, stopped: false };
  }

  if (tabId && activeSession.tabId !== tabId) {
    return { ok: false, error: "The active recording belongs to another tab." };
  }

  const response = await chrome.runtime.sendMessage({
    target: "offscreen",
    type: "GV_STOP_RECORDING",
    sessionId: activeSession.sessionId,
    reason
  });

  if (!response?.ok) {
    throw new Error(response?.error || "Offscreen recorder did not stop cleanly.");
  }

  const latest = await getActiveSession();
  if (latest?.sessionId === activeSession.sessionId) {
    await clearActiveSession(activeSession, response);
  }

  return { ok: true, stopped: true, sessionId: activeSession.sessionId };
}

async function noteOffscreenStarted(message) {
  await setBadge("REC", "#b91c1c", message.tabId);
}

async function noteOffscreenStopped(message) {
  const activeSession = await getActiveSession();
  if (!activeSession || activeSession.sessionId !== message.sessionId) {
    return;
  }

  await clearActiveSession(activeSession, message);
}

async function clearActiveSession(activeSession, stopResult) {
  await chrome.storage.session.remove("activeSession");
  const armedTab = await getArmedTab();
  await refreshBadge(activeSession.tabId);

  if (activeSession.tabId) {
    await chrome.tabs.sendMessage(activeSession.tabId, {
      type: "GV_RECORDING_STOPPED",
      sessionId: activeSession.sessionId,
      finish: stopResult.finish || null,
      armed: armedTab?.tabId === activeSession.tabId
    }).catch(() => undefined);
  }
}

async function disarmTab(tabId) {
  const armedTab = await getArmedTab();
  if (armedTab?.tabId === tabId) {
    await chrome.storage.session.remove("armedTab");
  }

  await setBadge("", "#0f766e", tabId);
  await ensureContentScript(tabId);
  await chrome.tabs.sendMessage(tabId, { type: "GV_DISARM_TAB" }).catch(() => undefined);
}

async function syncContentState(tabId) {
  if (!tabId) {
    return;
  }

  const activeSession = await getActiveSession();
  if (activeSession?.tabId === tabId) {
    const armedTab = await getArmedTab();
    await chrome.tabs.sendMessage(tabId, {
      type: "GV_RECORDING_STARTED",
      sessionId: activeSession.sessionId,
      micCaptured: true,
      micError: null,
      micTrackLabel: armedTab?.micTrackLabel || ""
    }).catch(() => undefined);
    return;
  }

  const armedTab = await getArmedTab();
  if (armedTab?.tabId === tabId) {
    await chrome.tabs.sendMessage(tabId, { type: "GV_ARM_TAB" }).catch(() => undefined);
  }
}

async function getContentState(tabId) {
  try {
    const response = await chrome.tabs.sendMessage(tabId, { type: "GV_GET_STATE" });
    return {
      armed: Boolean(response?.armed),
      recording: Boolean(response?.recording),
      sessionId: response?.sessionId || null
    };
  } catch {
    return { armed: false, recording: false, sessionId: null };
  }
}

async function ensureContentScript(tabId) {
  try {
    await chrome.tabs.sendMessage(tabId, { type: "GV_PING" });
  } catch {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["content/google_voice.js"]
    });
  }
}

async function ensureOffscreenDocument() {
  const offscreenUrl = chrome.runtime.getURL("offscreen.html");
  const contexts = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"],
    documentUrls: [offscreenUrl]
  });

  if (contexts.length > 0) {
    return;
  }

  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["USER_MEDIA", "AUDIO_PLAYBACK"],
    justification: "Record audio from a user-started Google Voice call and keep tab audio audible."
  });
}

async function checkMicrophoneReady() {
  try {
    await ensureOffscreenDocument();
    const preferredMicDeviceId = await getPreferredMicDeviceId();
    const response = await chrome.runtime.sendMessage({
      target: "offscreen",
      type: "GV_CHECK_MICROPHONE",
      preferredMicDeviceId
    });

    if (!response?.ok) {
      return {
        ok: false,
        error: response?.error || "Microphone check failed."
      };
    }

    return response;
  } catch (error) {
    return {
      ok: false,
      error: error.message || String(error)
    };
  }
}

async function openMicrophonePermissionPage() {
  const url = chrome.runtime.getURL("ui/permission.html");
  await chrome.tabs.create({ url, active: true });
}

async function assertServiceHealthy(serviceUrl) {
  const response = await fetch(`${serviceUrl}/health`, { method: "GET" });
  if (!response.ok) {
    throw new Error(`Local transcription service is not healthy: HTTP ${response.status}`);
  }
}

async function postJson(serviceUrl, path, body) {
  const response = await fetch(`${serviceUrl}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(body)
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || `HTTP ${response.status}`);
  }

  return payload;
}

async function safePostJson(serviceUrl, path, body) {
  try {
    return await postJson(serviceUrl, path, body);
  } catch (error) {
    console.warn("Best-effort POST failed", path, error);
    return null;
  }
}

async function getServiceUrl() {
  const { serviceUrl } = await chrome.storage.local.get({
    serviceUrl: DEFAULT_SERVICE_URL
  });

  return String(serviceUrl || DEFAULT_SERVICE_URL).replace(/\/+$/, "");
}

async function getPreferredMicDeviceId() {
  const { preferredMicDeviceId } = await chrome.storage.local.get("preferredMicDeviceId");
  return preferredMicDeviceId || "";
}

async function getArmedTab() {
  const { armedTab } = await chrome.storage.session.get("armedTab");
  return armedTab || null;
}

async function getActiveSession() {
  const { activeSession } = await chrome.storage.session.get("activeSession");
  return activeSession || null;
}

async function setBadge(text, color, tabId) {
  await chrome.action.setBadgeBackgroundColor({ color });
  await chrome.action.setBadgeText({
    text,
    ...(tabId ? { tabId } : {})
  });
}

async function refreshBadge(tabId) {
  const activeSession = await getActiveSession();
  if (activeSession?.tabId === tabId) {
    await setBadge("REC", "#b91c1c", tabId);
    return;
  }

  const armedTab = await getArmedTab();
  if (armedTab?.tabId === tabId) {
    await setBadge("ARM", "#0f766e", tabId);
    return;
  }

  await setBadge("", "#0f766e", tabId);
}

function isGoogleVoiceUrl(url) {
  return typeof url === "string" && url.startsWith(VOICE_ORIGIN);
}
