/* DigitalSoul Live Ghost Pilot HUD Controller */
(() => {
  'use strict';

  // Fallback Tauri bridge when running standalone or in dev
  const tauri = window.__TAURI__ || {
    core: { invoke: async () => {} },
    event: { listen: async () => () => {} }
  };

  const invoke = tauri.core.invoke;
  const listen = tauri.event.listen;

  // DOM Elements
  const root = document.getElementById('hud-root');
  const ghost = document.getElementById('ghost-pilot');
  const ghostBubble = document.getElementById('ghost-status-bubble');
  const ghostText = document.getElementById('ghost-status-text');
  const reticle = document.getElementById('target-reticle');
  const reticleLabel = document.getElementById('reticle-label');
  const browserCard = document.getElementById('browser-hud-card');
  const browserApp = document.getElementById('browser-hud-app');
  const browserStep = document.getElementById('browser-hud-step');
  const browserTarget = document.getElementById('browser-hud-target');
  const browserAction = document.getElementById('browser-hud-action');
  const statusBadge = document.getElementById('hud-status-badge');
  const chipLabel = document.getElementById('hud-chip-label');
  const interactiveControls = document.getElementById('hud-interactive-controls');
  const pauseBtn = document.getElementById('hud-pause-btn');
  const clickthroughBtn = document.getElementById('hud-clickthrough-btn');
  const completionToast = document.getElementById('completion-toast');
  const completionTitle = document.getElementById('completion-title');
  const completionSubtitle = document.getElementById('completion-subtitle');
  const completionIcon = document.getElementById('completion-icon');

  // State
  let currentPos = { x: window.innerWidth / 2, y: 26 };
  let isInteractive = false;
  let activeAnimationId = null;
  let dockTimer = null;
  let flightEndTimer = null;

  function notchPosition() {
    return { x: window.innerWidth / 2, y: 26 };
  }

  function setGhostPosition(x, y) {
    currentPos = { x, y };
    ghost.style.transform = `translate3d(${Math.round(x)}px, ${Math.round(y)}px, 0)`;
  }

  // Smooth arc flight towards target
  function flyGhost(goalX, goalY, duration = 480, onComplete = null) {
    if (activeAnimationId) {
      cancelAnimationFrame(activeAnimationId);
      activeAnimationId = null;
    }
    const startX = currentPos.x;
    const startY = currentPos.y;
    const dx = goalX - startX;
    const dy = goalY - startY;
    const dist = Math.hypot(dx, dy);

    if (dist < 4) {
      setGhostPosition(goalX, goalY);
      if (onComplete) onComplete();
      return;
    }

    // Arc offset pushed sideways
    const push = Math.min(60, dist * 0.2);
    const midX = (startX + goalX) / 2 - (dy / (dist || 1)) * push;
    const midY = (startY + goalY) / 2 + (dx / (dist || 1)) * push;

    const startTime = performance.now();
    const flightDuration = Math.min(850, Math.max(380, duration));

    function step(now) {
      const elapsed = now - startTime;
      const t = Math.min(1, elapsed / flightDuration);
      // Ease in-out sine
      const ease = (1 - Math.cos(Math.PI * t)) / 2;

      // Quadratic bezier
      const u = 1 - ease;
      const x = u * u * startX + 2 * u * ease * midX + ease * ease * goalX;
      const y = u * u * startY + 2 * u * ease * midY + ease * ease * goalY;

      setGhostPosition(x, y);

      if (t < 1) {
        activeAnimationId = requestAnimationFrame(step);
      } else {
        activeAnimationId = null;
        setGhostPosition(goalX, goalY);
        if (onComplete) onComplete();
      }
    }

    activeAnimationId = requestAnimationFrame(step);
  }

  function showStatusText(text) {
    if (!text) {
      ghostBubble.classList.add('hidden');
      return;
    }
    ghostText.textContent = text;
    ghostBubble.classList.remove('hidden');
  }

  function updateReticle(rect, labelText) {
    if (!rect || typeof rect.left !== 'number') {
      reticle.classList.add('hidden');
      return;
    }
    // Physical / viewport pixel coordinates
    const pad = 4;
    const left = Math.max(0, rect.left - pad);
    const top = Math.max(0, rect.top - pad);
    const width = Math.max(24, (rect.width || (rect.right - rect.left) || 40) + pad * 2);
    const height = Math.max(20, (rect.height || (rect.bottom - rect.top) || 30) + pad * 2);

    reticle.style.left = `${Math.round(left)}px`;
    reticle.style.top = `${Math.round(top)}px`;
    reticle.style.width = `${Math.round(width)}px`;
    reticle.style.height = `${Math.round(height)}px`;

    reticleLabel.textContent = labelText ? `Targeting: ${labelText}` : 'Targeting element';
    reticle.classList.remove('hidden', 'acting', 'paused', 'error');

    // Ghost hovers just above-left of the target box without occluding it
    const ghostTargetX = Math.max(10, left - 42);
    const ghostTargetY = Math.max(10, top - 48);
    flyGhost(ghostTargetX, ghostTargetY, 440);
  }

  function showBrowserStatus(payload) {
    reticle.classList.add('hidden');
    browserCard.classList.remove('hidden');

    browserApp.textContent = payload.browser || 'Microsoft Edge';
    browserStep.textContent = `Step ${payload.step_index || 1} of ${payload.total_steps || 1}`;
    browserTarget.textContent = `Targeting: ${payload.label || payload.field || 'Product Form'}`;
    browserAction.textContent = payload.action || payload.status || 'Executing browser action…';

    showStatusText(payload.action || 'Interacting with browser');

    // Ghost glides gracefully near the browser status card
    const cardRect = browserCard.getBoundingClientRect();
    flyGhost(cardRect.right + 18, cardRect.top + 8, 480);
  }

  function hideBrowserStatus() {
    browserCard.classList.add('hidden');
  }

  function setInteractiveMode(interactive) {
    isInteractive = Boolean(interactive);
    if (isInteractive) {
      statusBadge.classList.add('interactive');
      statusBadge.classList.remove('click-through');
      chipLabel.textContent = 'HUD Interactive • Clicks Enabled (Ctrl+F8 to lock)';
      interactiveControls.classList.remove('hidden');
    } else {
      statusBadge.classList.remove('interactive');
      statusBadge.classList.add('click-through');
      chipLabel.textContent = 'Ghost Pilot HUD • Click-Through (Ctrl+F8)';
      interactiveControls.classList.add('hidden');
    }
  }

  async function toggleInteractive() {
    const next = !isInteractive;
    setInteractiveMode(next);
    try {
      await invoke('set_hud_interactive', { interactive: next });
    } catch (_) {}
  }

  async function returnToNotch(reason = 'complete', message = '', subtitle = '') {
    if (dockTimer) clearTimeout(dockTimer);
    if (flightEndTimer) clearTimeout(flightEndTimer);

    reticle.classList.add('hidden');
    hideBrowserStatus();

    completionToast.classList.remove('hidden', 'error');
    if (reason === 'error' || reason === 'paused') {
      completionToast.classList.add('error');
      completionIcon.textContent = '⚠';
      completionTitle.textContent = message || 'Execution Paused';
      completionSubtitle.textContent = subtitle || 'Ghost returned to notch (F8 to resume/inspect)';
      ghost.classList.add('paused');
      showStatusText('Paused');
    } else {
      completionIcon.textContent = '✓';
      completionTitle.textContent = message || 'Routine Succeeded';
      completionSubtitle.textContent = subtitle || 'Docking back to notch…';
      ghost.classList.remove('paused', 'error');
      showStatusText('✓ Completed');
    }

    // Wait a brief moment for the user to read the state
    const readDelay = (reason === 'error' || reason === 'paused') ? 1600 : 900;
    dockTimer = setTimeout(() => {
      const notch = notchPosition();
      // Fly ghost back to the notch
      flyGhost(notch.x, notch.y, 520, () => {
        ghost.classList.add('parked');
        flightEndTimer = setTimeout(async () => {
          completionToast.classList.add('hidden');
          try {
            await invoke('set_hud_visible', { visible: false });
          } catch (_) {}
        }, 350);
      });
    }, readDelay);
  }

  // Handle incoming backend & execution events
  function handleEvent(payload) {
    if (!payload || !payload.event) return;

    switch (payload.event) {
      case 'replay_started':
      case 'hud_started':
        if (dockTimer) clearTimeout(dockTimer);
        completionToast.classList.add('hidden');
        ghost.classList.remove('parked', 'paused', 'error', 'acting');
        hideBrowserStatus();
        setGhostPosition(window.innerWidth / 2, 26);
        showStatusText('Ghost Pilot Engaged');
        try { invoke('set_hud_visible', { visible: true }); } catch (_) {}
        break;

      case 'hud_targeting':
      case 'step_targeting':
        ghost.classList.remove('parked', 'acting');
        completionToast.classList.add('hidden');
        if (payload.is_browser_level || !payload.rect) {
          showBrowserStatus(payload);
        } else {
          hideBrowserStatus();
          updateReticle(payload.rect, payload.label || payload.target_name);
          showStatusText(payload.status || `Targeting: ${payload.label || 'Control'}`);
        }
        break;

      case 'hud_acting':
      case 'step_acting':
        reticle.classList.add('acting');
        ghost.classList.add('acting');
        setTimeout(() => {
          reticle.classList.remove('acting');
          ghost.classList.remove('acting');
        }, 550);
        showStatusText(payload.status || `Executing: ${payload.action || 'action'}`);
        break;

      case 'hud_obstacle':
        completionToast.classList.add('hidden');
        ghost.classList.remove('parked', 'acting');
        ghost.classList.add('paused');
        if (payload.rect) {
          hideBrowserStatus();
          updateReticle(payload.rect, payload.label || 'Obstacle Detected');
          reticle.classList.remove('acting');
          reticle.classList.add('obstacle');
        } else {
          showBrowserStatus({
            browser: 'Obstacle Detection',
            step_index: '!',
            total_steps: '!',
            label: payload.label || 'Obstacle Detected',
            action: payload.details || 'Blocking workflow element'
          });
        }
        showStatusText(`⚠️ Obstacle: ${payload.label || 'Detected'}`);
        break;

      case 'hud_recovering':
        reticle.classList.remove('paused', 'error');
        reticle.classList.add('acting');
        ghost.classList.remove('paused', 'error');
        ghost.classList.add('acting');
        if (payload.rect) {
          updateReticle(payload.rect, payload.label || 'Recovering');
        }
        showStatusText(`🔧 ${payload.status || 'Recovering…'}`);
        break;

      case 'hud_retrying_verification':
        reticle.classList.remove('obstacle', 'error');
        ghost.classList.add('acting');
        showStatusText(`🔍 ${payload.status || 'Retrying verification…'}`);
        break;

      case 'hud_paused_for_help':
        reticle.classList.remove('acting');
        reticle.classList.add('error');
        ghost.classList.remove('acting');
        ghost.classList.add('paused');
        returnToNotch('paused', 'Help Needed', payload.reason || 'User assistance required to clear obstacle.');
        break;

      case 'hud_browser_step':
      case 'shopify_started':
        showBrowserStatus(payload);
        break;

      case 'replay_finished':
      case 'hud_completed':
        if (payload.job && payload.job.ok === false) {
          returnToNotch('paused', 'Replay Paused', payload.job.detail || 'Stopped with F8 or control mismatch.');
        } else {
          returnToNotch('complete', 'Routine Completed', payload.job?.detail || 'All steps executed deterministically.');
        }
        break;

      case 'error':
        returnToNotch('error', 'Execution Halted', payload.message || 'An error stopped the routine.');
        break;

      case 'hud_hide':
        returnToNotch('complete');
        break;
    }
  }

  // Keyboard shortcut: Ctrl+F8 toggles interactive overlay mode
  window.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key === 'F8') {
      e.preventDefault();
      toggleInteractive();
    }
  });

  statusBadge?.addEventListener('click', () => {
    toggleInteractive();
  });

  clickthroughBtn?.addEventListener('click', () => {
    setInteractiveMode(false);
    try { invoke('set_hud_interactive', { interactive: false }); } catch (_) {}
  });

  pauseBtn?.addEventListener('click', async () => {
    try {
      await invoke('backend_request', { method: 'stop_jobs', payload: {} });
      returnToNotch('paused', 'Stopped by User', 'Overlay paused execution.');
    } catch (err) {
      console.warn('Stop failed:', err);
    }
  });

  // Listen to desktop event stream from Tauri Rust backend
  listen('desktop-event', (evt) => {
    handleEvent(evt.payload);
  });

  listen('hud-interactive-changed', (evt) => {
    if (evt.payload && typeof evt.payload.interactive === 'boolean') {
      setInteractiveMode(evt.payload.interactive);
    }
  });

  // Export for testing or manual preview
  window.__DIGITALSOUL_HUD__ = {
    handleEvent,
    flyGhost,
    updateReticle,
    showBrowserStatus,
    returnToNotch,
    toggleInteractive
  };
})();
