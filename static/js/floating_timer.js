(function () {
  "use strict";

  const TIMER_CONTAINER_ID = "floating-train-timer";
  const PAGE_MARKER_ID = "train-page";
  const SESSION_STATS_ID = "train-session-stats";

  const DEFAULT_KEY_DASHBOARD = "timer:train:dashboard";
  const KEY_PREFIX_SESSION = "timer:session:";
  const POS_KEY = "timer:pos:floating";
  const ACTIVE_KEY_PREFIX = "timer:active:";
  const TAB_ID_KEY = "timer:tab-id";

  const TICK_MS = 250;
  const ACTIVE_TTL_MS = 15000;
  const ACTIVE_PING_MS = 5000;
  const READONLY_CHECK_TICK_MS = 1000;
  const RESET_HOLD_MS = 600;
  const MAX_SECONDS = 4 * 60 * 60; // 4h hard cap

  const $ = (id) => document.getElementById(id);

  function nowMs() {
    return Date.now();
  }

  function getCookie(name) {
    const match = document.cookie.match(new RegExp("(^|;\\s*)" + name + "=([^;]*)"));
    return match ? decodeURIComponent(match[2]) : null;
  }

  function safeParseJson(raw) {
    try {
      return JSON.parse(raw);
    } catch (_) {
      return null;
    }
  }

  function formatMmSs(totalSeconds) {
    const s = Math.max(0, Math.abs(Number(totalSeconds) || 0));
    const mm = Math.floor(s / 60);
    const ss = s % 60;
    const mmStr = String(mm).padStart(2, "0");
    const ssStr = String(ss).padStart(2, "0");
    return `${mmStr}:${ssStr}`;
  }

  function formatSigned(totalSeconds) {
    const n = Number(totalSeconds) || 0;
    if (n >= 0) return formatMmSs(n);
    return `+${formatMmSs(Math.abs(n))}`;
  }

  function clampInt(value, lo, hi) {
    const v = Number.isFinite(Number(value)) ? Number(value) : 0;
    return Math.max(lo, Math.min(hi, Math.trunc(v)));
  }

  function computeRemainingFromEndAt(endAt) {
    if (!endAt) return 0;
    const msLeft = Number(endAt) - nowMs();
    return Math.max(0, Math.ceil(msLeft / 1000));
  }

  function buildKeyFromPage(markerEl) {
    if (!markerEl) return null;
    const sessionId = (markerEl.dataset.sessionId || "").trim();
    if (sessionId) return KEY_PREFIX_SESSION + sessionId;
    return DEFAULT_KEY_DASHBOARD;
  }

  function getTargetMinutesFromPage(markerEl) {
    if (!markerEl) return null;
    const raw = (markerEl.dataset.targetMinutes || "").trim();
    const n = parseInt(raw, 10);
    if (!Number.isFinite(n) || n <= 0) return null;
    return n;
  }

  function isTimerEnabledOnPage(markerEl) {
    if (!markerEl) return false;
    return (markerEl.dataset.trainTimer || "") === "1";
  }

  function shouldAutostartFromPage(markerEl) {
    if (!markerEl) return false;
    return (markerEl.dataset.autostart || "") === "1";
  }

  function getTabId() {
    try {
      let id = sessionStorage.getItem(TAB_ID_KEY);
      if (id) return id;
      id = `tab_${Math.random().toString(36).slice(2, 10)}`;
      sessionStorage.setItem(TAB_ID_KEY, id);
      return id;
    } catch (_) {
      return `tab_${Math.random().toString(36).slice(2, 10)}`;
    }
  }

  function getActiveKey(timerKey) {
    return `${ACTIVE_KEY_PREFIX}${timerKey}`;
  }

  function loadActiveRecord(timerKey) {
    if (!timerKey) return null;
    try {
      const raw = localStorage.getItem(getActiveKey(timerKey));
      if (!raw) return null;
      const obj = safeParseJson(raw);
      if (!obj || typeof obj !== "object") return null;
      return obj;
    } catch (_) {
      return null;
    }
  }

  function saveActiveRecord(timerKey, record) {
    if (!timerKey) return;
    try {
      localStorage.setItem(getActiveKey(timerKey), JSON.stringify(record));
    } catch (_) {
      // ignore
    }
  }

  function clearActiveRecord(timerKey) {
    if (!timerKey) return;
    try {
      localStorage.removeItem(getActiveKey(timerKey));
    } catch (_) {
      // ignore
    }
  }

  function loadSavedPos() {
    try {
      const raw = localStorage.getItem(POS_KEY);
      if (!raw) return null;
      const obj = safeParseJson(raw);
      if (!obj || typeof obj !== "object") return null;
      if (obj.corner) {
        const corner = String(obj.corner);
        const offsetX = Number(obj.offsetX);
        const offsetY = Number(obj.offsetY);
        if (!Number.isFinite(offsetX) || !Number.isFinite(offsetY)) return null;
        return { type: "corner", corner, offsetX, offsetY };
      }
      const left = Number(obj.left);
      const top = Number(obj.top);
      if (!Number.isFinite(left) || !Number.isFinite(top)) return null;
      return { type: "abs", left, top };
    } catch (_) {
      return null;
    }
  }

  function savePosCorner(corner, offsetX, offsetY) {
    try {
      localStorage.setItem(
        POS_KEY,
        JSON.stringify({ corner, offsetX, offsetY, savedAt: nowMs() })
      );
    } catch (_) {
      // ignore
    }
  }

  function clampPos(left, top, width, height) {
    const pad = 12;
    const vw = window.innerWidth || 0;
    const vh = window.innerHeight || 0;
    const maxLeft = Math.max(pad, vw - width - pad);
    const maxTop = Math.max(pad, vh - height - pad);
    return {
      left: Math.max(pad, Math.min(maxLeft, left)),
      top: Math.max(pad, Math.min(maxTop, top)),
    };
  }

  function getCornerAnchors(width, height) {
    const pad = 12;
    const vw = window.innerWidth || 0;
    const vh = window.innerHeight || 0;
    return {
      tl: { left: pad, top: pad },
      tr: { left: Math.max(pad, vw - width - pad), top: pad },
      bl: { left: pad, top: Math.max(pad, vh - height - pad) },
      br: { left: Math.max(pad, vw - width - pad), top: Math.max(pad, vh - height - pad) },
    };
  }

  function snapToCorner(left, top, width, height) {
    const anchors = getCornerAnchors(width, height);
    let best = null;
    Object.entries(anchors).forEach(([corner, anchor]) => {
      const dist = Math.hypot(left - anchor.left, top - anchor.top);
      if (!best || dist < best.dist) {
        best = { corner, anchor, dist };
      }
    });
    if (!best) return null;
    const offsetX = left - best.anchor.left;
    const offsetY = top - best.anchor.top;
    return { corner: best.corner, left, top, offsetX, offsetY };
  }

  class FloatingTimer {
    constructor(containerEl) {
      this.containerEl = containerEl;
      this.cardEl = $("ft-card");
      this.toastEl = $("ft-toast");
      this.toastMsgEl = $("ft-toast-msg");
      this.toastActionsEl = $("ft-toast-actions");
      this.ringEl = $("ft-ring-progress");
      this.ringLen = null;

      this.timeEl = $("ft-time");
      this.subTitleEl = $("ft-sub-title");
      this.subPresetEl = $("ft-sub-preset");
      this.playIcon = $("ft-icon-play");
      this.pauseIcon = $("ft-icon-pause");
      this.playMiniIcon = $("ft-icon-play-mini");
      this.pauseMiniIcon = $("ft-icon-pause-mini");
      this.bellOnIcon = $("ft-icon-bell-on");
      this.bellOffIcon = $("ft-icon-bell-off");
      this.expandIcon = $("ft-icon-expand");
      this.compactIcon = $("ft-icon-compact");

      this.alarmBtn = $("ft-alarm");
      this.openSessionBtn = $("ft-open-session");
      this.minimizeBtn = $("ft-minimize");
      this.expandBtn = $("ft-expand");
      this.minusBtn = $("ft-minus");
      this.plusBtn = $("ft-plus");
      this.startPauseBtn = $("ft-start-pause");
      this.resetBtn = $("ft-reset");
      this.miniToggleBtn = $("ft-mini-toggle");

      this.toastCloseBtn = $("ft-toast-close");
      this.toastAdd5Btn = $("ft-toast-add5");
      this.toastContinueBtn = $("ft-toast-continue");
      this.toastEndSessionBtn = $("ft-toast-end-session");
      this.toastOpenSessionBtn = $("ft-toast-open-session");

      this.pacingLineEl = $("ft-pacing-line");
      this.pacingMetaEl = $("ft-pacing-meta");
      this.readonlyEl = $("ft-readonly");

      this.currentKey = null;
      this.state = null;
      this.tickHandle = null;
      this.lastRenderedRemaining = null;
      this.audioUnlocked = false;
      this.audioCtx = null;
      this.posApplied = false;
      this.drag = null;
      this.dragLast = null;
      this.lastDragMoved = false;
      this.resetHoldTimer = null;
      this.resetHoldFired = false;
      this.tabId = getTabId();
      this.readonly = false;
      this.lastActivePing = 0;
      this.lastReadonlyTickCheck = 0;
      this.sessionStats = null;
      this.cachedTargetMinutes = null;
      this.ringPalette = {
        primary: "#4f46e5",
        warning: "#f59e0b",
        danger: "#ef4444",
      };
      this.audioUnlockHandler = () => this.unlockAudio();

      if (this.ringEl) {
        try {
          this.ringLen = typeof this.ringEl.getTotalLength === "function" ? this.ringEl.getTotalLength() : null;
        } catch (_) {
          this.ringLen = null;
        }
        if (!this.ringLen || !Number.isFinite(this.ringLen)) {
          const r = parseFloat(this.ringEl.getAttribute("r") || "46.5");
          this.ringLen = 2 * Math.PI * (Number.isFinite(r) ? r : 46.5);
        }
        this.ringEl.style.strokeDasharray = `${this.ringLen} ${this.ringLen}`;
      }

      this._bind();
      this.refreshRingPalette();
    }

    _bind() {
      this.alarmBtn?.addEventListener("click", () => this.toggleAlarm());
      this.openSessionBtn?.addEventListener("click", () => this.openSession());
      this.minimizeBtn?.addEventListener("click", () => this.toggleMinimize());
      this.expandBtn?.addEventListener("click", () => this.toggleExpand());
      this.minusBtn?.addEventListener("click", () => this.adjustSeconds(-60));
      this.plusBtn?.addEventListener("click", () => this.adjustSeconds(60));
      this.startPauseBtn?.addEventListener("click", () => this.startPause());
      this.miniToggleBtn?.addEventListener("click", () => this.startPause());

      this.resetBtn?.addEventListener("pointerdown", (ev) => this.onResetHoldStart(ev));
      this.resetBtn?.addEventListener("pointerup", (ev) => this.onResetHoldEnd(ev));
      this.resetBtn?.addEventListener("pointercancel", (ev) => this.onResetHoldEnd(ev));
      this.resetBtn?.addEventListener("click", (ev) => this.onResetClick(ev));

      this.toastCloseBtn?.addEventListener("click", () => this.hideToast());
      this.toastAdd5Btn?.addEventListener("click", () => this.addExtraSeconds(300, { autostart: true }));
      this.toastContinueBtn?.addEventListener("click", () => this.startOvertime());
      this.toastEndSessionBtn?.addEventListener("click", () => this.endSessionFromToast());
      this.toastOpenSessionBtn?.addEventListener("click", () => this.openSession());

      this.cardEl?.addEventListener("pointerdown", (ev) => this.onDragStart(ev));
      this.cardEl?.addEventListener("pointermove", (ev) => this.onDragMove(ev));
      this.cardEl?.addEventListener("pointerup", (ev) => this.onDragEnd(ev));
      this.cardEl?.addEventListener("pointercancel", (ev) => this.onDragEnd(ev));
      this.cardEl?.addEventListener("click", (ev) => this.onCardClick(ev));
      window.addEventListener("resize", () => this.clampToViewport());
      document.addEventListener("visibilitychange", () => this.onVisibility());
      window.addEventListener("storage", (ev) => this.onStorage(ev));
      document.addEventListener("pointerdown", this.audioUnlockHandler, { passive: true });
      document.addEventListener("keydown", this.audioUnlockHandler, { passive: true });
    }

    detachAudioUnlockListeners() {
      if (!this.audioUnlockHandler) return;
      document.removeEventListener("pointerdown", this.audioUnlockHandler);
      document.removeEventListener("keydown", this.audioUnlockHandler);
    }

    primeAudioContext() {
      if (!this.audioCtx || this.audioCtx.state !== "running") return;
      try {
        const ctx = this.audioCtx;
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        gain.gain.setValueAtTime(0.00001, ctx.currentTime);
        osc.frequency.setValueAtTime(440, ctx.currentTime);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(ctx.currentTime);
        osc.stop(ctx.currentTime + 0.01);
      } catch (_) {
        // ignore
      }
    }

    configureFromPage() {
      const markerEl = $(PAGE_MARKER_ID);
      const enabled = isTimerEnabledOnPage(markerEl);
      if (!enabled) {
        this.hide();
        this.stopTick();
        return;
      }

      const key = buildKeyFromPage(markerEl);
      const autostart = shouldAutostartFromPage(markerEl);
      const targetMinutes = getTargetMinutesFromPage(markerEl) || 90;
      const defaultSeconds = clampInt(targetMinutes * 60, 60, MAX_SECONDS);
      const sessionId = (markerEl?.dataset.sessionId || "").trim() || null;

      this.show();
      this.applySavedPositionOnce();
      this.syncSessionStats();
      this.refreshRingPalette();

      const loaded = this.loadState(key);
      const hadState = Boolean(loaded);

      if (this.currentKey !== key) {
        this.currentKey = key;
        this.state = loaded || this.buildInitialState(defaultSeconds);
      } else if (!this.state) {
        this.state = loaded || this.buildInitialState(defaultSeconds);
      } else if (!hadState && (!this.state.defaultSeconds || this.state.defaultSeconds <= 0)) {
        this.state = this.buildInitialState(defaultSeconds);
      }

      // Update default when target minutes changes and timer is not running.
      if (this.state && !this.state.isRunning && !this.state.overtime) {
        if (this.state.defaultSeconds !== defaultSeconds) {
          const prevDefault = this.state.defaultSeconds;
          this.state.defaultSeconds = defaultSeconds;
          if (!this.state.remaining || this.state.remaining === prevDefault) {
            this.state.remaining = defaultSeconds;
          }
        }
      }

      this.state._sessionId = sessionId;
      this.cachedTargetMinutes = targetMinutes;

      this.applyViewState();
      this.applyMinimizedState();

      if (autostart && !hadState && this.state && !this.state.isRunning) {
        this.start({ unlockAudio: false });
        return;
      }

      if (this.state.isRunning) {
        if (this.state.overtime) {
          this.startTick();
        } else if (this.state.endAt) {
          const rem = computeRemainingFromEndAt(this.state.endAt);
          this.state.remaining = rem;
          if (rem <= 0) {
            this.finish({ playAlarm: true });
            return;
          }
          this.startTick();
        }
      } else {
        this.stopTick();
      }

      this.checkReadOnly();
      this.persist();
      this.render();
    }

    buildInitialState(defaultSeconds) {
      return {
        remaining: defaultSeconds,
        isRunning: false,
        endAt: null,
        defaultSeconds: defaultSeconds,
        alarmEnabled: true,
        savedAt: nowMs(),
        minimized: false,
        view: "compact",
        overtime: false,
        overtimeStartAt: null,
      };
    }

    loadState(key) {
      if (!key) return null;
      try {
        const raw = localStorage.getItem(key);
        if (!raw) return null;
        const obj = safeParseJson(raw);
        if (!obj || typeof obj !== "object") return null;
        const remaining = clampInt(obj.remaining, -MAX_SECONDS, MAX_SECONDS);
        const defaultSeconds = clampInt(obj.defaultSeconds, 60, MAX_SECONDS);
        const isRunning = Boolean(obj.isRunning);
        const endAt = obj.endAt ? Number(obj.endAt) : null;
        const alarmEnabled = obj.alarmEnabled === false ? false : true;
        const minimized = Boolean(obj.minimized);
        const view = obj.view === "expanded" ? "expanded" : "compact";
        const overtime = Boolean(obj.overtime);
        const overtimeStartAt = obj.overtimeStartAt ? Number(obj.overtimeStartAt) : null;
        return {
          remaining,
          isRunning,
          endAt,
          defaultSeconds,
          alarmEnabled,
          savedAt: Number(obj.savedAt) || nowMs(),
          minimized,
          view,
          overtime,
          overtimeStartAt,
        };
      } catch (_) {
        return null;
      }
    }

    persist() {
      if (!this.currentKey || !this.state) return;
      if (this.readonly) return;
      const payload = {
        remaining: this.state.remaining,
        isRunning: this.state.isRunning,
        endAt: this.state.endAt,
        defaultSeconds: this.state.defaultSeconds,
        alarmEnabled: this.state.alarmEnabled,
        savedAt: nowMs(),
        minimized: this.state.minimized,
        view: this.state.view,
        overtime: this.state.overtime,
        overtimeStartAt: this.state.overtimeStartAt,
      };
      try {
        localStorage.setItem(this.currentKey, JSON.stringify(payload));
      } catch (_) {
        // ignore storage errors (private mode, quota, etc.)
      }
    }

    show() {
      this.containerEl?.classList.remove("hidden");
    }

    hide() {
      this.containerEl?.classList.add("hidden");
      this.hideToast();
    }

    applySavedPositionOnce() {
      if (this.posApplied) return;
      this.posApplied = true;
      const pos = loadSavedPos();
      if (!pos) return;
      const rect = this.containerEl.getBoundingClientRect();
      if (pos.type === "corner") {
        const anchors = getCornerAnchors(rect.width, rect.height);
        const anchor = anchors[pos.corner] || anchors.br;
        const left = anchor.left + pos.offsetX;
        const top = anchor.top + pos.offsetY;
        const clamped = clampPos(left, top, rect.width, rect.height);
        this.applyAbsolutePos(clamped.left, clamped.top);
        return;
      }
      const clamped = clampPos(pos.left, pos.top, rect.width, rect.height);
      this.applyAbsolutePos(clamped.left, clamped.top);
      const snapped = snapToCorner(clamped.left, clamped.top, rect.width, rect.height);
      if (snapped) savePosCorner(snapped.corner, snapped.offsetX, snapped.offsetY);
    }

    clampToViewport() {
      const pos = this.getCurrentAbsolutePos();
      if (!pos) return;
      const rect = this.containerEl.getBoundingClientRect();
      const clamped = clampPos(pos.left, pos.top, rect.width, rect.height);
      this.applyAbsolutePos(clamped.left, clamped.top);
      const snapped = snapToCorner(clamped.left, clamped.top, rect.width, rect.height);
      if (snapped) savePosCorner(snapped.corner, snapped.offsetX, snapped.offsetY);
    }

    getCurrentAbsolutePos() {
      const left = this.containerEl.style.left;
      const top = this.containerEl.style.top;
      if (!left || !top) return null;
      const l = Number(left.replace("px", ""));
      const t = Number(top.replace("px", ""));
      if (!Number.isFinite(l) || !Number.isFinite(t)) return null;
      return { left: l, top: t };
    }

    applyAbsolutePos(left, top) {
      this.containerEl.style.left = `${Math.round(left)}px`;
      this.containerEl.style.top = `${Math.round(top)}px`;
      this.containerEl.style.right = "auto";
      this.containerEl.style.bottom = "auto";
    }

    onDragStart(ev) {
      if (!this.cardEl || !this.containerEl) return;
      const interactive = ev.target && ev.target.closest && ev.target.closest("button, a, input, select, textarea");
      if (interactive) return;
      if (ev.pointerType === "mouse" && ev.button !== 0) return;

      const rect = this.containerEl.getBoundingClientRect();
      this.drag = {
        pointerId: ev.pointerId,
        startX: ev.clientX,
        startY: ev.clientY,
        startLeft: rect.left,
        startTop: rect.top,
        moved: false,
      };
      this.dragLast = { left: rect.left, top: rect.top };
      this.containerEl.setAttribute("data-dragging", "1");
      try {
        this.cardEl.setPointerCapture(ev.pointerId);
      } catch (_) {
        // ignore
      }
    }

    onDragMove(ev) {
      if (!this.drag || this.drag.pointerId !== ev.pointerId) return;
      const dx = ev.clientX - this.drag.startX;
      const dy = ev.clientY - this.drag.startY;

      if (!this.drag.moved) {
        const dist = Math.hypot(dx, dy);
        if (dist < 6) return;
        this.drag.moved = true;
      }

      const rect = this.containerEl.getBoundingClientRect();
      const nextLeft = this.drag.startLeft + dx;
      const nextTop = this.drag.startTop + dy;
      const clamped = clampPos(nextLeft, nextTop, rect.width, rect.height);
      this.dragLast = { left: clamped.left, top: clamped.top };
      this.applyAbsolutePos(clamped.left, clamped.top);
    }

    onDragEnd(ev) {
      if (!this.drag || this.drag.pointerId !== ev.pointerId) return;
      this.containerEl.removeAttribute("data-dragging");
      const moved = Boolean(this.drag.moved);
      this.dragLast = moved ? this.dragLast : null;
      this.lastDragMoved = moved;
      this.drag = null;
      if (moved && this.dragLast) {
        const rect = this.containerEl.getBoundingClientRect();
        const snapped = snapToCorner(this.dragLast.left, this.dragLast.top, rect.width, rect.height);
        if (snapped) {
          this.applyAbsolutePos(snapped.left, snapped.top);
          savePosCorner(snapped.corner, snapped.offsetX, snapped.offsetY);
        }
      }
    }

    onCardClick(ev) {
      if (!this.containerEl) return;
      if (this.containerEl.getAttribute("data-minimized") !== "1") return;
      if (this.lastDragMoved) {
        this.lastDragMoved = false;
        return;
      }
      const interactive = ev.target && ev.target.closest && ev.target.closest("button, a, input, select, textarea");
      if (interactive) return;
      this.toggleMinimize();
    }

    startTick() {
      if (this.tickHandle) return;
      this.tickHandle = window.setInterval(() => this.tick(), TICK_MS);
    }

    stopTick() {
      if (!this.tickHandle) return;
      window.clearInterval(this.tickHandle);
      this.tickHandle = null;
    }

    tick() {
      if (!this.state || !this.state.isRunning) return;
      const now = nowMs();
      if (!this.lastReadonlyTickCheck || (now - this.lastReadonlyTickCheck) >= READONLY_CHECK_TICK_MS) {
        this.checkReadOnly();
        this.lastReadonlyTickCheck = now;
      }
      if (this.readonly) return;

      if (this.state.overtime) {
        const elapsed = Math.max(0, Math.floor((nowMs() - (this.state.overtimeStartAt || nowMs())) / 1000));
        const rem = -elapsed;
        if (rem !== this.state.remaining) {
          this.state.remaining = rem;
          this.persist();
          this.render();
        }
        this.updateActiveRecord();
        return;
      }

      if (!this.state.endAt) return;
      const rem = computeRemainingFromEndAt(this.state.endAt);
      if (rem !== this.state.remaining) {
        this.state.remaining = rem;
        this.persist();
        this.render();
      }
      if (rem <= 0) {
        this.finish({ playAlarm: true });
      } else {
        this.updateActiveRecord();
      }
    }

    unlockAudio() {
      try {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (!AudioContext) return;
        this.audioCtx = this.audioCtx || new AudioContext();
        const ctx = this.audioCtx;
        if (ctx.state === "suspended") {
          const resumed = ctx.resume();
          if (resumed && typeof resumed.then === "function") {
            resumed
              .then(() => {
                if (ctx.state === "running") {
                  this.audioUnlocked = true;
                  this.primeAudioContext();
                  this.detachAudioUnlockListeners();
                }
              })
              .catch(() => {
                // ignore
              });
            return;
          }
        }
        if (ctx.state === "running") {
          this.audioUnlocked = true;
          this.primeAudioContext();
          this.detachAudioUnlockListeners();
        }
      } catch (_) {
        // ignore
      }
    }

    beepSequence() {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (!this.audioCtx && AudioContext) {
        try {
          this.audioCtx = new AudioContext();
        } catch (_) {
          this.audioCtx = null;
        }
      }
      const ctx = this.audioCtx;
      if (!ctx) return;

      const play = () => {
        if (ctx.state !== "running") return;
        const t0 = ctx.currentTime;

        const beep = (tStart, freq, dur) => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = "triangle";
          osc.frequency.setValueAtTime(freq, tStart);
          gain.gain.setValueAtTime(0.0001, tStart);
          gain.gain.exponentialRampToValueAtTime(0.32, tStart + 0.01);
          gain.gain.exponentialRampToValueAtTime(0.0001, tStart + dur);
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.start(tStart);
          osc.stop(tStart + dur + 0.02);
        };

        beep(t0 + 0.0, 880, 0.20);
        beep(t0 + 0.30, 880, 0.20);
        beep(t0 + 0.60, 988, 0.24);
      };

      if (ctx.state === "suspended") {
        const resumed = ctx.resume();
        if (resumed && typeof resumed.then === "function") {
          resumed
            .then(() => {
              if (ctx.state === "running") {
                this.audioUnlocked = true;
                this.detachAudioUnlockListeners();
                play();
              }
            })
            .catch(() => {
              // ignore
            });
          return;
        }
      }

      if (ctx.state === "running") {
        this.audioUnlocked = true;
        this.detachAudioUnlockListeners();
        play();
      }
    }

    vibrate() {
      try {
        if (navigator.vibrate) navigator.vibrate([120, 80, 120]);
      } catch (_) {
        // ignore
      }
    }

    showToast(message) {
      if (!this.toastEl) return;
      this.toastMsgEl.textContent = message;
      this.toastEl.classList.remove("hidden");

      const sessionId = this.state?._sessionId;
      if (this.toastEndSessionBtn) {
        this.toastEndSessionBtn.classList.toggle("hidden", !sessionId);
      }
      if (this.toastOpenSessionBtn) {
        this.toastOpenSessionBtn.classList.toggle("hidden", !sessionId);
      }
    }

    hideToast() {
      this.toastEl?.classList.add("hidden");
    }

    finish({ playAlarm }) {
      if (!this.state) return;
      this.stopTick();
      this.state.isRunning = false;
      this.state.endAt = null;
      this.state.remaining = 0;
      this.state.overtime = false;
      this.state.overtimeStartAt = null;
      this.persist();
      this.render();
      this.releaseActiveRecord();

      const alarmEnabled = Boolean(this.state.alarmEnabled);
      if (playAlarm && alarmEnabled) {
        this.beepSequence();
        this.vibrate();
      }

      this.showToast("Tempo! Marque o resultado do bloco.");
      window.dispatchEvent(
        new CustomEvent("train:timerFinished", {
          detail: { key: this.currentKey, sessionId: this.state?._sessionId || null },
        })
      );
    }

    render() {
      if (!this.state) return;
      let remaining = this.state.remaining;
      if (this.state.isRunning) {
        if (this.state.overtime) {
          const elapsed = Math.max(0, Math.floor((nowMs() - (this.state.overtimeStartAt || nowMs())) / 1000));
          remaining = -elapsed;
        } else if (this.state.endAt) {
          remaining = computeRemainingFromEndAt(this.state.endAt);
        }
        this.state.remaining = remaining;
      }

      if (this.lastRenderedRemaining !== remaining && this.timeEl) {
        this.timeEl.textContent = formatSigned(remaining);
        this.lastRenderedRemaining = remaining;
      }

      if (this.ringEl && this.ringLen) {
        const total = Math.max(1, Number(this.state.defaultSeconds) || 1);
        const ratio = this.state.overtime ? 1 : Math.max(0, Math.min(1, Math.max(0, remaining) / total));
        const offset = this.ringLen * (1 - ratio);
        this.ringEl.style.strokeDashoffset = String(offset);
        const palette = this.ringPalette || {};
        const color = this.state.overtime
          ? (palette.danger || "#ef4444")
          : ratio <= 0.03
            ? (palette.danger || "#ef4444")
            : ratio <= 0.1
              ? (palette.warning || "#f59e0b")
              : (palette.primary || "#4f46e5");
        this.ringEl.style.stroke = String(color).trim();
      }

      const presetMin = Math.round((this.state.defaultSeconds || 0) / 60);
      const keyLabel = this.currentKey && this.currentKey.startsWith(KEY_PREFIX_SESSION) ? "Sessão" : "Treino";
      if (this.subTitleEl) this.subTitleEl.textContent = keyLabel;
      if (this.subPresetEl) this.subPresetEl.textContent = `${presetMin} min`;

      const running = Boolean(this.state.isRunning);
      this.containerEl?.setAttribute("data-running", running ? "1" : "0");
      this.containerEl?.setAttribute("data-alarm", this.state.alarmEnabled ? "1" : "0");

      if (this.startPauseBtn) {
        this.startPauseBtn.setAttribute("aria-label", running ? "Pausar" : "Iniciar");
      }
      if (this.minusBtn) this.minusBtn.disabled = running;
      if (this.plusBtn) this.plusBtn.disabled = running;

      if (this.alarmBtn) {
        const on = Boolean(this.state.alarmEnabled);
        this.alarmBtn.setAttribute("aria-label", on ? "Desativar alarme" : "Ativar alarme");
      }

      this.updateSessionButtons();
      this.applyPacing(remaining);
      this.applyReadOnlyUI();
    }

    updateSessionButtons() {
      const sessionId = this.state?._sessionId;
      const hasSession = Boolean(sessionId);
      if (this.openSessionBtn) {
        this.openSessionBtn.classList.toggle("hidden", !hasSession);
      }
      if (this.toastOpenSessionBtn) {
        this.toastOpenSessionBtn.classList.toggle("hidden", !hasSession);
      }
    }

    applyPacing(remainingSeconds) {
      if (!this.pacingLineEl || !this.pacingMetaEl) return;
      if (!this.sessionStats || !Number.isFinite(this.sessionStats.total) || this.sessionStats.total <= 0) {
        this.pacingLineEl.textContent = "No ritmo";
        this.pacingMetaEl.textContent = "Obrigatórios: --";
        return;
      }

      const total = Math.max(0, this.sessionStats.total);
      const done = Math.max(0, Math.min(total, this.sessionStats.done || 0));
      const remainingItems = Math.max(0, total - done);
      const expectedTotal = Math.max(0, this.sessionStats.expectedTotal || 0);
      const expectedDone = Math.max(0, this.sessionStats.expectedDone || 0);

      if (remainingItems === 0) {
        this.pacingLineEl.textContent = "Concluído";
        this.pacingMetaEl.textContent = "Obrigatórios: 0";
        return;
      }

      const remaining = Math.max(0, remainingSeconds);
      let diff = 0;
      let diffMin = 0;
      if (expectedTotal > 0) {
        const expectedRemaining = Math.max(0, expectedTotal - expectedDone) * 60;
        diff = remaining - expectedRemaining;
        diffMin = Math.round(Math.abs(diff) / 60);
      } else {
        const idealPerItem = total > 0 ? (this.state.defaultSeconds || 0) / total : 0;
        const idealRemaining = remainingItems * idealPerItem;
        diff = remaining - idealRemaining;
        diffMin = Math.round(Math.abs(diff) / 60);
      }

      if (diff > 300) {
        this.pacingLineEl.textContent = `Adiantado ~${diffMin} min`;
      } else if (diff < -300) {
        this.pacingLineEl.textContent = `Atrasado ~${diffMin} min`;
      } else {
        this.pacingLineEl.textContent = "No ritmo";
      }
      this.pacingMetaEl.textContent = `Obrigatórios: ${remainingItems}`;
    }

    applyViewState() {
      if (!this.state || !this.cardEl) return;
      const view = this.state.view === "expanded" ? "expanded" : "compact";
      this.state.view = view;
      this.cardEl.setAttribute("data-view", view);
    }

    applyMinimizedState() {
      if (!this.state || !this.containerEl) return;
      const minimized = Boolean(this.state.minimized);
      this.containerEl.setAttribute("data-minimized", minimized ? "1" : "0");
    }

    toggleExpand() {
      if (!this.state) return;
      this.checkReadOnly();
      if (this.readonly) return this.showToast("Timer ativo em outra aba.");
      this.state.view = this.state.view === "expanded" ? "compact" : "expanded";
      this.applyViewState();
      this.persist();
      this.render();
    }

    toggleAlarm() {
      if (!this.state) return;
      this.checkReadOnly();
      if (this.readonly) return this.showToast("Timer ativo em outra aba.");
      this.state.alarmEnabled = !this.state.alarmEnabled;
      this.persist();
      this.render();
    }

    toggleMinimize() {
      if (!this.state) return;
      this.state.minimized = !this.state.minimized;
      this.applyMinimizedState();
      this.persist();
      this.render();
    }

    adjustSeconds(deltaSeconds) {
      if (!this.state) return;
      this.checkReadOnly();
      if (this.readonly) return this.showToast("Timer ativo em outra aba.");
      if (this.state.isRunning) return;
      const next = Math.max(0, Math.min(MAX_SECONDS, (this.state.remaining || 0) + deltaSeconds));
      this.state.remaining = next;
      this.state.overtime = false;
      this.state.overtimeStartAt = null;
      this.persist();
      this.render();
    }

    addExtraSeconds(extra, opts) {
      if (!this.state) return;
      this.checkReadOnly();
      if (this.readonly) return this.showToast("Timer ativo em outra aba.");
      if (this.state.isRunning) return;
      const next = Math.max(0, Math.min(MAX_SECONDS, (this.state.remaining || 0) + extra));
      this.state.remaining = next;
      this.state.overtime = false;
      this.state.overtimeStartAt = null;
      this.persist();
      this.render();
      if (opts && opts.autostart) {
        this.start();
      }
      this.hideToast();
    }

    startPause() {
      if (!this.state) return;
      this.checkReadOnly();
      if (this.readonly) return this.showToast("Timer ativo em outra aba.");
      this.unlockAudio();
      if (this.state.isRunning) {
        this.pause();
      } else {
        this.start();
      }
    }

    start(opts) {
      if (!this.state) return;
      this.checkReadOnly();
      if (this.readonly) return;
      const unlock = opts && opts.unlockAudio === false ? false : true;
      if (unlock) this.unlockAudio();
      this.hideToast();
      if (this.state.remaining <= 0) {
        this.state.remaining = this.state.defaultSeconds;
      }
      this.state.overtime = false;
      this.state.overtimeStartAt = null;
      this.state.endAt = nowMs() + (Math.max(0, this.state.remaining) * 1000);
      this.state.isRunning = true;
      this.persist();
      this.render();
      this.startTick();
      this.updateActiveRecord(true);
    }

    startOvertime() {
      if (!this.state) return;
      this.checkReadOnly();
      if (this.readonly) return this.showToast("Timer ativo em outra aba.");
      this.hideToast();
      this.state.overtime = true;
      this.state.overtimeStartAt = nowMs();
      this.state.remaining = 0;
      this.state.endAt = null;
      this.state.isRunning = true;
      this.persist();
      this.render();
      this.startTick();
      this.updateActiveRecord(true);
    }

    pause() {
      if (!this.state) return;
      if (!this.state.isRunning) return;
      if (this.state.overtime) {
        this.state.overtime = false;
        this.state.overtimeStartAt = null;
      } else if (this.state.endAt) {
        this.state.remaining = computeRemainingFromEndAt(this.state.endAt);
      }
      this.state.isRunning = false;
      this.state.endAt = null;
      this.persist();
      this.render();
      this.stopTick();
      this.releaseActiveRecord();
    }

    reset() {
      if (!this.state) return;
      this.checkReadOnly();
      if (this.readonly) return this.showToast("Timer ativo em outra aba.");
      this.hideToast();
      this.state.isRunning = false;
      this.state.endAt = null;
      this.state.remaining = this.state.defaultSeconds;
      this.state.overtime = false;
      this.state.overtimeStartAt = null;
      this.persist();
      this.render();
      this.stopTick();
      this.releaseActiveRecord();
    }

    onResetHoldStart(ev) {
      if (!this.state) return;
      this.checkReadOnly();
      if (this.readonly) return;
      if (!this.state.isRunning) return;
      if (ev.pointerType === "mouse" && ev.button !== 0) return;
      this.resetHoldFired = false;
      this.resetHoldTimer = window.setTimeout(() => {
        this.resetHoldFired = true;
        this.reset();
      }, RESET_HOLD_MS);
    }

    onResetHoldEnd(_ev) {
      if (!this.state) return;
      if (!this.state.isRunning) return;
      if (this.resetHoldTimer) {
        window.clearTimeout(this.resetHoldTimer);
        this.resetHoldTimer = null;
      }
      if (!this.resetHoldFired) {
        this.showToast("Segure para resetar enquanto o timer roda.");
      }
      this.resetHoldFired = false;
    }

    onResetClick(ev) {
      if (!this.state) return;
      if (this.state.isRunning) {
        ev.preventDefault();
        return;
      }
      this.reset();
    }

    async endSessionFromToast() {
      const sessionId = this.state?._sessionId;
      if (!sessionId) return;
      const url = `/treino/session/${sessionId}/end/`;
      const csrftoken = getCookie("csrftoken");
      try {
        const resp = await fetch(url, {
          method: "POST",
          headers: csrftoken ? { "X-CSRFToken": csrftoken } : {},
          credentials: "same-origin",
        });
        if (resp.ok) {
          window.location.href = "/treino/";
          return;
        }
      } catch (_) {
        // ignore
      }
      window.location.href = `/treino/session/${sessionId}/`;
    }

    openSession() {
      const sessionId = this.state?._sessionId;
      if (!sessionId) return;
      window.location.href = `/treino/session/${sessionId}/`;
    }

    updateActiveRecord(force) {
      if (!this.currentKey || !this.state || !this.state.isRunning || this.readonly) return;
      const now = nowMs();
      if (!force && now - this.lastActivePing < ACTIVE_PING_MS) return;
      saveActiveRecord(this.currentKey, {
        tabId: this.tabId,
        endAt: this.state.endAt,
        overtime: this.state.overtime,
        overtimeStartAt: this.state.overtimeStartAt,
        updatedAt: now,
      });
      this.lastActivePing = now;
    }

    releaseActiveRecord() {
      if (!this.currentKey) return;
      const record = loadActiveRecord(this.currentKey);
      if (record && record.tabId === this.tabId) {
        clearActiveRecord(this.currentKey);
      }
    }

    checkReadOnly() {
      if (!this.currentKey) return;
      const record = loadActiveRecord(this.currentKey);
      if (!record) {
        this.readonly = false;
        this.applyReadOnlyUI();
        return;
      }
      const isStale = record.updatedAt && nowMs() - record.updatedAt > ACTIVE_TTL_MS;
      if (isStale) {
        if (record.tabId === this.tabId) {
          clearActiveRecord(this.currentKey);
        }
        this.readonly = false;
        this.applyReadOnlyUI();
        return;
      }
      this.readonly = record.tabId && record.tabId !== this.tabId;
      this.applyReadOnlyUI();
    }

    applyReadOnlyUI() {
      if (!this.containerEl) return;
      this.containerEl.setAttribute("data-readonly", this.readonly ? "1" : "0");
      if (this.readonlyEl) {
        this.readonlyEl.classList.toggle("hidden", !this.readonly);
      }
    }

    onVisibility() {
      if (document.visibilityState === "visible") {
        this.refreshRingPalette();
        this.checkReadOnly();
        if (this.state && this.state.isRunning && !this.state.overtime && this.state.endAt) {
          const rem = computeRemainingFromEndAt(this.state.endAt);
          this.state.remaining = rem;
          if (rem <= 0) {
            this.finish({ playAlarm: true });
            return;
          }
          this.persist();
        }
        this.render();
      }
    }

    onStorage(ev) {
      if (!this.currentKey) return;
      const activeKey = getActiveKey(this.currentKey);
      if (ev.key !== this.currentKey && ev.key !== activeKey) return;
      const record = loadActiveRecord(this.currentKey);
      if (record && record.tabId !== this.tabId) {
        const loaded = this.loadState(this.currentKey);
        if (loaded) {
          loaded._sessionId = this.state?._sessionId || null;
          this.state = loaded;
        }
      }
      this.checkReadOnly();
      this.render();
    }

    syncSessionStats() {
      const statsEl = $(SESSION_STATS_ID);
      if (!statsEl) {
        this.sessionStats = null;
        return;
      }
      const total = parseInt(statsEl.dataset.total || "0", 10);
      const done = parseInt(statsEl.dataset.done || "0", 10);
      const expectedTotal = parseInt(statsEl.dataset.expectedTotal || "0", 10);
      const expectedDone = parseInt(statsEl.dataset.expectedDone || "0", 10);
      if (!Number.isFinite(total) || total < 0) {
        this.sessionStats = null;
        return;
      }
      this.sessionStats = {
        total,
        done: Number.isFinite(done) ? done : 0,
        expectedTotal: Number.isFinite(expectedTotal) ? expectedTotal : 0,
        expectedDone: Number.isFinite(expectedDone) ? expectedDone : 0,
      };
    }

    refreshRingPalette() {
      if (!this.containerEl) return;
      try {
        const styles = getComputedStyle(this.containerEl);
        const readVar = (key, fallback) => {
          const value = styles.getPropertyValue(key);
          const trimmed = String(value || "").trim();
          return trimmed || fallback;
        };
        this.ringPalette = {
          primary: readVar("--ft-primary", "#4f46e5"),
          warning: readVar("--ft-warning", "#f59e0b"),
          danger: readVar("--ft-danger", "#ef4444"),
        };
      } catch (_) {
        this.ringPalette = {
          primary: "#4f46e5",
          warning: "#f59e0b",
          danger: "#ef4444",
        };
      }
    }
  }

  function boot() {
    const containerEl = $(TIMER_CONTAINER_ID);
    if (!containerEl) return;

    const timer = new FloatingTimer(containerEl);
    window.__floatingTrainTimer = timer;

    const configure = () => timer.configureFromPage();
    document.addEventListener("DOMContentLoaded", configure);
    document.body.addEventListener("htmx:afterSwap", configure);
    document.body.addEventListener("htmx:afterSettle", configure);
    configure();
  }

  boot();
})();
