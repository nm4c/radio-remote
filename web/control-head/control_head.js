// control_head.js: input handling for the control head SVG.
// It reports what the operator did. Your app decides what each event means.
//
// Event shapes passed to radioHead.on(callback):
//   {control:"softkey_1", action:"press"}       Button went down
//   {control:"softkey_1", action:"release"}     Button came up
//   {control:"softkey_1", action:"long_press"}  Button held for longPressMs
//   {control:"emergency", action:"activate"}    Guarded button held for its full time
//   {control:"volume_knob", action:"change", value:42}  Absolute value, 0 to 100
//   {control:"channel_knob", action:"step", delta:1}    One detent, + is clockwise
const radioHead = (() => {
  const svg = document.querySelector("#radio svg");
  const listeners = new Set();
  const emit = event => listeners.forEach(fn => fn(event));

  // Converts a pointer position from screen pixels to base-canvas units.
  // This keeps knob angles correct at any rendered size.
  const toCanvas = e => {
    const point = new DOMPoint(e.clientX, e.clientY).matrixTransform(svg.getScreenCTM().inverse());
    return { x: point.x, y: point.y };
  };

  // ---------- Buttons ----------
  svg.querySelectorAll("[data-control]").forEach(el => {
    const control = el.dataset.control;
    const holdMs = RADIO_CONFIG.holdMs[control]; // Set only for guarded buttons.
    let timer = null;
    let down = false;

    el.setAttribute("role", "button");
    el.setAttribute("tabindex", "0");
    el.setAttribute("aria-label", control.replace(/_/g, " "));

    const start = () => {
      if (down) return; // Ignores keyboard auto-repeat.
      down = true;
      el.classList.add("pressed");
      if (holdMs) {
        // Guarded buttons send nothing unless held for the full time.
        timer = setTimeout(() => emit({ control, action: "activate" }), holdMs);
      } else {
        emit({ control, action: "press" });
        timer = setTimeout(() => emit({ control, action: "long_press" }), RADIO_CONFIG.longPressMs);
      }
    };
    const end = () => {
      if (!down) return;
      down = false;
      el.classList.remove("pressed");
      clearTimeout(timer);
      if (!holdMs) emit({ control, action: "release" });
    };

    el.addEventListener("pointerdown", e => { el.setPointerCapture(e.pointerId); start(); });
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end); // Also releases if the OS takes the pointer.
    el.addEventListener("blur", end);          // A key press must not stick on focus loss.
    el.addEventListener("keydown", e => {
      if (e.key === " " || e.key === "Enter") { e.preventDefault(); start(); }
    });
    el.addEventListener("keyup", e => {
      if (e.key === " " || e.key === "Enter") end();
    });
  });

  // ---------- Knobs ----------
  const setRotation = (knob, degrees) => {
    const { cx, cy } = knob.dataset;
    knob.querySelectorAll(".rotor").forEach(r => r.setAttribute("transform", `rotate(${degrees} ${cx} ${cy})`));
  };

  // Volume: fixed end stops, like the real pot.
  const volumeKnob = svg.querySelector('[data-knob="volume_knob"]');
  let volume = 0;
  const setVolume = (next, notify = true) => {
    const clamped = Math.round(Math.min(100, Math.max(0, next)));
    if (clamped === volume) return;
    volume = clamped;
    setRotation(volumeKnob, volume * 2.7); // 0 to 100 maps to 270 degrees.
    volumeKnob.setAttribute("aria-valuenow", volume);
    if (notify) emit({ control: "volume_knob", action: "change", value: volume });
  };

  // Channel: endless rotation with detents.
  const channelKnob = svg.querySelector('[data-knob="channel_knob"]');
  let channelAngle = 0;
  const stepChannel = delta => {
    channelAngle += delta * RADIO_CONFIG.detent;
    setRotation(channelKnob, channelAngle);
    emit({ control: "channel_knob", action: "step", delta });
  };

  // Shared drag handling. onTurn receives the signed degrees moved since last event.
  const bindKnob = (knob, label, onTurn, onNudge) => {
    knob.setAttribute("role", "slider");
    knob.setAttribute("tabindex", "0");
    knob.setAttribute("aria-label", label);
    const centre = { x: +knob.dataset.cx, y: +knob.dataset.cy };
    const angleAt = e => {
      const p = toCanvas(e);
      return Math.atan2(p.y - centre.y, p.x - centre.x) * 180 / Math.PI;
    };
    let last = null;
    knob.addEventListener("pointerdown", e => {
      knob.setPointerCapture(e.pointerId);
      last = angleAt(e);
    });
    knob.addEventListener("pointermove", e => {
      if (last === null) return;
      const now = angleAt(e);
      let delta = now - last;
      // Unwraps the jump where atan2 flips between +180 and -180.
      if (delta > 180) delta -= 360;
      if (delta < -180) delta += 360;
      last = now;
      onTurn(delta);
    });
    const stop = () => { last = null; };
    knob.addEventListener("pointerup", stop);
    knob.addEventListener("pointercancel", stop);
    // Wheel up and right/up arrows both count as clockwise.
    knob.addEventListener("wheel", e => {
      e.preventDefault();
      onNudge(e.deltaY < 0 ? 1 : -1);
    }, { passive: false });
    knob.addEventListener("keydown", e => {
      const direction = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1 }[e.key];
      if (direction) { e.preventDefault(); onNudge(direction); }
    });
  };

  // Keeps fractional drag movement, so slow turns still register.
  let volumeRemainder = 0;
  bindKnob(volumeKnob, "volume", degrees => {
    volumeRemainder += degrees / 2.7;
    const whole = Math.trunc(volumeRemainder);
    if (whole) { volumeRemainder -= whole; setVolume(volume + whole); }
  }, direction => setVolume(volume + direction * 2));
  volumeKnob.setAttribute("aria-valuemin", 0);
  volumeKnob.setAttribute("aria-valuemax", 100);
  volumeKnob.setAttribute("aria-valuenow", 0);

  let channelRemainder = 0;
  bindKnob(channelKnob, "channel", degrees => {
    channelRemainder += degrees;
    while (Math.abs(channelRemainder) >= RADIO_CONFIG.detent) {
      const direction = Math.sign(channelRemainder);
      channelRemainder -= direction * RADIO_CONFIG.detent;
      stepChannel(direction);
    }
  }, stepChannel);

  // ---------- Screen ----------
  // Sets text, then shrinks it if needed so long names stay inside their zone.
  const setText = (id, value) => {
    const el = svg.getElementById(id);
    if (!el || value === undefined) return;
    el.textContent = value;
    el.removeAttribute("textLength");
    el.removeAttribute("lengthAdjust");
    const max = +el.dataset.maxWidth;
    if (max && el.getComputedTextLength() > max) {
      el.setAttribute("textLength", max);
      el.setAttribute("lengthAdjust", "spacingAndGlyphs");
    }
  };

  return {
    on(callback) { listeners.add(callback); return () => listeners.delete(callback); },
    // Only the fields you pass are changed.
    setDisplay({ zone, channel, detail, status, signal, softkeys } = {}) {
      setText("display-zone", zone);
      setText("display-channel", channel);
      setText("display-detail", detail);
      setText("display-status", status);
      if (signal !== undefined) {
        svg.querySelectorAll(".signal-bar").forEach((bar, i) => {
          bar.setAttribute("fill", i < signal ? "#3ddc62" : "#2a2c2f");
        });
      }
      (softkeys || []).forEach((label, i) => setText(`display-softkey-${i + 1}`, label));
    },
    // Syncs the knob from the radio without echoing an event back.
    setVolume: value => setVolume(value, false),
  };
})();
