/**
 * DigitalSoul Observer - Content Script (Developer 1 / M1)
 * Captures semantic DOM interactions with locator bundles and source redaction.
 */

(() => {
  if (window.__digitalSoulInjected) return;
  window.__digitalSoulInjected = true;

  // Sensitive patterns dropped at source
  const SENSITIVE_PATTERNS = [
    /password/i,
    /passwd/i,
    /secret/i,
    /token/i,
    /api[_-]?key/i,
    /credit[_-]?card/i,
    /card[_-]?number/i,
    /cvv/i,
    /cvc/i,
    /ssn/i,
    /auth/i,
  ];

  // Whitelisted field names where actual text may be captured
  const VALUE_ALLOWLIST = new Set(["tag", "segment", "notes", "summary", "status", "category"]);

  function isSensitiveElement(el) {
    if (!el) return false;
    if (el.type === "password") return true;
    const name = el.name || "";
    const id = el.id || "";
    const autocomplete = el.autocomplete || "";
    const placeholder = el.placeholder || "";
    const ariaLabel = el.getAttribute("aria-label") || "";

    const combined = `${name} ${id} ${autocomplete} ${placeholder} ${ariaLabel}`;
    return SENSITIVE_PATTERNS.some((pat) => pat.test(combined));
  }

  function getCSSPath(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return "";
    if (el.id) return `#${el.id}`;
    let path = [];
    while (el && el.nodeType === Node.ELEMENT_NODE && el.tagName.toLowerCase() !== "html") {
      let selector = el.tagName.toLowerCase();
      if (el.className && typeof el.className === "string") {
        const classes = el.className.trim().split(/\s+/).filter(c => !c.includes(":") && !c.includes("["));
        if (classes.length) selector += `.${classes.slice(0, 2).join(".")}`;
      }
      path.unshift(selector);
      el = el.parentElement;
      if (path.length >= 4) break;
    }
    return path.join(" > ");
  }

  function getXPath(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return "";
    let paths = [];
    for (; el && el.nodeType === Node.ELEMENT_NODE; el = el.parentNode) {
      let index = 0;
      let hasSameTagSiblings = false;
      for (let sibling = el.previousSibling; sibling; sibling = sibling.previousSibling) {
        if (sibling.nodeType === Node.DOCUMENT_TYPE_NODE) continue;
        if (sibling.nodeName === el.nodeName) {
          hasSameTagSiblings = true;
          index++;
        }
      }
      const tagName = el.nodeName.toLowerCase();
      const pathIndex = hasSameTagSiblings ? `[${index + 1}]` : "";
      paths.unshift(tagName + pathIndex);
      if (paths.length >= 5) break;
    }
    return "/" + paths.join("/");
  }

  function getAssociatedLabel(el) {
    if (el.labels && el.labels.length > 0) {
      return el.labels[0].innerText.trim();
    }
    if (el.id) {
      const label = document.querySelector(`label[for="${el.id}"]`);
      if (label) return label.innerText.trim();
    }
    const parentLabel = el.closest("label");
    if (parentLabel) return parentLabel.innerText.trim();
    return null;
  }

  function buildTargetBundle(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return null;
    const rect = el.getBoundingClientRect();
    const testid = el.dataset?.testid || el.getAttribute("data-testid") || el.getAttribute("data-test-id") || null;
    const role = el.getAttribute("role") || el.tagName.toLowerCase();
    const name = el.getAttribute("aria-label") || (el.innerText ? el.innerText.slice(0, 50).trim() : null) || el.title || null;
    const label = getAssociatedLabel(el);
    const css = getCSSPath(el);
    const xpath = getXPath(el);

    return {
      role: role || null,
      name: name || null,
      testid: testid || null,
      label: label || null,
      css: css || null,
      xpath: xpath || null,
      uia: null,
      bbox: [Math.round(rect.x), Math.round(rect.y), Math.round(rect.width), Math.round(rect.height)],
    };
  }

  function parsePageContext() {
    const pathname = window.location.pathname;
    // Parameterize patterns like /customers/1042 -> /customers/:id
    const idMatch = pathname.match(/\/(customers|accounts|users|records)\/([0-9a-zA-Z_-]+)/i);
    if (idMatch) {
      const base = idMatch[1];
      const paramVal = idMatch[2];
      return {
        url_pattern: `/${base}/:id`,
        url_params: { id: paramVal },
      };
    }
    return {
      url_pattern: pathname || "/",
      url_params: null,
    };
  }

  function determineAppId() {
    const host = window.location.hostname;
    if (host.includes("portal") || document.title.toLowerCase().includes("portal")) return "acme-portal";
    if (host.includes("crm") || document.title.toLowerCase().includes("crm")) return "acme-crm";
    return host.replace(/\./g, "-");
  }

  function captureEvent(kind, targetEl, rawValue = null) {
    if (isSensitiveElement(targetEl)) {
      // Never capture secrets
      return;
    }

    const targetBundle = buildTargetBundle(targetEl);
    const pageCtx = parsePageContext();
    const appId = determineAppId();

    // Redaction decision
    let redacted = true;
    let shape = "none";
    let text = null;

    if (rawValue !== null) {
      const labelOrName = (targetBundle?.label || targetBundle?.name || "").toLowerCase();
      const isAllowlisted = Array.from(VALUE_ALLOWLIST).some(w => labelOrName.includes(w));

      if (isAllowlisted && typeof rawValue === "string" && rawValue.length <= 100) {
        redacted = false;
        shape = rawValue.length > 30 ? "text:long" : "text:short";
        text = rawValue;
      } else {
        redacted = true;
        shape = "text:short";
        text = null;
      }
    }

    const payload = {
      event_id: crypto.randomUUID(),
      ts: new Date().toISOString(),
      source: "extension",
      kind: kind,
      app: {
        id: appId,
        type: "web",
        domain: window.location.hostname || "localhost",
        process: "chrome.exe",
        window_title: document.title,
      },
      target: targetBundle,
      value: {
        redacted: redacted,
        shape: shape,
        text: text,
      },
      page: pageCtx,
      session_id: window.__digitalSoulSessionId || "default-session",
    };

    try {
      chrome.runtime.sendMessage({ type: "RAW_EVENT", event: payload });
    } catch (err) {
      // Extension context invalidated or background sleeping
    }
  }

  // --- Listeners ---

  // Navigation
  captureEvent("navigate", document.body);

  // Click
  document.addEventListener("click", (e) => {
    const el = e.target.closest("button, a, input, select, [role='button'], [data-testid]") || e.target;
    captureEvent("click", el);
  }, true);

  // Input Change
  document.addEventListener("change", (e) => {
    const el = e.target;
    if (el.tagName === "INPUT" || el.tagName === "SELECT" || el.tagName === "TEXTAREA") {
      captureEvent("input_change", el, el.value);
    }
  }, true);

  // Submit
  document.addEventListener("submit", (e) => {
    const form = e.target;
    captureEvent("submit", form);
  }, true);

})();
