# DXcontrol

A tiny app to send patches to a **Yamaha reface DX** over MIDI — and to pull the
synth's current voice back into a parameter sheet. It comes in two forms:

- **Web app** (`index.html`) — runs in Chrome via the Web MIDI API, hostable on
  GitHub Pages. No install.
- **Windows desktop app** (`dxcontrol.py`) — the original Tkinter program.

The `.syx` files in the library folders (`Reface-DX21`, `Reface-DX9`,
`Reface-TX81Z`) are already native reface DX SysEx bulk dumps, so DXcontrol just
streams them straight to your synth — no conversion involved.

## Web app

The web app is a set of static files (`index.html`, `app.js`, `style.css`,
`manifest.json`) plus the library folders. It uses the **Web MIDI API**, which
works in **Chrome and Edge** and requires a secure context — i.e. `https://` (such
as GitHub Pages) or `http://localhost`. Opening `index.html` directly as a
`file://` URL will not work.

### Hosting on GitHub Pages

1. Push this folder to your GitHub repository.
2. In the repo, go to **Settings → Pages**, set **Source** to *Deploy from a
   branch*, pick your branch (e.g. `main`) and the `/ (root)` folder, then
   **Save**.
3. After a minute the site is live at `https://<user>.github.io/<repo>/`.
4. Open it in Chrome, allow the **MIDI / SysEx** permission prompt, pick the
   *reface* port, and click a voice.

### Using it locally

From this folder: `py -m http.server` then open `http://localhost:8000` in Chrome.

### Load current voice (web)

The web app can request the synth's current voice, show its parameter sheet, and
**download** it as a matching `.syx` + `.txt` (a browser page can't write into the
repo). To add a captured voice to your library, drop those two files into a
`Reface-User/SYX` and `Reface-User/TXT` folder and re-run `py make_manifest.py`,
then commit.

### Updating the patch index

`manifest.json` lists every library and voice (GitHub Pages can't list folders).
Regenerate it after adding or removing `.syx` files:

```
py make_manifest.py
```

## Windows desktop app

## How to use

1. **Connect** the reface DX to your PC (USB cable, or a USB-MIDI interface on the
   5-pin MIDI IN). Turn it on.
2. **Double-click `start.bat`.** The first time, it installs the two small MIDI
   libraries it needs (`mido` and `python-rtmidi`); after that it just opens.
3. In the window:
   - Pick a **Library** (DX21 / DX9 / TX81Z, or your captured **Reface-User** voices).
   - Pick the **MIDI Out** (and **MIDI In**) port — anything named *reface* is
     selected automatically. Click **Refresh** if you plugged in after opening.
   - **Click a voice** on the left: it is sent to the synth immediately, its
     parameter sheet shows on the right, and (if **Audition** is ticked) it plays
     for one second.

## Sending vs. auditioning

- Selecting a voice **sends it to the synth's edit buffer**. Play it to audition,
  or leave **Audition (play 1s)** ticked to hear it automatically.
- The voice is in the edit buffer only — press **Store** on the reface DX itself to
  keep it in a user slot.

## Load current voice from synth

Click **Load current voice from synth** to request whatever voice is active on the
reface DX. DXcontrol captures the SysEx dump, decodes it into a parameter sheet, and
saves it under a **`Reface-User`** library as both a `.syx` (re-sendable) and a
matching `.txt`. The new voice is selected automatically so you can review it.

## Look & feel

Dark text areas (terminal-style), native Windows controls, a monospace font matching
Windows Terminal/PowerShell, a dark title bar, and a "DX" app icon. The window sizes
itself to fit on any display scaling.

## Requirements

- Windows with Python 3 installed (the `py` launcher). `start.bat` handles the rest.

## Troubleshooting

- **No MIDI ports listed:** make sure the synth is on and connected, then click
  *Refresh*. Close any other program that might be using the MIDI port.
- **"MIDI library not available":** run `start.bat` (it installs the dependency).
- **Synth ignores the dump / load returns nothing:** the reface DX uses MIDI
  channel 1 by default, which matches DXcontrol. If you changed the synth's MIDI
  channel, set it back to 1.

## Notes

- Captured fixed-frequency operators are shown in Hz via `10^(coarse/8 + fine/100)`;
  this can differ from the bundled libraries' sheets by a thousandth of a Hz due to
  rounding, which is cosmetic only.
