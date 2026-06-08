/* DXcontrol - web version.
 *
 * Sends reface DX SysEx bulk dumps to the synth via the Web MIDI API (Chrome),
 * and can request + decode the synth's current voice. The .syx files are native
 * reface DX dumps, so "sending" just streams their SysEx messages to the port.
 */

"use strict";

// --- constants (mirrors dxcontrol.py) -------------------------------------- //
const INTER_MESSAGE_DELAY = 20;   // ms between SysEx messages
const AUDITION_NOTE = 60;         // middle C
const AUDITION_VELOCITY = 100;
const AUDITION_DURATION = 1000;   // ms
const AUDITION_CHANNEL = 0;       // MIDI channel 1 (reface DX default)
const LOAD_SETTLE = 150;          // ms before the audition note
const CAPTURE_LIBRARY = "Reface-User";

// Bulk-dump request for the current voice (device 0x20 = ch 1, model 0x05).
const DUMP_REQUEST = [0xf0, 0x43, 0x20, 0x7f, 0x1c, 0x05, 0, 0, 0, 0xf7];
const FOOTER_ADDRESS = [0x0f, 0x0f, 0x00];
const DUMP_TIMEOUT = 3000;        // ms to wait for first data
const DUMP_IDLE = 1000;           // ms of silence that ends a capture

// --- enums for decoding ---------------------------------------------------- //
const PART_MODE = ["Poly", "Mono-Full", "Mono-Legato"];
const LFO_WAVE = ["Sine", "Triangle", "Sawtooth Up", "Sawtooth Down", "Square",
  "Sample & Hold 8", "Sample & Hold"];
const EFFECT_TYPE = ["Thru", "Distortion", "Touch Wah", "Chorus", "Flanger",
  "Phaser", "Delay", "Reverb"];
const KSC_CURVE = ["-LIN", "-EXP", "+EXP", "+LIN"];
const FB_TYPE = ["Sawtooth", "Square"];
const FREQ_MODE = ["Ratio", "Fixed"];
const EFFECT_PARAMS = {
  0: ["---", "---"], 1: ["Drive", "Tone"], 2: ["Freq", "Depth"],
  3: ["Depth", "Rate"], 4: ["Depth", "Rate"], 5: ["Depth", "Rate"],
  6: ["Time", "Feedback"], 7: ["Time", "Depth"],
};
const OP_HEADER = " ".repeat(16) + rjust("OP1", 9) + rjust("OP2", 9) +
  rjust("OP3", 9) + rjust("OP4", 9) + " ".repeat(5);

// --- small string helpers (Python str.rjust / ljust / rstrip) -------------- //
function rjust(s, n) { s = String(s); return s.length >= n ? s : " ".repeat(n - s.length) + s; }
function ljust(s, n) { s = String(s); return s.length >= n ? s : s + " ".repeat(n - s.length); }
function rstrip(s, ch) { let i = s.length; while (i > 0 && s[i - 1] === ch) i--; return s.slice(0, i); }
function enumName(table, i) { return (i >= 0 && i < table.length) ? table[i] : String(i); }
function toHex(bytes) {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(" ");
}

// --------------------------------------------------------------------------- //
// SysEx handling
// --------------------------------------------------------------------------- //
function splitSysex(data) {
  // data: Uint8Array or Array. Returns an array of Uint8Array messages (F0..F7).
  const messages = [];
  let start = null;
  for (let i = 0; i < data.length; i++) {
    if (data[i] === 0xf0) start = i;
    else if (data[i] === 0xf7 && start !== null) {
      messages.push(data.slice(start, i + 1));
      start = null;
    }
  }
  return messages;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function sendSyx(output, bytes, audition, onProgress) {
  const messages = splitSysex(bytes);
  if (messages.length === 0) throw new Error("No SysEx messages found in this file.");

  const total = messages.length;
  // Schedule with timestamps so timing is steady regardless of JS jitter.
  const t0 = performance.now() + 30;
  for (let i = 0; i < total; i++) {
    output.send(Array.from(messages[i]), t0 + i * INTER_MESSAGE_DELAY);
    if (onProgress) onProgress(i + 1, total);
  }
  const end = t0 + (total - 1) * INTER_MESSAGE_DELAY;
  if (audition) {
    const noteAt = end + LOAD_SETTLE;
    output.send([0x90 | AUDITION_CHANNEL, AUDITION_NOTE, AUDITION_VELOCITY], noteAt);
    output.send([0x80 | AUDITION_CHANNEL, AUDITION_NOTE, 0], noteAt + AUDITION_DURATION);
    await sleep(noteAt + AUDITION_DURATION - performance.now() + 30);
  } else {
    await sleep(end - performance.now() + 30);
  }
  return total;
}

function requestCurrentVoice(input, output) {
  // Resolves with a Uint8Array of the concatenated voice dump.
  //
  // Web MIDI may split one SysEx message across several MIDIMessageEvents, and
  // continuation events do NOT start with 0xF0. So we append the bytes of every
  // event into one buffer (rather than treating each event as a whole message)
  // and split it into F0..F7 messages only once the dump is complete.
  return new Promise((resolve, reject) => {
    const buf = [];
    let events = 0;
    let started = false;
    let idleTimer = null;
    let overallTimer = null;

    const cleanup = () => {
      input.onmidimessage = prevHandler;
      if (idleTimer) clearTimeout(idleTimer);
      if (overallTimer) clearTimeout(overallTimer);
    };
    const fail = () => {
      cleanup();
      reject(new Error(
        "No data received from the synth. Check the MIDI In port and that " +
        "the reface DX is connected and powered on."));
    };
    const succeed = () => {
      cleanup();
      resolve({ bytes: Uint8Array.from(buf), events });
    };

    // The 7-message voice dump is complete once we have 7 whole messages, or
    // the footer block (address 0F 0F 00) has arrived.
    const isComplete = () => {
      const msgs = splitSysex(buf);
      if (msgs.length >= 7) return true;
      return msgs.some((m) => m.length >= 11 && m[8] === FOOTER_ADDRESS[0] &&
        m[9] === FOOTER_ADDRESS[1] && m[10] === FOOTER_ADDRESS[2]);
    };

    const prevHandler = input.onmidimessage;
    input.onmidimessage = (e) => {
      started = true;
      events += 1;
      console.log(`[DXcontrol] MIDI in event ${events}: ${e.data.length} bytes`,
        toHex(e.data));
      for (const b of e.data) buf.push(b);
      if (overallTimer) { clearTimeout(overallTimer); overallTimer = null; }
      if (idleTimer) clearTimeout(idleTimer);
      if (isComplete()) { succeed(); return; }
      idleTimer = setTimeout(() => (buf.length ? succeed() : fail()), DUMP_IDLE);
    };

    overallTimer = setTimeout(() => { if (!started) fail(); }, DUMP_TIMEOUT);
    output.send(DUMP_REQUEST);
  });
}

// --------------------------------------------------------------------------- //
// Decoding a voice dump into a parameter sheet (mirrors dxcontrol.py)
// --------------------------------------------------------------------------- //
function voicePayloads(syxBytes) {
  const msgs = splitSysex(syxBytes);
  if (msgs.length !== 7)
    throw new Error(`Expected 7 SysEx messages in a voice, got ${msgs.length}.`);
  return msgs.map((m) => {
    const count = (m[5] << 7) | m[6];
    return m.slice(11, 11 + count - 4);
  });
}

function voiceName(syxBytes) {
  const common = voicePayloads(syxBytes)[1];
  let name = "";
  for (let i = 0; i < 10; i++) {
    const code = common[i];
    name += (code >= 32 && code < 127) ? String.fromCharCode(code) : " ";
  }
  return name.replace(/\s+$/, "");
}

function decodeVoice(syxBytes) {
  const payloads = voicePayloads(syxBytes);
  const common = payloads[1];
  const ops = payloads.slice(2, 6);

  const c = {
    name: voiceName(syxBytes),
    transpose: common[0x0c] - 64,
    part_mode: enumName(PART_MODE, common[0x0d]),
    porta: common[0x0e],
    pb_range: common[0x0f] - 64,
    algorithm: common[0x10] + 1,
    lfo_wave: enumName(LFO_WAVE, common[0x11]),
    lfo_speed: common[0x12],
    lfo_delay: common[0x13],
    lfo_pmd: common[0x14],
    peg_rate: [0, 1, 2, 3].map((i) => common[0x15 + i]),
    peg_level: [0, 1, 2, 3].map((i) => common[0x19 + i] - 64),
    fx1_type: common[0x1d], fx1_p1: common[0x1e], fx1_p2: common[0x1f],
    fx2_type: common[0x20], fx2_p1: common[0x21], fx2_p2: common[0x22],
  };

  const oplist = ops.map((op) => ({
    enable: op[0x00],
    eg_rate: [0, 1, 2, 3].map((i) => op[0x01 + i]),
    eg_level: [0, 1, 2, 3].map((i) => op[0x05 + i]),
    ksc_rate: op[0x09], ksc_ld: op[0x0a], ksc_rd: op[0x0b],
    ksc_lc: op[0x0c], ksc_rc: op[0x0d],
    lfo_amd: op[0x0e], lfo_pm: op[0x0f], peg_pm: op[0x10],
    velo: op[0x11], level: op[0x12], feedback: op[0x13],
    fb_type: op[0x14], freq_mode: op[0x15],
    coarse: op[0x16], fine: op[0x17], detune: op[0x18] - 64,
  }));

  return { common: c, ops: oplist };
}

function ratioStr(coarse, fine) {
  let s;
  if (coarse === 0) s = rstrip((0.5 + fine / 200.0).toFixed(3), "0");
  else s = rstrip((coarse + fine / 100.0).toFixed(2), "0");
  return s.endsWith(".") ? s + "0" : s;
}

function fixedFreqStr(coarse, fine) {
  const val = Math.pow(10.0, coarse / 8.0 + fine / 100.0);
  return rstrip(rstrip(val.toFixed(3), "0"), ".");
}

function freqStr(op) {
  return op.freq_mode === 1 ? fixedFreqStr(op.coarse, op.fine)
                            : ratioStr(op.coarse, op.fine);
}

function formatVoiceSheet(voice) {
  const c = voice.common, ops = voice.ops;
  const out = [];
  const cl = (label, value, annot) => {
    const s = ljust(label, 15) + "= " + rjust(String(value), 8);
    return annot !== undefined ? s + ` (${annot})` : s;
  };
  const ol = (label, values) =>
    ljust(label, 15) + "=" + values.map((v) => rjust(String(v), 9)).join("");

  out.push(ljust("VOICE NAME", 15) + "= " + c.name);
  out.push("=".repeat(28));
  out.push(cl("TRANSPOSE", c.transpose));
  out.push(cl("MONO/POLY", c.part_mode));
  out.push(cl("PORTA TIME", c.porta));
  out.push(cl("PB RANGE", c.pb_range));
  out.push(cl("ALGORITHM", c.algorithm));
  out.push(cl("LFO WAVE", c.lfo_wave));
  out.push(cl("LFO SPEED", c.lfo_speed));
  out.push(cl("LFO DELAY", c.lfo_delay));
  out.push(cl("LFO PMD", c.lfo_pmd));
  for (let i = 0; i < 4; i++) out.push(cl(`PEG RATE ${i + 1}`, c.peg_rate[i]));
  for (let i = 0; i < 4; i++) out.push(cl(`PEG LEVEL ${i + 1}`, c.peg_level[i]));
  for (const [fx, t, p1, p2] of [
    ["FX1", c.fx1_type, c.fx1_p1, c.fx1_p2],
    ["FX2", c.fx2_type, c.fx2_p1, c.fx2_p2],
  ]) {
    const pn = EFFECT_PARAMS[t] || ["P1", "P2"];
    out.push(cl(`${fx} TYPE`, t, enumName(EFFECT_TYPE, t)));
    out.push(cl(`${fx} PARAM 1`, p1, pn[0]));
    out.push(cl(`${fx} PARAM 2`, p2, pn[1]));
  }

  out.push("");
  out.push(OP_HEADER);
  out.push("-".repeat(52));
  out.push(ol("OP Off/On", ops.map((o) => o.enable ? "On" : "Off")));
  for (let i = 0; i < 4; i++) out.push(ol(`EG RATE ${i + 1}`, ops.map((o) => o.eg_rate[i])));
  for (let i = 0; i < 4; i++) out.push(ol(`EG LEVEL ${i + 1}`, ops.map((o) => o.eg_level[i])));
  out.push(ol("RATE SCALING", ops.map((o) => o.ksc_rate)));
  out.push(ol("SCALING LD", ops.map((o) => o.ksc_ld)));
  out.push(ol("SCALING RD", ops.map((o) => o.ksc_rd)));
  out.push(ol("SCALING LC", ops.map((o) => enumName(KSC_CURVE, o.ksc_lc))));
  out.push(ol("SCALING RC", ops.map((o) => enumName(KSC_CURVE, o.ksc_rc))));
  out.push(ol("LFO AMD", ops.map((o) => o.lfo_amd)));
  out.push(ol("LFO PMD Off/On", ops.map((o) => o.lfo_pm ? "On" : "Off")));
  out.push(ol("PEG Off/On", ops.map((o) => o.peg_pm ? "On" : "Off")));
  out.push(ol("VELO SENS", ops.map((o) => o.velo)));
  out.push(ol("OUT LEVEL", ops.map((o) => o.level)));
  out.push(ol("FEEDBACK", ops.map((o) => o.feedback)));
  out.push(ol("FB TYPE", ops.map((o) => enumName(FB_TYPE, o.fb_type))));
  out.push(ol("FREQ MODE", ops.map((o) => enumName(FREQ_MODE, o.freq_mode))));
  out.push(ol("RATIO | FREQ", ops.map((o) => freqStr(o))));
  out.push(ol("   freq coarse", ops.map((o) => o.coarse)));
  out.push(ol("   freq fine", ops.map((o) => o.fine)));
  out.push(ol("FREQ DETUNE", ops.map((o) => o.detune)));
  return out.join("\n") + "\n";
}

function nameToFilename(name) {
  let safe = "";
  for (const ch of name)
    safe += (/[a-zA-Z0-9 _-]/.test(ch)) ? ch : "_";
  safe = safe.trim().replace(/ /g, "_");
  return safe || "Voice";
}

// --------------------------------------------------------------------------- //
// UI
// --------------------------------------------------------------------------- //
const el = {
  library: document.getElementById("library"),
  midiOut: document.getElementById("midiOut"),
  midiIn: document.getElementById("midiIn"),
  refresh: document.getElementById("refresh"),
  audition: document.getElementById("audition"),
  load: document.getElementById("load"),
  voices: document.getElementById("voices"),
  detail: document.getElementById("detail"),
  status: document.getElementById("status"),
};

let midiAccess = null;
let libraries = {};        // { libName: [ {name, syx, txt}, ... ] }
let selectedIndex = -1;
let busy = false;

function setStatus(msg, isError = false) {
  el.status.textContent = msg;
  el.status.classList.toggle("error", isError);
}

function preferReface(items, valueOf) {
  const hit = items.find((it) => valueOf(it).toLowerCase().includes("reface"));
  return hit || items[0];
}

function fillSelect(select, entries, getValue, getLabel, preferredValue) {
  select.innerHTML = "";
  for (const entry of entries) {
    const opt = document.createElement("option");
    opt.value = getValue(entry);
    opt.textContent = getLabel(entry);
    select.appendChild(opt);
  }
  if (preferredValue !== undefined && preferredValue !== null)
    select.value = preferredValue;
}

function refreshPorts(initial = false) {
  if (!midiAccess) return;
  const outs = [...midiAccess.outputs.values()];
  const ins = [...midiAccess.inputs.values()];

  fillSelect(el.midiOut, outs, (p) => p.id, (p) => p.name,
    outs.length ? preferReface(outs, (p) => p.name).id : undefined);
  fillSelect(el.midiIn, ins, (p) => p.id, (p) => p.name,
    ins.length ? preferReface(ins, (p) => p.name).id : undefined);

  if (!outs.length && !initial) setStatus("No MIDI output ports found.", true);
}

function currentVoices() {
  return libraries[el.library.value] || [];
}

function populateList() {
  el.voices.innerHTML = "";
  selectedIndex = -1;
  currentVoices().forEach((patch, i) => {
    const li = document.createElement("li");
    li.textContent = patch.name;
    li.addEventListener("click", () => onSelect(i));
    el.voices.appendChild(li);
  });
  el.detail.textContent = "(no voice selected)";
}

function markSelected(i) {
  [...el.voices.children].forEach((li, idx) =>
    li.classList.toggle("selected", idx === i));
  selectedIndex = i;
}

async function showDetails(patch) {
  if (!patch.txt) { el.detail.textContent = "(no description file available for this patch)"; return; }
  try {
    const resp = await fetch(patch.txt);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    el.detail.textContent = await resp.text();
  } catch (err) {
    el.detail.textContent = `(could not read description: ${err.message})`;
  }
}

async function onSelect(i) {
  const patch = currentVoices()[i];
  if (!patch) return;
  markSelected(i);
  showDetails(patch);

  if (busy) return;
  const out = midiAccess && midiAccess.outputs.get(el.midiOut.value);
  if (!out) {
    setStatus("No MIDI output port selected. Connect your reface DX, click " +
      "Refresh and pick its port.", true);
    return;
  }

  busy = true;
  setStatus(`Sending '${patch.name}'...`);
  try {
    const buf = await (await fetch(patch.syx)).arrayBuffer();
    await sendSyx(out, new Uint8Array(buf), el.audition.checked,
      (sent, total) => setStatus(`Sending '${patch.name}'... message ${sent}/${total}`));
    setStatus(`Sent '${patch.name}'. Loaded into the edit buffer - ` +
      "press Store on the synth to keep it.");
  } catch (err) {
    setStatus(`Could not send '${patch.name}': ${err.message}`, true);
  } finally {
    busy = false;
  }
}

function download(filename, data, mime) {
  const blob = new Blob([data], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function onLoad() {
  if (busy) return;
  const input = midiAccess && midiAccess.inputs.get(el.midiIn.value);
  const output = midiAccess && midiAccess.outputs.get(el.midiOut.value);
  if (!input || !output) {
    setStatus("Both a MIDI In and a MIDI Out port are needed to load from the " +
      "synth. Connect the reface DX, click Refresh, and pick its ports.", true);
    return;
  }

  busy = true;
  el.load.disabled = true;
  setStatus("Requesting current voice from synth...");
  let received = null;
  try {
    received = await requestCurrentVoice(input, output);
    const syx = received.bytes;
    const sheet = formatVoiceSheet(decodeVoice(syx));
    const name = voiceName(syx) || "Voice";
    const stem = nameToFilename(name);
    el.detail.textContent = sheet;
    download(stem + ".syx", syx, "application/octet-stream");
    download(stem + ".txt", sheet, "text/plain");
    setStatus(`Loaded '${name}' from synth - downloaded ${stem}.syx and ` +
      `${stem}.txt. Drop them into a ${CAPTURE_LIBRARY}/SYX and /TXT folder ` +
      "to add them to your library.");
  } catch (err) {
    // Surface what actually arrived so a failed capture can be diagnosed.
    let diag = "";
    if (received) {
      const hex = toHex(received.bytes);
      console.log(`[DXcontrol] dump failed: ${received.bytes.length} bytes in ` +
        `${received.events} event(s). Full hex:\n${hex}`);
      diag = ` [received ${received.bytes.length} bytes in ${received.events} ` +
        `MIDI event(s); see DevTools console (F12) for the full hex]`;
    }
    setStatus(`Could not load from the synth: ${err.message}${diag}`, true);
  } finally {
    busy = false;
    el.load.disabled = false;
  }
}

const VERSION = "2026-06-08c";

async function init() {
  console.log(`[DXcontrol] app.js version ${VERSION}`);
  // Load the patch manifest.
  try {
    const resp = await fetch("manifest.json");
    libraries = (await resp.json()).libraries;
  } catch (err) {
    setStatus("Could not load manifest.json: " + err.message, true);
    return;
  }

  fillSelect(el.library, Object.keys(libraries), (n) => n, (n) => n);
  populateList();
  el.library.addEventListener("change", populateList);
  el.refresh.addEventListener("click", () => refreshPorts());
  el.load.addEventListener("click", onLoad);

  // Request MIDI access (SysEx required). Needs HTTPS or localhost.
  if (!navigator.requestMIDIAccess) {
    setStatus("This browser has no Web MIDI support. Use Chrome (or Edge) over " +
      "https:// or http://localhost.", true);
    return;
  }
  try {
    midiAccess = await navigator.requestMIDIAccess({ sysex: true });
    midiAccess.onstatechange = () => refreshPorts();
    refreshPorts(true);
    setStatus("Select a voice to send it.");
  } catch (err) {
    setStatus("MIDI access denied: " + err.message +
      " (Web MIDI needs https:// or http://localhost and your permission.)", true);
  }
}

init();
