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
  radio: { type: "buttons", canSync: true, canTune: false, band: null },
  holdIntervalMs: 300,
  frameSamples: 320,
  server: null, // last "state" message
  pttWanted: false,
  holdTimer: null,
  txDeadline: null,
  rtcAvailable: false, // server offers WebRTC audio
  pc: null,            // RTCPeerConnection
  rtcUp: false,        // audio is flowing over WebRTC (else WebSocket PCM)
  txGain: null,        // mic -> gain -> txDest (WebRTC send track)
  txDest: null,
  rtcRxGain: null,     // WebRTC receive audio -> gain -> speakers
  rtcRxEl: null,
  releaseTimer: null,
  statsTimer: null,
  head: null,          // radioHead from control_head.js, once the control head has loaded
  headShown: {},       // what the head's screen shows now, so unchanged fields aren't redrawn
  meter: null,         // last "meter" message (RX level, busy, RSSI)
  pending: null,       // channel picked on the head but not yet confirmed: {n, sent, timer}
  lastHeadNotice: 0,   // throttles "can't change channel" toasts while the knob turns
  selcall: null,       // selcall settings from the server (null = selcall off)
  selcallLog: [],      // recent calls, oldest first, as sent by the server
  selcallShown: null,  // a call shown on the head's bottom line: {text, until}
  token: null,         // from the server at login; authorises clip uploads
  clips: null,         // clip limits from the server (null = clips off)
  clipList: [],        // the clips stored on the Pi, shared by every operator
  clipSel: null,       // id of the clip this operator picked for the Play key
  clipBusy: false,     // converting or uploading files
  recBase: null,       // performance.now() when the current recording started (from state.recording)
  role: null,          // "control" or "listen", from the server after login
  account: null,       // {username, display}
  session: null,       // session token for reconnecting (the password itself is never kept)
  remember: false,     // keep the session in localStorage ("Keep me logged in")
  fxList: null,        // voice effect presets from the server (null = effects off)
  fxSel: null,         // preset this operator picked for the P / P1 key
  fxShown: null,       // "Voice: Vader" on the head's bottom line for a moment: {text, until}
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
  const saved = store.get("session");
  $("#resumeBox").hidden = !saved;
  $("#loginForm").hidden = !!saved;
  $("#resumeName").textContent = store.get("sessionUser") || "";
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
    // WebRTC send path: mic -> gain -> MediaStream track (muted until PTT).
    S.txGain = ctx.createGain();
    S.txGain.gain.value = Number($("#micGain").value);
    S.txDest = ctx.createMediaStreamDestination();
    S.txGain.connect(S.txDest);
    S.txDest.stream.getAudioTracks()[0].enabled = false;
    S.rtcRxGain = ctx.createGain();
    S.rtcRxGain.gain.value = Number($("#vol").value);
    S.rtcRxGain.connect(ctx.destination);
    await resumed;
  }
  // The microphone is asked for after login, and only for accounts that can transmit
  // (applyRole), so a listen-only account never triggers the browser's mic prompt.
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
    S.micSource.connect(S.txGain);
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
  if (S.rtcUp || !S.pttWanted || !ws || ws.readyState !== WebSocket.OPEN) return;
  if (ws.bufferedAmount > 32000) return; // network is backed up: drop rather than lag
  ws.send(buf);
}

// ---------- WebRTC audio ----------
// Opus over UDP: lower and steadier latency than audio over the WebSocket (TCP), which stalls
// behind every lost packet. The WebSocket keeps carrying control; it also carries audio if
// WebRTC isn't available or fails to connect.

function iceDone(pc, ms) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    pc.addEventListener("icegatheringstatechange", () => {
      if (pc.iceGatheringState === "complete") { clearTimeout(t); resolve(); }
    });
  });
}

async function rtcStart() {
  rtcStop();
  if (!S.rtcAvailable || !S.ctx || !S.txDest) { updateAudioInfo(); return; }
  const pc = new RTCPeerConnection({ iceServers: [] });
  S.pc = pc;
  const tx = pc.addTransceiver(S.txDest.stream.getAudioTracks()[0], { direction: "sendrecv", streams: [S.txDest.stream] });
  const rx = tx.receiver;
  // Ask for the smallest receive buffer the network allows.
  if ("jitterBufferTarget" in rx) rx.jitterBufferTarget = 0;
  if ("playoutDelayHint" in rx) rx.playoutDelayHint = 0;

  pc.ontrack = (e) => {
    const stream = e.streams[0] || new MediaStream([e.track]);
    // Chrome only delivers remote WebRTC audio to Web Audio if a media element also plays it.
    const el = new Audio();
    el.muted = true;
    el.srcObject = stream;
    el.play().catch(() => {});
    S.rtcRxEl = el;
    S.ctx.createMediaStreamSource(stream).connect(S.rtcRxGain);
  };
  pc.onconnectionstatechange = () => {
    if (pc !== S.pc) return;
    const st = pc.connectionState;
    if (st === "connected") {
      S.rtcUp = true;
      updateAudioInfo();
    } else if (st === "failed" || st === "closed") {
      rtcFallback(`connection ${st}`);
    }
  };
  try {
    await pc.setLocalDescription(await pc.createOffer());
    await iceDone(pc, 2000);
    if (pc !== S.pc) return;
    send({ type: "rtc_offer", sdp: pc.localDescription.sdp });
  } catch (e) {
    rtcFallback(e.message);
  }
  clearInterval(S.statsTimer);
  S.statsTimer = setInterval(updateAudioInfo, 2000);
}

function rtcStop() {
  clearInterval(S.statsTimer);
  if (S.pc) { const pc = S.pc; S.pc = null; pc.close(); }
  if (S.rtcRxEl) { S.rtcRxEl.srcObject = null; S.rtcRxEl = null; }
  S.rtcUp = false;
}

function rtcFallback(why) {
  if (!S.pc) return;
  console.warn("WebRTC audio unavailable:", why);
  rtcStop();
  send({ type: "rtc_close" });
  toast("Low-latency audio couldn't connect; using the fallback.", "warn");
  updateAudioInfo();
}

// Settings → Audio: transport, round trip and receive buffer, from the browser's own stats.
async function updateAudioInfo() {
  const el = $("#audioInfo");
  if (!S.rtcUp || !S.pc) { el.textContent = S.authed ? "WebSocket (fallback)" : "—"; return; }
  let rtt = null, buf = null;
  try {
    const stats = await S.pc.getStats();
    stats.forEach((r) => {
      if (r.type === "candidate-pair" && r.nominated && r.currentRoundTripTime != null) rtt = r.currentRoundTripTime * 1000;
      if (r.type === "inbound-rtp" && r.kind === "audio" && r.jitterBufferEmittedCount) {
        buf = (r.jitterBufferDelay / r.jitterBufferEmittedCount) * 1000;
      }
    });
  } catch { /* stats unsupported */ }
  const parts = ["WebRTC"];
  if (rtt != null) parts.push(`round trip ${Math.round(rtt)} ms`);
  if (buf != null) parts.push(`buffer ${Math.round(buf)} ms`);
  el.textContent = parts.join(" · ");
}

// ---------- control head ----------
// The radio drawn at the top of the page. Two templates, both generated in assets/control-head/:
//   control-head.svg  desk control head (build_template.py), for screens wider than 600 px
//   handheld.svg      portrait handheld (build_handheld.py), for phones
// control_head.js (createRadioHead) turns presses and knob turns on either into the same events
// and fills the same screen fields. This section maps the events onto the radio controls the
// rest of the page uses:
//
//   channel_knob  step      pick a channel; one "goto" is sent when the knob stops turning
//   dpad_up/down  press     the same, one channel per press
//   volume_knob   change    speaker volume, kept in step with the Speaker volume slider
//   power         activate  disconnect (desk only; only after the hold in RADIO_CONFIG.holdMs)
//   emergency     press     the orange button: start or stop recording received audio. While a
//                           recording runs, any other control except the volume knob stops it
//                           instead of doing its usual job. Recording never transmits.
//   menu          press     open Settings (handheld)
//   soft keys     press     Call (send the selcall number; with none entered, open the Selcall
//                           panel), Play (transmit the clip picked in the Audio clips panel;
//                           shows Stop while a clip is on air), Chans (channel list), Tune (SA818)
//                           or Sync (button radios), Setup (Settings).
//                           Desk, 5 keys: Chans, Tune/Sync, Call, Play, Setup.
//                           Handheld, 3 keys: Call, Play, Setup. Chans replaces Call if selcall is
//                           off, and Tune/Sync replaces Play if clips are off. (With clips on, the
//                           handheld reaches Tune on the page and Sync through Setup.)
//   P (desk), P1 (handheld)  press  switch the voice effect picked in the Voice effects panel
//                           on or off. The status bar shows FX while it's on.
//   P2, home, brightness and D-pad left/right: not assigned yet.

const HEAD_DIR = "/static/control-head";
const CHANNEL_SETTLE_MS = 400; // knob idle time before the picked channel is sent
const PENDING_TIMEOUT_MS = 5000; // forget a sent pick if the server never confirms it
const VOLUME_KNOB_UNITY = 50; // knob 0-100 -> speaker gain 0-2, so mid-travel is gain 1
const PHONE_LAYOUT = matchMedia("(max-width: 600px)"); // phones get the handheld

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = src;
    el.onload = resolve;
    el.onerror = () => reject(new Error(`can't load ${src}`));
    document.head.appendChild(el);
  });
}

// Load the head's scripts once (RADIO_CONFIG from the generated radio_config.js, then
// control_head.js), then show the template that suits the screen. Rotating or resizing across
// the phone width swaps it.
async function loadHead() {
  try {
    await loadScript(`${HEAD_DIR}/radio_config.js`);
    await loadScript(`${HEAD_DIR}/control_head.js`);
    await mountHead();
  } catch (err) {
    console.error("Control head unavailable:", err);
    $("#radio").hidden = true;
    toast("The radio display couldn't load. Reload the page to try again.", "error");
    return;
  }
  PHONE_LAYOUT.addEventListener("change", () => mountHead().catch((err) => console.error(err)));
}

async function mountHead() {
  const file = PHONE_LAYOUT.matches ? "handheld.svg" : "control-head.svg";
  const res = await fetch(`${HEAD_DIR}/${file}`);
  if (!res.ok) throw new Error(`${file}: HTTP ${res.status}`);
  const text = await res.text();
  if (file !== (PHONE_LAYOUT.matches ? "handheld.svg" : "control-head.svg")) return; // changed meanwhile
  const box = $("#radio");
  box.innerHTML = text; // our own generated file, same origin
  const svg = box.querySelector("svg");
  const head = createRadioHead(svg); // from control_head.js
  S.head = head;
  S.headSoftkeyCount = svg.querySelectorAll('[id^="display-softkey-"]').length;
  S.headShown = {}; // new, empty screen: redraw everything
  head.setVolume(Number($("#vol").value) * VOLUME_KNOB_UNITY);
  head.on((e) => { if (S.head === head) onHeadEvent(e); });
  updateHead();
}

// Soft keys for the current radio, laid out for the template's 3 or 5 keys.
function headSoftkeys() {
  const chans = { label: "Chans", run: () => $("#channelGrid").scrollIntoView({ behavior: "smooth", block: "center" }) };
  const setup = { label: "Setup", run: openSettings };
  const second = S.radio.canTune ? { label: "Tune", run: showTune }
    : S.radio.canSync ? { label: "Sync", run: showSync } : null;
  const call = S.selcall ? { label: "Call", run: sendSelcall } : null;
  const play = S.clips ? { label: S.server && S.server.clip_tx ? "Stop" : "Play", run: playClip } : null;
  return S.headSoftkeyCount >= 5 ? [chans, second, call, play, setup] : [call || chans, play || second, setup];
}

function openSettings() {
  $("#settings").open = true;
  $("#settings").scrollIntoView({ behavior: "smooth", block: "start" });
}

function showTune() {
  $("#tuneForm").scrollIntoView({ behavior: "smooth", block: "center" });
  $("#tuneFreq").focus({ preventScroll: true });
}

function showSync() {
  $("#settings").open = true;
  $("#syncBlock").scrollIntoView({ behavior: "smooth", block: "center" });
}

// Listen-only accounts can turn the volume knob, open Settings and scroll to the channel list;
// everything else on the head would change the radio, so it only explains why nothing happens.
function headAllowedForListener(e) {
  if (e.control === "volume_knob" || e.control === "menu") return true;
  const key = /^softkey_(\d)$/.exec(e.control);
  const k = key && headSoftkeys()[Number(key[1]) - 1];
  return !!k && (k.label === "Setup" || k.label === "Chans");
}

function onHeadEvent(e) {
  if (S.role !== "control" && ["press", "step", "activate"].includes(e.action) && !headAllowedForListener(e)) {
    listenOnlyToast();
    return;
  }
  // Recording: the orange button toggles it, and any other control (bar volume) stops it. That
  // press only stops the recording, so a stray tap can't also change channel or transmit.
  if (S.server && S.server.recording && e.control !== "volume_knob" && e.control !== "emergency"
      && ["press", "step", "activate"].includes(e.action)) {
    send({ type: "record", on: false });
    return;
  }
  switch (`${e.control}:${e.action}`) {
    case "channel_knob:step": headPickChannel(e.delta); break;
    case "dpad_up:press": headPickChannel(1); break;
    case "dpad_down:press": headPickChannel(-1); break;
    case "volume_knob:change": setSpeakerVolume(e.value / VOLUME_KNOB_UNITY, "head"); break;
    case "power:activate": disconnect(); break;
    case "emergency:press": toggleRecord(); break;
    case "programmable_p:press": case "p1:press": toggleFx(); break;
    case "menu:press": openSettings(); break;
    default: {
      const key = /^softkey_(\d)$/.exec(e.control);
      if (key && e.action === "press") {
        const k = headSoftkeys()[Number(key[1]) - 1];
        // Run just after the press: the browser focuses the pressed key as part of the same press,
        // which would take focus straight back from a field the action focuses (Tune, Call).
        if (k) setTimeout(k.run, 0);
      }
    }
  }
}

// Knob turns pick a channel on the head's screen straight away, like a real radio, but only one
// "goto" goes to the server once the knob settles. A button-pressing radio then steps once
// to the final channel instead of chasing every detent.
function headPickChannel(delta) {
  const st = S.server;
  if (!st || !S.channels.length || !S.authed) return;
  if (st.ptt.active || st.stepping || S.pttWanted) {
    if (performance.now() - S.lastHeadNotice > 2000) {
      S.lastHeadNotice = performance.now();
      toast(st.stepping ? "Wait for channel change to finish" : "Can't change channel while transmitting");
    }
    return;
  }
  const list = S.channels.map((ch) => ch.n);
  const from = S.pending ? S.pending.n : st.channel;
  const i = list.indexOf(from);
  // Off the list (the SA818 VFO, channel 0): step onto the first or last channel, as the server does.
  const next = i < 0
    ? list[delta > 0 ? 0 : list.length - 1]
    : list[(((i + delta) % list.length) + list.length) % list.length]; // the knob is endless: wrap
  clearTimeout(S.pending && S.pending.timer);
  S.pending = { n: next, sent: false, timer: setTimeout(sendPendingChannel, CHANNEL_SETTLE_MS) };
  updateHead();
}

function sendPendingChannel() {
  const p = S.pending;
  if (!p || p.sent) return;
  if (!S.server || p.n === S.server.channel) { cancelPendingChannel(); return; }
  p.sent = true;
  send({ type: "channel", action: "goto", channel: p.n });
  p.timer = setTimeout(cancelPendingChannel, PENDING_TIMEOUT_MS);
}

function cancelPendingChannel() {
  if (!S.pending) return;
  clearTimeout(S.pending.timer);
  S.pending = null;
  updateHead();
}

function channelLabel(n) {
  return n === 0 ? "VFO" : `CH ${String(n).padStart(2, "0")}`;
}

// Frequency line for the current channel: "439.1250  TX 431.1250  T 91.5  W".
function freqText(info) {
  if (!info) return "";
  let t = info.rx;
  if (info.tx !== info.rx) t += `  TX ${info.tx}`;
  const tone = info.tx_tone === info.rx_tone ? info.tx_tone : `${info.tx_tone || "–"} / ${info.rx_tone || "–"}`;
  if (tone) t += `  T ${tone}`;
  if (info.bandwidth === "wide") t += "  W";
  return t;
}

// Signal bars (0-4): SA818 RSSI if the radio reports it, otherwise the received audio level.
function signalBars(m) {
  if (!m) return 0;
  const level = m.rssi != null
    ? (m.rssi - 30) / 100 // RSSI 0-255 in 1 dB steps; roughly 30 (quiet) to 130 (strong)
    : (m.rx_dbfs + 60) / 60; // -60 to 0 dBFS
  return Math.max(0, Math.min(4, Math.round(level * 4)));
}

// Push radio state to the head's screen. Called on state and meter messages and by the TX timer.
// Only fields that changed are sent, because setDisplay re-measures text to fit it.
function updateHead() {
  const st = S.server;
  if (!S.head || !st) return;
  const p = st.ptt;
  const m = S.meter;
  const picking = S.pending && S.pending.n !== st.channel ? S.pending.n : null;
  const shownCh = picking ?? st.channel;
  const pickedInfo = picking != null ? S.channels.find((ch) => ch.n === picking) : null;

  // Status bar, right-hand end: TX with time left / CHG / BUSY, then RSSI and LOW power.
  const status = [];
  if (p.active) status.push(p.mine ? `TX ${totText()}` : "TX");
  else if (st.stepping) status.push("CHG");
  else if (m && m.busy) status.push("BUSY");
  if (m && m.rssi_dbm != null && !p.active) status.push(`${Math.round(m.rssi_dbm)} dBm`);
  if (st.info && st.info.low_power) status.push("LOW");
  if (st.recording) status.unshift("REC");
  if (st.fx) status.unshift("FX");

  // Bottom line: problems first, then who is transmitting, then the frequency.
  let detail;
  const call = S.selcallShown && performance.now() < S.selcallShown.until ? S.selcallShown.text
    : S.fxShown && performance.now() < S.fxShown.until ? S.fxShown.text : null;
  if (st.error) detail = st.error;
  else if (st.selcall_tx) detail = `Sending selcall ${st.selcall_tx}`;
  else if (st.clip_tx) detail = `Playing ${st.clip_tx.name}`;
  else if (p.active && !p.mine) detail = `TX by ${p.by}`;
  else if (st.stepping) detail = "Changing channel…";
  else if (call) detail = call;
  else if (picking != null) detail = pickedInfo && pickedInfo.freq ? pickedInfo.freq : "";
  else detail = freqText(st.info);

  const next = {
    zone: channelLabel(shownCh),
    channel: picking != null ? (pickedInfo ? pickedInfo.name : "") : st.channel_name,
    detail,
    status: status.join("  "),
    signal: signalBars(m),
    softkeys: headSoftkeys().map((k) => (k ? k.label : "")),
  };
  const changed = {};
  for (const [k, v] of Object.entries(next)) {
    const key = k === "softkeys" ? v.join("\n") : v;
    if (S.headShown[k] !== key) { S.headShown[k] = key; changed[k] = v; }
  }
  if (Object.keys(changed).length) S.head.setDisplay(changed);
  const box = $("#radio");
  box.classList.toggle("tx", p.mine);         // my transmission: red status text, red edge
  box.classList.toggle("picking", picking != null); // picked channel not on the radio yet
  box.classList.toggle("recording", !!st.recording); // REC in red, orange button lit
  renderRecording();
}

// Speaker volume is a playback gain from 0 to 2 (1 = as received). The slider and the head's
// volume knob both set it; whichever moved updates the other.
function setSpeakerVolume(gain, from) {
  if (from !== "slider") $("#vol").value = gain;
  if (from !== "head" && S.head) S.head.setVolume(gain * VOLUME_KNOB_UNITY);
  if (S.rxNode) S.rxNode.port.postMessage({ gain });
  if (S.rtcRxGain && !S.pttWanted) S.rtcRxGain.gain.value = gain; // stays muted while transmitting
}

// ---------- selcall ----------
// Sequential tone calls. The number is typed (or picked from a preset) in the Selcall panel and
// sent with the head's Call soft key. The server keys the radio, plays the tones and
// unkeys; it also decodes calls heard on the channel. Both kinds arrive as "selcall" messages.

const SELCALL_SHOW_MS = 10000; // how long a call stays on the head's bottom line

function setupSelcall(sc) {
  S.selcall = sc || null;
  $("#selcallPanel").hidden = !sc;
  if (!sc) return;
  $("#selcallDigits").maxLength = sc.max_digits;
  const std = `${sc.standard} ${sc.tone_ms} ms`;
  $("#selcallStd").textContent = sc.my_id ? `${std} · this radio ${sc.my_id}` : std;
  const presets = $("#selcallPresets");
  presets.replaceChildren(...sc.presets.map((p) => {
    const b = document.createElement("button");
    b.type = "button";
    const num = document.createElement("b");
    num.textContent = p.digits;
    b.append(p.name, num);
    b.addEventListener("click", () => { $("#selcallDigits").value = p.digits; });
    return b;
  }));
  S.selcallLog = sc.log.slice();
  renderSelcallLog();
}

function showSelcall() {
  $("#selcallPanel").scrollIntoView({ behavior: "smooth", block: "center" });
  $("#selcallDigits").focus({ preventScroll: true });
}

function sendSelcall() {
  if (!S.selcall || !S.authed) return;
  const digits = $("#selcallDigits").value.trim();
  if (!new RegExp(`^\\d{1,${S.selcall.max_digits}}$`).test(digits)) {
    toast(`Enter a selcall number (1–${S.selcall.max_digits} digits) first`);
    showSelcall();
    return;
  }
  const st = S.server;
  if (st && st.ptt.active) { toast(st.selcall_tx ? "A selcall is already being sent" : "Radio is transmitting"); return; }
  send({ type: "selcall", digits });
}

function onSelcall(ev) {
  S.selcallLog.push(ev);
  if (S.selcallLog.length > 20) S.selcallLog.shift();
  renderSelcallLog();
  if (ev.dir === "rx") {
    S.selcallShown = { text: ev.to_me ? `Call for this radio ${ev.digits}` : `Selcall ${ev.digits}`,
      until: performance.now() + SELCALL_SHOW_MS };
    toast(ev.to_me ? `Selcall for this radio (${ev.digits})` : `Selcall heard: ${ev.digits}`, ev.to_me ? "error" : "warn");
    if (ev.to_me) chime();
  } else {
    S.selcallShown = { text: `Sent selcall ${ev.digits}`, until: performance.now() + SELCALL_SHOW_MS / 2 };
  }
  updateHead();
}

function renderSelcallLog() {
  const list = $("#selcallLog");
  list.replaceChildren(...S.selcallLog.slice().reverse().map((ev) => {
    const li = document.createElement("li");
    li.classList.toggle("me", !!ev.to_me);
    li.title = "Use this number";
    const dir = document.createElement("span");
    dir.className = "dir";
    dir.textContent = ev.dir === "tx" ? "SENT" : "HEARD";
    const num = document.createElement("span");
    num.className = "num";
    num.textContent = ev.digits + (ev.to_me ? " (this radio)" : ev.by ? ` · ${ev.by}` : "");
    const when = document.createElement("span");
    when.className = "when";
    when.textContent = new Date(ev.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    li.append(dir, num, when);
    li.addEventListener("click", () => { $("#selcallDigits").value = ev.digits; });
    return li;
  }));
}

// Two short beeps through the page's audio, so a call for this radio is noticed.
function chime() {
  const ctx = S.ctx;
  if (!ctx || ctx.state !== "running") return;
  const t = ctx.currentTime;
  [0, 0.18].forEach((dt, i) => {
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.frequency.value = i ? 1320 : 880;
    g.gain.setValueAtTime(0, t + dt);
    g.gain.linearRampToValueAtTime(0.2, t + dt + 0.01);
    g.gain.linearRampToValueAtTime(0, t + dt + 0.14);
    osc.connect(g).connect(ctx.destination);
    osc.start(t + dt);
    osc.stop(t + dt + 0.15);
  });
}

// ---------- audio clips ----------
// Recordings kept on the Pi and transmitted with the head's Play soft key. The browser decodes
// each file itself (MP3, M4A, WAV, whatever it can play), mixes it to mono at the wire rate,
// trims silence off the ends and uploads it as a 16-bit WAV, so the Pi needs no audio codecs.
// The server keys the radio, plays the clip at an even level and unkeys. Any operator can stop it.

const CLIP_TRIM_LEVEL = 0.003; // about -50 dBFS: quieter lead-in and tail are trimmed off

function setupClips(cl) {
  S.clips = cl || null;
  $("#clipsPanel").hidden = !cl;
  if (!cl) return;
  $("#clipsInfo").textContent = `up to ${Math.round(cl.max_seconds)} s each`;
  $("#recordRow").hidden = !cl.record;
  S.clipSel = store.get("clip");
  setClipList(cl.list);
}

function setClipList(list) {
  S.clipList = list;
  if (!list.some((c) => c.id === S.clipSel)) S.clipSel = null; // deleted (here or by someone else)
  renderClips();
  updateHead();
}

function clipLength(s) {
  return s < 60 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
}

function renderClips() {
  if (!S.clips) return;
  const playing = S.server && S.server.clip_tx ? S.server.clip_tx.id : null;
  $("#clipEmpty").hidden = S.clipList.length > 0;
  $("#clipList").replaceChildren(...S.clipList.map((c) => {
    const li = document.createElement("li");
    li.classList.toggle("on", c.id === S.clipSel);
    li.classList.toggle("playing", c.id === playing);
    li.classList.toggle("rec", c.kind === "rec");
    const tooLong = c.seconds > S.clips.max_play_seconds;
    const label = document.createElement("label");
    const pick = document.createElement("input");
    pick.type = "radio";
    pick.name = "clip";
    pick.id = `clip-${c.id}`;
    pick.checked = c.id === S.clipSel;
    pick.disabled = tooLong; // still there to listen to or save
    if (tooLong) li.title = `Too long to transmit (over ${Math.round(S.clips.max_play_seconds)} s)`;
    pick.addEventListener("change", () => {
      S.clipSel = c.id;
      store.set("clip", c.id);
      renderClips();
    });
    const name = document.createElement("span");
    name.className = "clip-name";
    name.textContent = c.name;
    name.title = c.by ? `Added by ${c.by}` : "";
    const len = document.createElement("span");
    len.className = "clip-len";
    len.textContent = clipLength(c.seconds);
    label.append(pick, name, len);
    const listen = document.createElement("a");
    listen.className = "clip-listen";
    listen.href = `/clips/${c.id}.wav?token=${encodeURIComponent(S.token || "")}`;
    listen.target = "_blank";
    listen.rel = "noopener";
    listen.textContent = "Listen";
    listen.setAttribute("aria-label", `Listen to ${c.name}`);
    const del = document.createElement("button");
    del.type = "button";
    del.textContent = "Delete";
    del.setAttribute("aria-label", `Delete ${c.name}`);
    del.disabled = c.id === playing;
    del.addEventListener("click", () => {
      if (confirm(`Delete the clip “${c.name}” for everyone?`)) send({ type: "clip_delete", id: c.id });
    });
    li.append(label, listen, del);
    return li;
  }));
}

// ---------- voice effects ----------
// The Pi runs the effect on this operator's live voice while they transmit (not on clips or
// selcalls). The preset is picked in the panel and switched on and off with P / P1 (or the
// panel's button). Picking another preset while one is on switches straight to it.

const FX_SHOW_MS = 3000;

function setupFx(list) {
  S.fxList = list || null;
  $("#fxPanel").hidden = !list;
  if (!list) return;
  const saved = store.get("fx");
  S.fxSel = list.some((p) => p.id === saved) ? saved : list[0].id;
  $("#fxPresets").replaceChildren(...list.map((p) => {
    const b = document.createElement("button");
    b.type = "button";
    b.id = `fx-${p.id}`;
    b.textContent = p.name;
    b.addEventListener("click", () => pickFx(p.id));
    return b;
  }));
  renderFx();
}

function fxName(id) {
  const p = S.fxList && S.fxList.find((x) => x.id === id);
  return p ? p.name : id;
}

function pickFx(id) {
  S.fxSel = id;
  store.set("fx", id);
  if (S.server && S.server.fx && S.server.fx !== id) send({ type: "voice_fx", preset: id });
  renderFx();
}

function toggleFx() {
  if (!S.fxList || !S.authed) {
    toast("Voice effects aren't set up on this remote.");
    return;
  }
  const on = !(S.server && S.server.fx);
  send({ type: "voice_fx", preset: on ? S.fxSel : null });
  S.fxShown = { text: on ? `Voice: ${fxName(S.fxSel)}` : "Voice: normal", until: performance.now() + FX_SHOW_MS };
  updateHead();
}

function renderFx() {
  if (!S.fxList) return;
  const active = S.server && S.server.fx;
  for (const b of $("#fxPresets").children) {
    const id = b.id.slice(3);
    b.classList.toggle("sel", id === S.fxSel);
    b.classList.toggle("on", id === active);
    b.setAttribute("aria-pressed", String(id === S.fxSel));
  }
  const btn = $("#fxToggle");
  btn.textContent = active ? `${fxName(active)} on · switch off` : `Switch ${fxName(S.fxSel)} on`;
  btn.classList.toggle("on", !!active);
}

// ---------- recording ----------
// The Pi records received audio while recording is on, in parts of record_minutes, and each
// finished part joins the clip list. It keeps going with no one connected, and after a restart.

function toggleRecord() {
  if (!S.clips || !S.clips.record || !S.authed) {
    toast("Recording isn't set up on this remote.");
    return;
  }
  send({ type: "record", on: !(S.server && S.server.recording) });
}

function clock(s) {
  s = Math.floor(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

// The record line in the clips panel. Called from updateHead, so it ticks every 250 ms.
function renderRecording() {
  if (!S.clips || !S.clips.record) return;
  const rec = S.server && S.server.recording;
  const row = $("#recordRow");
  row.classList.toggle("on", !!rec);
  $("#recordBtn").textContent = rec ? "Stop recording" : "Record";
  if (!rec) {
    $("#recordState").textContent = S.role !== "control" ? "Not recording."
      : `Not recording. Press the orange button to record what the radio hears, in ${Number(S.clips.record_minutes.toFixed(1))} minute parts.`;
    return;
  }
  const t = S.recBase != null ? (performance.now() - S.recBase) / 1000 : rec.seconds;
  $("#recordState").textContent = `Recording ${clock(t)}${rec.by ? ` · started by ${rec.by}` : ""}`;
}

function showClips() {
  $("#clipsPanel").scrollIntoView({ behavior: "smooth", block: "center" });
}

// The Play soft key: transmit the picked clip, or stop the one on air.
function playClip() {
  if (!S.clips || !S.authed) return;
  const st = S.server;
  if (st && st.clip_tx) { send({ type: "clip_stop" }); return; }
  const clip = S.clipList.find((c) => c.id === S.clipSel);
  if (!clip) {
    toast(S.clipList.length ? "Pick a clip to play first" : "Upload a clip first");
    showClips();
    return;
  }
  if (st && st.ptt.active) { toast(st.selcall_tx ? "A selcall is being sent" : "Radio is transmitting"); return; }
  send({ type: "clip_play", id: clip.id });
}

// Decode a file and turn it into mono 16-bit WAV bytes at `rate`, with silent ends trimmed.
async function fileToWav(file, rate, maxSeconds) {
  const data = await file.arrayBuffer();
  const ctx = S.ctx || new OfflineAudioContext(1, 1, rate);
  let decoded;
  try {
    decoded = await ctx.decodeAudioData(data);
  } catch {
    throw new Error("this browser can't read that file as audio");
  }
  // Resample and mix down in one go: an offline context at the wire rate with one channel.
  const off = new OfflineAudioContext(1, Math.max(1, Math.ceil(decoded.duration * rate)), rate);
  const src = off.createBufferSource();
  src.buffer = decoded;
  src.connect(off.destination);
  src.start();
  const x = (await off.startRendering()).getChannelData(0);
  let a = 0, b = x.length;
  while (a < b && Math.abs(x[a]) < CLIP_TRIM_LEVEL) a++;
  while (b > a && Math.abs(x[b - 1]) < CLIP_TRIM_LEVEL) b--;
  if (a === b) throw new Error("it's silent");
  const pad = Math.round(rate * 0.02); // keep a moment either side so words aren't clipped
  const y = x.subarray(Math.max(0, a - pad), Math.min(x.length, b + pad));
  if (y.length / rate > maxSeconds + 0.05) {
    throw new Error(`it's ${clipLength(y.length / rate)} long; the limit is ${Math.round(maxSeconds)} s`);
  }
  const buf = new ArrayBuffer(44 + y.length * 2);
  const v = new DataView(buf);
  const text = (o, t) => { for (let i = 0; i < t.length; i++) v.setUint8(o + i, t.charCodeAt(i)); };
  text(0, "RIFF"); v.setUint32(4, 36 + y.length * 2, true); text(8, "WAVE");
  text(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  text(36, "data"); v.setUint32(40, y.length * 2, true);
  for (let i = 0; i < y.length; i++) v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, y[i])) * 32767, true);
  return buf;
}

async function uploadClips(files) {
  if (!S.clips || S.clipBusy || !files.length) return;
  S.clipBusy = true;
  $("#clipUpload").disabled = true;
  const progress = $("#clipProgress");
  let added = 0;
  try {
    for (const file of files) {
      const name = file.name.replace(/\.[^.]+$/, "");
      try {
        progress.textContent = `Converting ${name}…`;
        const wav = await fileToWav(file, S.wireRate, S.clips.max_seconds);
        progress.textContent = `Uploading ${name}…`;
        const res = await fetch(`/clips?name=${encodeURIComponent(name)}`, {
          method: "POST",
          headers: { "Content-Type": "audio/wav", "X-Radio-Token": S.token || "" },
          body: wav,
        });
        const reply = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(reply.error || `the server said HTTP ${res.status}`);
        added++;
        if (!S.clipSel) { S.clipSel = reply.id; store.set("clip", reply.id); }
      } catch (err) {
        toast(`Couldn't add ${name}: ${err.message}`, "error");
      }
    }
  } finally {
    S.clipBusy = false;
    $("#clipUpload").disabled = false;
    progress.textContent = added ? `Added ${added} clip${added > 1 ? "s" : ""}` : "";
    renderClips();
  }
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
    rtcStop();
    cancelPendingChannel();
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
        S.creds = null;
        if (m.code === "session") forgetSession(); // expired or ended elsewhere
        showLogin(m.error || "Login failed");
        return;
      }
      S.authed = true;
      S.token = m.token;
      S.reconnectDelay = 1000;
      if (m.session) {
        // A fresh login: from now on reconnect with the session token, never the password.
        S.session = m.session;
        S.creds = { session: m.session };
        if (S.remember) { store.set("session", m.session); store.set("sessionUser", m.name); }
      } else if (store.get("session")) {
        store.set("sessionUser", m.name);
      }
      $("#password").value = "";
      S.account = { username: m.username, display: m.name };
      S.role = m.role;
      $("#login").hidden = true;
      $("#head").hidden = false;
      applyRole();
      keepAwake(true);
      updateAudioHint();
      break;
    case "account":
      // An administrator changed this account while it was connected.
      S.account.display = m.display;
      if (m.role !== S.role) {
        S.role = m.role;
        if (m.role !== "control") pttRelease(false);
        toast(m.role === "control" ? "Your account now has full control" : "Your account is now listen-only");
      }
      applyRole();
      break;
    case "password":
      if (m.ok) {
        toast("Password changed. Other devices have been logged out.", "warn");
        $("#pwForm").reset();
      } else {
        toast(m.error || "Couldn't change the password", "error");
      }
      break;
    case "hello":
      S.channels = m.channels;
      S.radio = { type: m.radio_type || "buttons", canSync: m.can_sync !== false, canTune: !!m.can_tune, band: m.band };
      $("#syncBlock").hidden = !S.radio.canSync;
      $("#tuneForm").hidden = !S.radio.canTune;
      $("#tuneHint").textContent = S.radio.band ? `${S.radio.band[0]}–${S.radio.band[1]} MHz` : "";
      $("#radioName").textContent = m.radio_name;
      document.title = `${m.radio_name} · Radio Remote`;
      S.holdIntervalMs = m.hold_interval_ms;
      S.rtcAvailable = !!m.webrtc && "RTCPeerConnection" in window;
      S.wireRate = m.wire_rate;
      setupSelcall(m.selcall);
      setupClips(m.clips);
      setupFx(m.voice_fx);
      buildChannels();
      rtcStart();
      break;
    case "state":
      S.server = m;
      S.recBase = m.recording ? performance.now() - m.recording.seconds * 1000 : null;
      // A channel picked on the head is confirmed once the radio reports it and has settled.
      if (S.pending && S.pending.sent && m.channel === S.pending.n && !m.stepping) cancelPendingChannel();
      if (m.ptt.mine && m.ptt.remaining_s != null) S.txDeadline = performance.now() + m.ptt.remaining_s * 1000;
      if (!m.ptt.mine) S.txDeadline = null;
      render();
      break;
    case "meter":
      S.meter = m; // shown on the head: signal bars, BUSY and RSSI in dBm
      updateHead();
      break;
    case "selcall":
      onSelcall(m);
      break;
    case "clips":
      setClipList(m.list);
      break;
    case "rtc_answer":
      if (S.pc) S.pc.setRemoteDescription({ type: "answer", sdp: m.sdp }).catch((e) => rtcFallback(e.message));
      break;
    case "rtc_failed":
      rtcFallback(m.text);
      break;
    case "notice":
      toast(m.text, m.level);
      if (m.code === "logged_out") {
        S.manualClose = true;
        S.authed = false;
        forgetSession();
        showLogin(m.text);
      }
      if (m.code === "ptt_denied" || m.code === "ptt_dropped") pttRelease();
      if (S.pending && S.pending.sent) cancelPendingChannel(); // e.g. the channel change was refused
      break;
  }
}

// ---------- PTT ----------
function pttPress() {
  wakeAudio(); // a PTT press is a user gesture, so iOS will let us restart audio here
  if (S.pttWanted || !S.authed || !S.ws) return;
  if (S.role !== "control") { listenOnlyToast(); return; }
  if (!S.micOk) { toast("No microphone – can't transmit"); return; }
  if (!micLive()) { toast("Restarting microphone. Press PTT again."); return; }
  const st = S.server;
  if (st && st.ptt.active && !st.ptt.mine) { toast(`Radio in use by ${st.ptt.by}`); return; }
  if (st && st.stepping) { toast("Wait for channel change to finish"); return; }
  // A channel picked on the knob but not sent yet would make the screen show a channel the radio
  // isn't on. Drop the pick so the screen shows the channel this transmission really uses.
  if (S.pending && !S.pending.sent) cancelPendingChannel();
  S.pttWanted = true;
  clearTimeout(S.releaseTimer);
  if (S.txDest) S.txDest.stream.getAudioTracks()[0].enabled = true;
  if (S.rtcRxGain) S.rtcRxGain.gain.value = 0;
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
  // Keep sending a moment longer so the last syllable leaves the phone; the server waits for it.
  clearTimeout(S.releaseTimer);
  S.releaseTimer = setTimeout(() => {
    if (!S.pttWanted && S.txDest) S.txDest.stream.getAudioTracks()[0].enabled = false;
    if (!S.pttWanted && S.rtcRxGain) S.rtcRxGain.gain.value = Number($("#vol").value);
  }, 60);
  if (notify) send({ type: "ptt", on: false });
  render();
}

// Space is PTT unless it's typing into a field or pressing a focused control on the head.
function isTyping(el) {
  return el && (el.tagName === "INPUT" || el.tagName === "SELECT" || el.tagName === "TEXTAREA"
    || !!(el.closest && el.closest("#radio")));
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
    label.textContent = ch.freq ? `CH ${ch.n} · ${ch.freq}` : `CH ${ch.n}`;
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

  updateHead(); // channel, frequency, TX/CHG/BUSY, errors and signal go on the head's screen
  renderClips(); // marks the clip on air, and stops it being deleted meanwhile
  renderFx();

  const btn = $("#ptt");
  btn.classList.toggle("live", p.mine);
  btn.classList.toggle("pending", S.pttWanted && !p.mine);
  btn.classList.toggle("locked", otherTx || !S.micOk);
  $("#pttLabel").textContent = p.mine ? (st.selcall_tx ? "SELCALL" : st.clip_tx ? "CLIP" : "ON AIR")
    : otherTx ? "IN USE" : "PTT";

  $("#tuneForm").querySelector("button").disabled = busyRadio;
  for (const b of $("#channelGrid").children) {
    b.disabled = busyRadio || S.role !== "control";
    b.classList.toggle("current", Number(b.dataset.ch) === st.channel);
  }
  $("#operators").textContent = st.operators.join(", ") || "—";
}

// Time left before the server's transmit timeout, as m:ss. Shown after TX on the head.
function totText() {
  if (S.txDeadline == null) return "";
  const left = Math.max(0, Math.ceil((S.txDeadline - performance.now()) / 1000));
  return `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
}

// ---------- init ----------
function init() {
  // Earlier versions kept the password itself here. Remove it: sessions replace it.
  store.set("password", null);
  store.set("name", null);
  $("#secureHint").hidden = window.isSecureContext;
  $("#iosNote").hidden = !IS_IOS;
  showLogin();

  $("#loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    S.remember = $("#remember").checked;
    S.creds = { username: $("#username").value.trim(), password: $("#password").value, remember: S.remember };
    $("#loginError").hidden = true;
    await setupAudio(); // needs this click (user gesture)
    connect();
  });
  $("#resumeBtn").addEventListener("click", async () => {
    S.remember = true;
    S.creds = { session: store.get("session") };
    $("#loginError").hidden = true;
    await setupAudio(); // needs this tap (user gesture)
    connect();
  });
  $("#switchUser").addEventListener("click", () => { forgetSession(); showLogin(); $("#username").focus(); });
  $("#pwForm").addEventListener("submit", (e) => {
    e.preventDefault();
    send({ type: "change_password", old: $("#pwOld").value, new: $("#pwNew").value });
  });

  $("#syncBtn").addEventListener("click", () => {
    send({ type: "channel", action: "sync", channel: Number($("#syncSelect").value) });
    toast("Channel display updated");
  });
  $("#tuneForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const freq = parseFloat($("#tuneFreq").value);
    if (!Number.isFinite(freq)) { toast("Enter a frequency in MHz, e.g. 439.125"); return; }
    const tx = $("#tuneTx").value.trim();
    send({ type: "channel", action: "tune", freq, tx_freq: tx ? parseFloat(tx) : null, tone: $("#tuneTone").value.trim() || null });
  });
  $("#vol").addEventListener("input", (e) => setSpeakerVolume(Number(e.target.value), "slider"));
  $("#micGain").addEventListener("input", (e) => {
    const g = Number(e.target.value);
    if (S.txNode) S.txNode.port.postMessage({ gain: g });
    if (S.txGain) S.txGain.gain.value = g;
  });
  $("#disconnectBtn").addEventListener("click", disconnect);
  $("#logout").addEventListener("click", logout);
  // Selcall number: digits only (phones may offer other characters on the numeric keypad).
  $("#selcallDigits").addEventListener("input", (e) => {
    const clean = e.target.value.replace(/\D/g, "");
    if (clean !== e.target.value) e.target.value = clean;
  });

  $("#clipUpload").addEventListener("click", () => $("#clipFile").click());
  $("#recordBtn").addEventListener("click", toggleRecord);
  $("#fxToggle").addEventListener("click", toggleFx);
  $("#clipFile").addEventListener("change", (e) => {
    const files = [...e.target.files];
    e.target.value = ""; // so picking the same file again still fires change
    uploadClips(files);
  });

  bindPtt();
  setInterval(updateHead, 250); // counts down the TX timeout on the head's screen
  loadHead();
}

// ---------- account ----------
let lastListenToast = -Infinity;
function listenOnlyToast() {
  if (performance.now() - lastListenToast < 2500) return;
  lastListenToast = performance.now();
  toast("Listen-only account: you can't transmit or change the radio");
}

// Show or hide the controls for this account's role, and fetch the mic if it may transmit.
function applyRole() {
  const control = S.role === "control";
  document.body.classList.toggle("listen-only", !control);
  const name = S.account ? S.account.display : "";
  setStatus("online", `Online · ${name}${control ? "" : " (listening)"}`);
  $("#acctName").textContent = S.account ? `${name} (${S.account.username})` : "—";
  $("#acctRole").textContent = control ? "full control" : "listen only";
  $("#pwUser").value = S.account ? S.account.username : "";
  $("#clipEmpty").textContent = control ? "No clips yet. Upload an MP3 to add one." : "No clips yet.";
  if (control && !micLive()) setupMic();
  render();
  updateHead();
}

function forgetSession() {
  store.set("session", null);
  store.set("sessionUser", null);
  S.session = null;
}

// Settings → Log out: ends the session on the server too, so this device must log in again.
function logout() {
  send({ type: "logout" });
  forgetSession();
  S.creds = null;
  disconnect();
}

// Settings → Disconnect, or holding the head's power button. Stays logged in.
function disconnect() {
  S.manualClose = true;
  S.authed = false;
  pttRelease();
  if (S.ws) S.ws.close();
  keepAwake(false);
  updateAudioHint();
  setStatus("offline", "Offline");
  showLogin();
}

init();
