/**
 * DigitalSoul Observer - Background Service Worker (Developer 1 / M1)
 * Buffers RawEvents and flushes batches to the local agent on 127.0.0.1:7777.
 */

const AGENT_BASE_URL = "http://127.0.0.1:7777/api/v1";
let eventBuffer = [];
let flushTimer = null;
let isObserving = true;

// Periodically check agent observation settings
async function checkObservationSettings() {
  try {
    const res = await fetch(`${AGENT_BASE_URL}/settings/observation`);
    if (res.ok) {
      const data = await res.json();
      isObserving = data.observing;
    }
  } catch (err) {
    // Agent offline or starting up
  }
}

// Initial check & interval
checkObservationSettings();
setInterval(checkObservationSettings, 10000);

async function flushBuffer() {
  if (eventBuffer.length === 0 || !isObserving) return;

  const batch = eventBuffer.splice(0, 200);

  try {
    const res = await fetch(`${AGENT_BASE_URL}/events/batch`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(batch),
    });

    if (!res.ok) {
      console.warn("Agent batch ingest rejected:", res.status);
    }
  } catch (err) {
    // If agent is offline, re-queue up to 200 events so we don't drop during restart
    eventBuffer = batch.slice(-100).concat(eventBuffer);
  }
}

function queueEvent(event) {
  if (!isObserving) return;
  eventBuffer.push(event);

  if (eventBuffer.length >= 20) {
    if (flushTimer) clearTimeout(flushTimer);
    flushBuffer();
  } else if (!flushTimer) {
    flushTimer = setTimeout(() => {
      flushTimer = null;
      flushBuffer();
    }, 2000);
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "RAW_EVENT" && message.event) {
    queueEvent(message.event);
    sendResponse({ status: "queued" });
  }
  return true;
});
