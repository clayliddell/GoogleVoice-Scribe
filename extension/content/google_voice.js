(() => {
  if (globalThis.__googleVoiceTranscriberContentLoaded) {
    return;
  }

  globalThis.__googleVoiceTranscriberContentLoaded = true;

  let armed = false;
  let recording = false;
  let activeSeen = false;
  let lastIndicatorAt = 0;
  let watcher = null;
  let observer = null;
  let sessionId = null;
  let stopReported = false;

  chrome.runtime.sendMessage({ type: "GV_CONTENT_READY" }).catch(() => undefined);

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || !message.type) {
      return false;
    }

    switch (message.type) {
      case "GV_PING":
        sendResponse({ ok: true });
        return true;

      case "GV_GET_STATE":
        sendResponse({ ok: true, armed, recording, sessionId });
        return true;

      case "GV_ARM_TAB":
        armed = true;
        stopReported = false;
        showToast("Google Voice recorder armed");
        sendResponse({ ok: true });
        return true;

      case "GV_DISARM_TAB":
        armed = false;
        showToast("Google Voice recorder disarmed");
        sendResponse({ ok: true });
        return true;

      case "GV_RECORDING_STARTED":
        recording = true;
        armed = true;
        activeSeen = false;
        lastIndicatorAt = Date.now();
        sessionId = message.sessionId;
        stopReported = false;
        startCallEndWatcher();
        showToast(message.micTrackLabel ? `Recording call with ${message.micTrackLabel}` : "Recording Google Voice call with microphone");
        sendResponse({ ok: true });
        return true;

      case "GV_RECORDING_STOPPED":
        resetRecordingState({ keepArmed: Boolean(message.armed) });
        showToast(message.armed ? "Recording saved; recorder still armed" : "Google Voice recording saved");
        sendResponse({ ok: true });
        return true;

      case "GV_ERROR":
        resetRecordingState({ keepArmed: false });
        showToast(`Recorder error: ${message.error}`);
        sendResponse({ ok: true });
        return true;

      default:
        return false;
    }
  });

  document.addEventListener("click", (event) => {
    const interactive = nearestInteractiveElement(event.target);
    if (!interactive) {
      return;
    }

    if (armed && !recording && isLikelyOutgoingCallButton(interactive)) {
      const label = elementLabel(interactive);
      showToast("Starting Google Voice recording...");
      chrome.runtime.sendMessage({
        type: "GV_CALL_BUTTON_CLICKED",
        label,
        pageUrl: location.href
      }).then((response) => {
        if (!response?.ok) {
          showToast(`Recorder did not start: ${response?.error || "unknown error"}`);
        }
      }).catch((error) => {
        showToast(`Recorder did not start: ${error.message}`);
      });
      return;
    }

    if (recording && isLikelyHangupButton(interactive)) {
      setTimeout(() => reportCallEnded("hangup_button_clicked"), 1500);
    }
  }, true);

  window.addEventListener("beforeunload", () => {
    if (recording && !stopReported) {
      chrome.runtime.sendMessage({
        type: "GV_CALL_ENDED",
        reason: "google_voice_page_unloaded"
      }).catch(() => undefined);
    }
  });

  function startCallEndWatcher() {
    stopCallEndWatcher();

    watcher = window.setInterval(checkCallState, 1000);
    observer = new MutationObserver(checkCallState);
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["aria-label", "data-tooltip", "title"]
    });

    checkCallState();
  }

  function stopCallEndWatcher() {
    if (watcher) {
      window.clearInterval(watcher);
      watcher = null;
    }

    if (observer) {
      observer.disconnect();
      observer = null;
    }
  }

  function checkCallState() {
    if (!recording || stopReported) {
      return;
    }

    const now = Date.now();
    const hasIndicator = hasActiveCallIndicator();

    if (hasIndicator) {
      activeSeen = true;
      lastIndicatorAt = now;
      return;
    }

    if (activeSeen && now - lastIndicatorAt > 3000) {
      reportCallEnded("call_ui_inactive");
    }
  }

  function reportCallEnded(reason) {
    if (!recording || stopReported) {
      return;
    }

    stopReported = true;
    chrome.runtime.sendMessage({
      type: "GV_CALL_ENDED",
      reason,
      sessionId
    }).catch((error) => {
      stopReported = false;
      showToast(`Recorder did not stop: ${error.message}`);
    });
  }

  function resetRecordingState({ keepArmed } = { keepArmed: false }) {
    armed = Boolean(keepArmed);
    recording = false;
    activeSeen = false;
    lastIndicatorAt = 0;
    sessionId = null;
    stopReported = false;
    stopCallEndWatcher();
  }

  function hasActiveCallIndicator() {
    const candidates = document.querySelectorAll("button, [role='button'], [aria-label], [data-tooltip], [title]");

    for (const candidate of candidates) {
      const label = elementLabel(candidate).toLowerCase();
      if (
        label.includes("hang up") ||
        label.includes("end call") ||
        label.includes("call in progress") ||
        label.includes("calling")
      ) {
        return true;
      }
    }

    return false;
  }

  function nearestInteractiveElement(target) {
    if (!(target instanceof Element)) {
      return null;
    }

    return target.closest("button, [role='button'], a, gv-call-button, div[aria-label], span[aria-label]");
  }

  function isLikelyOutgoingCallButton(element) {
    const label = elementLabel(element).toLowerCase();
    if (!label.includes("call")) {
      return false;
    }

    return !(
      label.includes("end call") ||
      label.includes("hang up") ||
      label.includes("call history") ||
      label.includes("missed call") ||
      label.includes("video call")
    );
  }

  function isLikelyHangupButton(element) {
    const label = elementLabel(element).toLowerCase();
    return label.includes("hang up") || label.includes("end call");
  }

  function elementLabel(element) {
    const pieces = [
      element.getAttribute("aria-label"),
      element.getAttribute("data-tooltip"),
      element.getAttribute("title"),
      element.textContent
    ];

    return pieces.filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
  }

  function showToast(message) {
    let toast = document.getElementById("gv-local-transcriber-toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "gv-local-transcriber-toast";
      toast.style.cssText = [
        "position: fixed",
        "right: 16px",
        "bottom: 16px",
        "z-index: 2147483647",
        "max-width: 360px",
        "padding: 10px 12px",
        "border-radius: 6px",
        "background: #111827",
        "color: #fff",
        "font: 13px/1.35 system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
        "box-shadow: 0 8px 24px rgba(0,0,0,0.24)",
        "opacity: 0",
        "transition: opacity 150ms ease"
      ].join(";");
      document.documentElement.appendChild(toast);
    }

    toast.textContent = message;
    toast.style.opacity = "1";
    window.clearTimeout(toast.__hideTimer);
    toast.__hideTimer = window.setTimeout(() => {
      toast.style.opacity = "0";
    }, 3500);
  }
})();
