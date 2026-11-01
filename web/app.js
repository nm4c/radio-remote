"use strict";

const $ = (sel) => document.querySelector(sel);

const S = {
  ws: null,
  creds: null,
  authed: false,
  manualClose: false,
  reconnectDelay: 1000,
  reconnectTimer: null,
  ctx: null,
  rxNode: null,
  txNode: null,
  micOk: false,
  channels: [],
  holdIntervalMs: 300,
  frameSamples: 320,
  server: null, // last "state" message
  pttWanted: false,
  holdTimer: null,
  txDeadline: null,
};

// ---------- storage (best effort) ----------
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch {} },
};

// ---------- UI helpers ----------
function toast(text, level = "warn") {
  const el = document.createElement("div");
  el.className = `toast ${level}`;
  el.textContent = text;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 4500);
}

function setStatus(state, text) {
  const el = $("#status");
  el.dataset.state = state;
  el.textContent = text;
}

function showLogin(error) {
  $("#head").hidden = true;
  $("#login").hidden = false;
  $("#loginError").hidden = !error;
  $("#loginError").textContent = error || "";
}

// ---------- audio ----------
const IS_IOS = /iPhone|iPad|iPod/.test(navigator.userAgent)
  || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1); // iPadOS reports as Mac

// Must be called from a click/tap handler the first time (browsers require a user gesture).
async function setupAudio() {
  if (!S.ctx) {
    // Create the context synchronously inside the gesture: iOS refuses to start it otherwise.
    const ctx = new AudioContext({ latencyHint: "interactive" });
    ctx.onstatechange = updateAudioHint;
    S.ctx = ctx;
    const resumed = ctx.resume();
    await ctx.audioWorklet.addModule("/static/worklet.js");
    const rx = new AudioWorkletNode(ctx, "rx-player", {
      numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [1],
    });
    rx.connect(ctx.destination);
    rx.port.postMessage({ gain: Number($("#vol").value) });
    S.rxNode = rx;
    await resumed;
  }
  await setupMic();
  updateAudioHint();
}

function micLive() {
  return !!S.micStream && S.micStream.getAudioTracks().some((t) => t.readyState === "live");
}

// (Re)acquire the microphone. iOS can end the mic track after a phone call, Siri, or a lock.
function setupMic() {
  if (micLive()) return Promise.resolve();
  if (!S.micPending) S.micPending = acquireMic().finally(() => { S.micPending = null; });
  return S.micPending;
}

async function acquireMic() {
  if (S.micSource) { S.micSource.disconnect(); S.micSource = null; }
  try {
    if (!window.isSecureContext) throw new Error("insecure context");
    const mic = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    if (!S.txNode) {
      const tx = new AudioWorkletNode(S.ctx, "tx-capture", {
        numberOfInputs: 1, numberOfOutputs: 0,
        processorOptions: { frameSamples: S.frameSamples },
      });
      tx.port.postMessage({ gain: Number($("#micGain").value) });
      tx.port.onmessage = (e) => {
        const d = e.data;
        if (d.level !== undefined) $("#micMeter").style.width = `${Math.min(100, d.level * 140)}%`;
        if (d.frame) sendFrame(d.frame);
      };
      S.txNode = tx;
    }
    S.micSource = S.ctx.createMediaStreamSource(mic);
    S.micSource.connect(S.txNode);
    S.micStream = mic;
    S.micOk = true;
  } catch (err) {
    S.micOk = false;
    toast(window.isSecureContext
      ? `Microphone unavailable (${err.message}). Receive only.`
      : "Microphone needs HTTPS. Receive only.", "error");
  }
  render();
}

// Restart audio after iOS interrupted or suspended it. Call it from tap handlers when possible.
function wakeAudio() {
  if (!S.ctx) return;
  if (S.ctx.state !== "running") S.ctx.resume().then(updateAudioHint, () => {});
  if (S.micOk && !micLive()) setupMic();
}

function updateAudioHint() {
  $("#audioHint").hidden = !(S.ctx && S.authed && S.ctx.state !== "running");
}

// Keep the screen on while connected (iOS stops audio when the phone locks).
async function keepAwake(on) {
  try {
    if (on && !S.wakeLock && "wakeLock" in navigator && !document.hidden) {
      S.wakeLock = await navigator.wakeLock.request("screen");
      S.wakeLock.addEventListener("release", () => { S.wakeLock = null; });
    } else if (!on && S.wakeLock) {
      const lock = S.wakeLock;
      S.wakeLock = null;
      await lock.release();
    }
  } catch { /* not supported or refused: page still works */ }
}

function sendFrame(buf) {
  const ws = S.ws;
  if (!S.pttWanted || !ws || ws.readyState !== WebSocket.OPEN) return;
  if (ws.bufferedAmount > 32000) return; // network is backed up: drop rather than lag
  ws.send(buf);
}

// ---------- WebSocket ----------
function send(obj) {
  if (S.ws && S.ws.readyState === WebSocket.OPEN) S.ws.send(JSON.stringify(obj));
}

function connect() {
  clearTimeout(S.reconnectTimer);
  S.manualClose = false;
  setStatus("connecting", "Connecting…");
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.binaryType = "arraybuffer";
  S.ws = ws;

  ws.onopen = () => ws.send(JSON.stringify({ type: "auth", ...S.creds }));

  ws.onmessage = (e) => {
    if (typeof e.data !== "string") {
      if (S.rxNode && !S.pttWanted) S.rxNode.port.postMessage(e.data, [e.data]);
      return;
    }
    let m;
    try { m = JSON.parse(e.data); } catch { return; }
    handle(m);
  };

  ws.onclose = () => {
    if (S.ws !== ws) return;
    pttRelease(false);
    S.ws = null;
    if (S.manualClose || !S.authed) {
      setStatus("offline", "Offline");
      return;
    }
    setStatus("connecting", "Reconnecting…");
    S.reconnectTimer = setTimeout(connect, S.reconnectDelay);
    S.reconnectDelay = Math.min(S.reconnectDelay * 2, 15000);
  };
}

function handle(m) {
  switch (m.type) {
    case "auth":
      if (!m.ok) {
        S.authed = false;
        S.manualClose = true;
        store.set("password", null);
        showLogin(m.error || "Login failed");
        return;
      }
      S.authed = true;
      S.reconnectDelay = 1000;
      setStatus("online", `Online · ${m.name}`);
      $("#login").hidden = true;
      $("#head").hidden = false;
      keepAwake(true);
      updateAudioHint();
      break;
    case "hello":
      S.channels = m.channels;
      $("#radioName").textContent = m.radio_name;
      document.title = `${m.radio_name} · Radio Remote`;
      S.holdIntervalMs = m.hold_interval_ms;
      buildChannels();
      break;
    case "state":
      S.server = m;
      if (m.ptt.mine && m.ptt.remaining_s != null) S.txDeadline = performance.now() + m.ptt.remaining_s * 1000;
      if (!m.ptt.mine) S.txDeadline = null;
      render();
      break;
    case "meter":
      $("#rxMeter").style.width = `${Math.max(0, Math.min(100, ((m.rx_dbfs + 60) / 60) * 100))}%`;
      $("#flagBusy").classList.toggle("on", !!m.busy);
      break;
    case "notice":
      toast(m.text, m.level);
      if (m.code === "ptt_denied" || m.code === "ptt_dropped") pttRelease();
      break;
  }
}

// ---------- PTT ----------
function pttPress() {
  wakeAudio(); // a PTT press is a user gesture, so iOS will let us restart audio here
  if (S.pttWanted || !S.authed || !S.ws) return;
  if (!S.micOk) { toast("No microphone – can't transmit"); return; }
  if (!micLive()) { toast("Restarting microphone. Press PTT again."); return; }
  const st = S.server;
  if (st && st.ptt.active && !st.ptt.mine) { toast(`Radio in use by ${st.ptt.by}`); return; }
  if (st && st.stepping) { toast("Wait for channel change to finish"); return; }
  S.pttWanted = true;
  S.rxNode.port.postMessage({ flush: true });
  S.txNode.port.postMessage({ active: true });
  send({ type: "ptt", on: true });
  S.holdTimer = setInterval(() => send({ type: "ptt_hold" }), S.holdIntervalMs);
  render();
}

function pttRelease(notify = true) {
  if (!S.pttWanted) return;
  S.pttWanted = false;
  clearInterval(S.holdTimer);
  if (S.txNode) S.txNode.port.postMessage({ active: false });
  if (notify) send({ type: "ptt", on: false });
  render();
}

function isTyping(el) {
  return el && (el.tagName === "INPUT" || el.tagName === "SELECT" || el.tagName === "TEXTAREA");
}

function bindPtt() {
  const btn = $("#ptt");
  btn.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    btn.setPointerCapture(e.pointerId);
    pttPress();
  });
  for (const ev of ["pointerup", "pointercancel", "lostpointercapture"]) {
    btn.addEventListener(ev, () => pttRelease());
  }
  btn.addEventListener("contextmenu", (e) => e.preventDefault());

  document.addEventListener("keydown", (e) => {
    if (e.code === "Space" && !e.repeat && !isTyping(e.target) && !$("#head").hidden) {
      e.preventDefault();
      pttPress();
    }
  });
  document.addEventListener("keyup", (e) => {
    if (e.code === "Space") { e.preventDefault(); pttRelease(); }
  });
  // Never leave the radio keyed if the window loses focus.
  window.addEventListener("blur", () => pttRelease());
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      pttRelease();
    } else if (S.authed) {
      keepAwake(true); // the wake lock is dropped whenever the page is hidden
      wakeAudio();
      // iOS may have silently killed the socket while we were in the background.
      if (!S.ws && !S.manualClose) connect();
    }
  });
  // Any tap counts as a gesture that lets iOS resume interrupted audio.
  document.addEventListener("pointerdown", wakeAudio, true);
}

// ---------- rendering ----------
function buildChannels() {
  const grid = $("#channelGrid");
  const sync = $("#syncSelect");
  grid.replaceChildren();
  sync.replaceChildren();
  for (const ch of S.channels) {
    const b = document.createElement("button");
    b.dataset.ch = ch.n;
    const label = document.createElement("b");
    label.textContent = `CH ${ch.n}`;
    b.append(label, ch.name);
    b.addEventListener("click", () => send({ type: "channel", action: "goto", channel: ch.n }));
    grid.appendChild(b);

    const o = document.createElement("option");
    o.value = ch.n;
    o.textContent = `${ch.n} – ${ch.name}`;
    sync.appendChild(o);
  }
  render();
}

function render() {
  const st = S.server;
  if (!st) return;
  const p = st.ptt;
  const otherTx = p.active && !p.mine;
  const busyRadio = p.active || st.stepping;

  $("#chNum").textContent = String(st.channel).padStart(2, "0");
  $("#chName").textContent = st.channel_name;
  $("#flagTx").classList.toggle("on", p.active);
  $("#flagChg").classList.toggle("on", st.stepping);
  $("#lcd").classList.toggle("tx", p.mine);
  $("#lcdFoot").textContent = otherTx ? `TX by ${p.by}` : st.stepping ? "Changing channel…" : " ";

  const btn = $("#ptt");
  btn.classList.toggle("live", p.mine);
  btn.classList.toggle("pending", S.pttWanted && !p.mine);
  btn.classList.toggle("locked", otherTx || !S.micOk);
  $("#pttLabel").textContent = p.mine ? "ON AIR" : otherTx ? "IN USE" : "PTT";

  $("#chUp").disabled = busyRadio;
  $("#chDown").disabled = busyRadio;
  for (const b of $("#channelGrid").children) {
    b.disabled = busyRadio;
    b.classList.toggle("current", Number(b.dataset.ch) === st.channel);
  }
  $("#operators").textContent = st.operators.join(", ") || "—";
  updateTimer();
}

function updateTimer() {
  const el = $("#txTimer");
  if (S.txDeadline == null) { el.textContent = ""; return; }
  const left = Math.max(0, Math.ceil((S.txDeadline - performance.now()) / 1000));
  el.textContent = `TOT ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
}

// ---------- init ----------
function init() {
  $("#name").value = store.get("name") || "";
  const saved = store.get("password");
  if (saved) { $("#password").value = saved; $("#remember").checked = true; }
  $("#secureHint").hidden = window.isSecureContext;
  $("#iosNote").hidden = !IS_IOS;

  $("#loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = $("#name").value.trim();
    const password = $("#password").value;
    store.set("name", name);
    store.set("password", $("#remember").checked ? password : null);
    S.creds = { name, password };
    $("#loginError").hidden = true;
    await setupAudio(); // needs this click (user gesture)
    connect();
  });

  $("#chUp").addEventListener("click", () => send({ type: "channel", action: "up" }));
  $("#chDown").addEventListener("click", () => send({ type: "channel", action: "down" }));
  $("#syncBtn").addEventListener("click", () => {
    send({ type: "channel", action: "sync", channel: Number($("#syncSelect").value) });
    toast("Channel display updated");
  });
  $("#vol").addEventListener("input", (e) => S.rxNode && S.rxNode.port.postMessage({ gain: Number(e.target.value) }));
  $("#micGain").addEventListener("input", (e) => S.txNode && S.txNode.port.postMessage({ gain: Number(e.target.value) }));
  $("#logout").addEventListener("click", () => {
    S.manualClose = true;
    S.authed = false;
    pttRelease();
    if (S.ws) S.ws.close();
    keepAwake(false);
    updateAudioHint();
    setStatus("offline", "Offline");
    showLogin();
  });

  bindPtt();
  setInterval(updateTimer, 250);
}

init();
