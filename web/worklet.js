// AudioWorklet processors for the remote head.
// Wire format: Int16 mono PCM at 16 kHz, 20 ms frames.

const WIRE_RATE = 16000;

// Plays RX audio from the radio. Jitter buffer + linear-interpolation resampler.
class RxPlayer extends AudioWorkletProcessor {
  constructor() {
    super();
    this.size = WIRE_RATE * 2;
    this.buf = new Float32Array(this.size);
    this.r = 0;
    this.w = 0;
    this.count = 0;
    this.frac = 0;
    this.step = WIRE_RATE / sampleRate;
    this.target = Math.round(WIRE_RATE * 0.1); // 100 ms jitter buffer
    this.maxLag = Math.round(WIRE_RATE * 0.4); // skip ahead if we fall 400 ms behind
    this.playing = false;
    this.gain = 1;
    this.port.onmessage = (e) => this.onMessage(e.data);
  }

  onMessage(d) {
    if (d instanceof ArrayBuffer) {
      this.push(new Int16Array(d));
      return;
    }
    if (d.gain !== undefined) this.gain = d.gain;
    if (d.flush) {
      this.r = this.w;
      this.count = 0;
      this.frac = 0;
      this.playing = false;
    }
  }

  push(pcm) {
    for (let i = 0; i < pcm.length; i++) {
      this.buf[this.w] = pcm[i] / 32768;
      this.w = (this.w + 1) % this.size;
      if (this.count < this.size) this.count++;
      else this.r = (this.r + 1) % this.size;
    }
    if (this.count > this.maxLag) {
      const drop = this.count - this.target;
      this.r = (this.r + drop) % this.size;
      this.count -= drop;
    }
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!this.playing && this.count >= this.target) this.playing = true;
    for (let i = 0; i < out.length; i++) {
      if (!this.playing || this.count < 2) {
        this.playing = false; // underrun: wait for the buffer to refill
        out[i] = 0;
        continue;
      }
      const a = this.buf[this.r];
      const b = this.buf[(this.r + 1) % this.size];
      out[i] = (a + (b - a) * this.frac) * this.gain;
      this.frac += this.step;
      while (this.frac >= 1) {
        this.frac -= 1;
        this.r = (this.r + 1) % this.size;
        this.count--;
      }
    }
    return true;
  }
}

// Captures the microphone, low-passes, downsamples to 16 kHz and posts Int16 frames.
class TxCapture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.frameSamples = (options.processorOptions && options.processorOptions.frameSamples) || 320;
    this.ratio = sampleRate / WIRE_RATE;
    this.phase = 0;
    this.a = 1 - Math.exp((-2 * Math.PI * 3400) / sampleRate); // 2-pole ~3.4 kHz low-pass
    this.s1 = 0;
    this.s2 = 0;
    this.frame = new Int16Array(this.frameSamples);
    this.n = 0;
    this.active = false;
    this.gain = 1;
    this.peak = 0;
    this.levelCountdown = 0;
    this.port.onmessage = (e) => {
      const d = e.data;
      if (d.gain !== undefined) this.gain = d.gain;
      if (d.active !== undefined) {
        this.active = d.active;
        this.n = 0;
      }
    };
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      const x = ch[i] * this.gain;
      this.s1 += this.a * (x - this.s1);
      this.s2 += this.a * (this.s1 - this.s2);
      const ax = x < 0 ? -x : x;
      if (ax > this.peak) this.peak = ax;
      this.phase += 1;
      if (this.phase >= this.ratio) {
        this.phase -= this.ratio;
        if (this.active) {
          const v = this.s2 > 1 ? 1 : this.s2 < -1 ? -1 : this.s2;
          this.frame[this.n++] = v * 32767;
          if (this.n === this.frameSamples) {
            const out = this.frame.slice();
            this.port.postMessage({ frame: out.buffer }, [out.buffer]);
            this.n = 0;
          }
        }
      }
    }
    this.levelCountdown -= ch.length;
    if (this.levelCountdown <= 0) {
      this.port.postMessage({ level: this.peak });
      this.peak = 0;
      this.levelCountdown = sampleRate / 15;
    }
    return true;
  }
}

registerProcessor("rx-player", RxPlayer);
registerProcessor("tx-capture", TxCapture);
