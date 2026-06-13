# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DXcontrol is a patch management tool for the **Yamaha reface DX** synthesizer. It sends SysEx voice patches to the synth over MIDI and captures the synth's current voice back to disk. There are two independent implementations that share the same logic:

- **Web app** (`index.html` + `app.js`): Chrome/Edge only (Web MIDI API), deployed to GitHub Pages or served locally
- **Desktop app** (`dxcontrol.py`): Windows, Python 3 + Tkinter

## Running the Apps

**Web app (local):**
```bash
python -m http.server
# Then open http://localhost:8000 in Chrome or Edge (not Firefox/Safari)
```

**Desktop app (Windows):**
```bash
pip install -r requirements.txt
py dxcontrol.py
```

**Regenerate manifest after adding/removing .syx files:**
```bash
py make_manifest.py
```

There are no automated tests or linting tools — testing requires a physical Yamaha reface DX connected via USB MIDI.

## Architecture

### Data flow: sending a patch
1. User picks a voice from the library list
2. App fetches the `.syx` file and splits it into individual F0..F7 SysEx messages
3. Messages are sent to the MIDI output port with a 20 ms inter-message delay (synth buffer management)
4. Optionally, a middle-C audition note plays after a 150 ms settle delay

### Data flow: loading from synth
1. App sends `DUMP_REQUEST` (`F0 43 20 7F 1C 05 0E 0F 00 F7`) to MIDI Out
2. Synth responds with 7 SysEx messages
3. App collects bytes until the footer block (address `0E 0F 00`) arrives or a 1 s idle timeout
4. **Desktop:** saves `.syx` + `.txt` to `Reface-User/SYX` and `Reface-User/TXT`, rescans library
5. **Web:** downloads `.syx` + `.txt` to the browser — user must manually drop them into the repo

### Voice format
A voice dump is exactly 7 SysEx messages. Each message follows:
```
F0 43 [device] 7F 1C [model=05] [addr3 bytes] [count_high count_low] [payload] [checksum] F7
```
Payloads in order: header, common, op1, op2, op3, op4, footer. The common payload holds the 10-char voice name.

### Mirrored logic (Python ↔ JavaScript)
The core voice encode/decode logic is intentionally duplicated between `dxcontrol.py` and `app.js` — no shared module exists. When changing any of the following, update **both files**:
- `splitSysex` / `split_sysex` — split raw bytes into F0..F7 messages; filters system real-time bytes (0xF8–0xFF)
- `voicePayloads` / `voice_payloads` — extract the 7 payloads from a dump
- `voiceName` / `voice_name` — read 10-char name from common payload
- `decodeVoice` / `decode_voice` — parse common params + 4 operators into a dict
- `formatVoiceSheet` / `format_voice_sheet` — render the parameter sheet as aligned text
- Frequency display math: ratio mode (`coarse + fine/100`) vs fixed mode (`10^(coarse/8 + fine/100)`)
- All timing constants (see table below)

### manifest.json
`manifest.json` is **generated** by `make_manifest.py` — never edit it manually. It indexes all `.syx` / `.txt` pairs across the four library folders so the web app can list patches without a server.

## Key Constants (shared between both implementations)

| Constant | Value | Purpose |
|---|---|---|
| `INTER_MESSAGE_DELAY` | 20 ms | Delay between SysEx messages to the synth |
| `LOAD_SETTLE` | 150 ms | Wait before playing audition note after voice loads |
| `AUDITION_NOTE` | 60 (middle C) | Note played for voice preview |
| `AUDITION_DURATION` | 1000 ms | How long the audition note plays |
| `DUMP_TIMEOUT` | 3000 ms | Max wait for first byte from synth after dump request |
| `DUMP_IDLE` | 1000 ms | Silence duration that ends voice capture |

## Patch Libraries

| Folder | Contents |
|---|---|
| `Reface-DX21/` | DX21 factory voices (~128 voices) |
| `Reface-DX9/` | DX9 factory voices |
| `Reface-TX81Z/` | TX81Z factory voices |
| `Reface-User/` | User-captured voices (writable by desktop app) |

Each library folder has `SYX/` (binary SysEx) and `TXT/` (human-readable parameter sheets) sub-folders. Patch files are named `[Code]-[VoiceName].syx` (e.g., `A01-Deep_Grand.syx`). User captures use plain `[VoiceName].syx` and auto-number duplicates (`_2`, `_3`, …).

## Web App Constraints

- Requires Chrome or Edge — Web MIDI API is not available in Firefox or Safari
- Must be served over HTTPS or `localhost` — `file://` URLs do not work
- The browser prompts for MIDI + SysEx permission on first use; auto-selects any port with "reface" in its name
