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
// The radio-style head at the top of the page. Its artwork is web/control-head/control-head.svg,
// generated by assets/control-head/build_template.py. control_head.js turns presses and knob turns
// into events (radioHead.on) and fills the screen (radioHead.setDisplay). This section maps
// those events onto the same radio controls the rest of the page uses:
//
//   channel_knob  step      pick a channel; one "goto" is sent when the knob stops turning
//   dpad_up/down  press     the same, one channel per press
//   volume_knob   change    speaker volume, kept in step with the Speaker volume slider
//   power         activate  disconnect (only after the hold time in RADIO_CONFIG.holdMs)
//   emergency     activate  shows a notice only. Not wired to anything that transmits.
//   softkey_1     press     scroll to the channel list
//   softkey_2     press     the Tune form (SA818) or channel sync (button-pressing radios)
//   softkey_5     press     open Settings
//   P, home, brightness, D-pad left/right, softkeys 3 and 4: not assigned yet.

const HEAD_DIR = "/static/control-head";
const CHANNEL_SETTLE_MS = 400; // knob idle time before the picked channel is sent
const PENDING_TIMEOUT_MS = 5000; // forget a sent pick if the server never confirms it
const VOLUME_KNOB_UNITY = 50; // knob 0-100 -> speaker gain 0-2, so mid-travel is gain 1

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = src;
    el.onload = resolve;
    el.onerror = () => reject(new Error(`can't load ${src}`));
    document.head.appendChild(el);
  });
}

// Insert the head's SVG, then load its scripts. control_head.js finds "#radio svg" as soon as it
// runs, so the SVG must be in the page first. RADIO_CONFIG comes from the generated radio_config.js.
async function loadHead() {
  const box = $("#radio");
  try {
    const res = await fetch(`${HEAD_DIR}/control-head.svg`);
    if (!res.ok) throw new Error(`control-head.svg: HTTP ${res.status}`);
    box.innerHTML = await res.text(); // our own generated file, same origin
    await loadScript(`${HEAD_DIR}/radio_config.js`);
    await loadScript(`${HEAD_DIR}/control_head.js`);
  } catch (err) {
    console.error("Control head unavailable:", err);
    box.hidden = true;
    toast("The radio display couldn't load. Reload the page to try again.", "error");
    return;
  }
  fitHead(box.querySelector("svg"));
  S.head = radioHead; // global const defined by control_head.js
  S.head.setVolume(Number($("#vol").value) * VOLUME_KNOB_UNITY);
  S.head.on(onHeadEvent);
  updateHead();
}

// Phones crop the head to its screen, channel knob and D-pad (the view box comes from the
// generator), so the text stays readable. Knob angles stay right: control_head.js maps
// pointer positions through the SVG's own transform.
function fitHead(svg) {
  const full = svg.getAttribute("viewBox");
  const compact = svg.dataset.compactViewbox;
  const narrow = matchMedia("(max-width: 600px)");
  const apply = () => {
    const box = narrow.matches && compact ? compact : full;
    const [, , w, h] = box.split(" ");
    svg.setAttribute("viewBox", box);
    // width/height set the aspect ratio CSS uses for height:auto, so they follow the view box.
    svg.setAttribute("width", w);
    svg.setAttribute("height", h);
  };
  apply();
  narrow.addEventListener("change", apply);
}

function onHeadEvent(e) {
  switch (`${e.control}:${e.action}`) {
    case "channel_knob:step": headPickChannel(e.delta); break;
    case "dpad_up:press": headPickChannel(1); break;
    case "dpad_down:press": headPickChannel(-1); break;
    case "volume_knob:change": setSpeakerVolume(e.value / VOLUME_KNOB_UNITY, "head"); break;
    case "power:activate": disconnect(); break;
    case "emergency:activate":
      // Deliberately inert. Agree what it should do before wiring it to anything that keys the radio.
      toast("Emergency isn't set up on this remote.");
      break;
    case "softkey_1:press": $("#channelGrid").scrollIntoView({ behavior: "smooth", block: "center" }); break;
    case "softkey_2:press":
      if (S.radio.canTune) {
        $("#tuneForm").scrollIntoView({ behavior: "smooth", block: "center" });
        $("#tuneFreq").focus({ preventScroll: true });
      } else if (S.radio.canSync) {
        $("#settings").open = true;
        $("#syncBlock").scrollIntoView({ behavior: "smooth", block: "center" });
      }
      break;
    case "softkey_5:press":
      $("#settings").open = true;
      $("#settings").scrollIntoView({ behavior: "smooth", block: "start" });
      break;
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

  // Bottom line: problems first, then who is transmitting, then the frequency.
  let detail;
  if (st.error) detail = st.error;
  else if (p.active && !p.mine) detail = `TX by ${p.by}`;
  else if (st.stepping) detail = "Changing channel…";
  else if (picking != null) detail = pickedInfo && pickedInfo.freq ? pickedInfo.freq : "";
  else detail = freqText(st.info);

  const next = {
    zone: channelLabel(shownCh),
    channel: picking != null ? (pickedInfo ? pickedInfo.name : "") : st.channel_name,
    detail,
    status: status.join("  "),
    signal: signalBars(m),
    softkeys: ["Chans", S.radio.canTune ? "Tune" : S.radio.canSync ? "Sync" : "", "", "", "Setup"],
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
}

// Speaker volume is a playback gain from 0 to 2 (1 = as received). The slider and the head's
// volume knob both set it; whichever moved updates the other.
function setSpeakerVolume(gain, from) {
  if (from !== "slider") $("#vol").value = gain;
  if (from !== "head" && S.head) S.head.setVolume(gain * VOLUME_KNOB_UNITY);
  if (S.rxNode) S.rxNode.port.postMessage({ gain });
  if (S.rtcRxGain && !S.pttWanted) S.rtcRxGain.gain.value = gain; // stays muted while transmitting
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
      S.radio = { type: m.radio_type || "buttons", canSync: m.can_sync !== false, canTune: !!m.can_tune, band: m.band };
      $("#syncBlock").hidden = !S.radio.canSync;
      $("#tuneForm").hidden = !S.radio.canTune;
      $("#tuneHint").textContent = S.radio.band ? `${S.radio.band[0]}–${S.radio.band[1]} MHz` : "";
      $("#radioName").textContent = m.radio_name;
      document.title = `${m.radio_name} · Radio Remote`;
      S.holdIntervalMs = m.hold_interval_ms;
      S.rtcAvailable = !!m.webrtc && "RTCPeerConnection" in window;
      buildChannels();
      rtcStart();
      break;
    case "state":
      S.server = m;
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
    case "rtc_answer":
      if (S.pc) S.pc.setRemoteDescription({ type: "answer", sdp: m.sdp }).catch((e) => rtcFallback(e.message));
      break;
    case "rtc_failed":
      rtcFallback(m.text);
      break;
    case "notice":
      toast(m.text, m.level);
      if (m.code === "ptt_denied" || m.code === "ptt_dropped") pttRelease();
      if (S.pending && S.pending.sent) cancelPendingChannel(); // e.g. the channel change was refused
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

  const btn = $("#ptt");
  btn.classList.toggle("live", p.mine);
  btn.classList.toggle("pending", S.pttWanted && !p.mine);
  btn.classList.toggle("locked", otherTx || !S.micOk);
  $("#pttLabel").textContent = p.mine ? "ON AIR" : otherTx ? "IN USE" : "PTT";

  $("#tuneForm").querySelector("button").disabled = busyRadio;
  for (const b of $("#channelGrid").children) {
    b.disabled = busyRadio;
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
  $("#logout").addEventListener("click", disconnect);

  bindPtt();
  setInterval(updateHead, 250); // counts down the TX timeout on the head's screen
  loadHead();
}

// Settings → Disconnect, or holding the head's power button.
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
