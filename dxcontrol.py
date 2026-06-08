#!/usr/bin/env python3
"""DXcontrol - send patch libraries to a Yamaha reface DX over MIDI.

The .syx files in the library folders are already native reface DX SysEx bulk
dumps, so "sending" a patch means streaming the file's SysEx messages out to the
MIDI port the synth is connected to. No conversion is performed.
"""

import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter import font as tkfont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Delay between individual SysEx messages so the synth's receive buffer keeps up.
INTER_MESSAGE_DELAY = 0.02  # seconds

# Preferred monospace families, matching the Windows Terminal / PowerShell look.
MONO_FAMILIES = (
    "Cascadia Mono", "Cascadia Code", "Consolas", "Lucida Console", "Courier New",
)
FONT_SIZE = 11

# Dark colors for the text areas (Windows Terminal dark palette).
TEXT_BG = "#0c0c0c"
TEXT_FG = "#cccccc"
TEXT_SEL_BG = "#264f78"
TEXT_SEL_FG = "#ffffff"

ICON_PATH = os.path.join(SCRIPT_DIR, "dxcontrol.ico")


# --------------------------------------------------------------------------- #
# MIDI (imported lazily so the GUI can still show a helpful message if missing)
# --------------------------------------------------------------------------- #
try:
    import mido
    MIDI_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - depends on environment
    mido = None
    MIDI_IMPORT_ERROR = exc


def get_output_ports():
    if mido is None:
        return []
    try:
        return list(mido.get_output_names())
    except Exception:
        return []


def split_sysex(data):
    """Split a raw .syx byte blob into individual F0..F7 SysEx messages."""
    messages = []
    start = None
    for i, byte in enumerate(data):
        if byte == 0xF0:
            start = i
        elif byte == 0xF7 and start is not None:
            messages.append(data[start:i + 1])
            start = None
    return messages


# Audition note settings.
AUDITION_NOTE = 60       # middle C
AUDITION_VELOCITY = 100
AUDITION_DURATION = 1.0  # seconds
AUDITION_CHANNEL = 0     # MIDI channel 1 (reface DX default)
LOAD_SETTLE = 0.15       # let the synth load the voice before playing


def send_syx_file(port_name, path, progress=None, audition=False):
    """Send every SysEx message in `path` to `port_name`.

    `progress` is an optional callback(sent, total). When `audition` is true,
    a note is played for AUDITION_DURATION seconds after the dump so the loaded
    voice can be heard.
    """
    with open(path, "rb") as fh:
        data = fh.read()

    messages = split_sysex(data)
    if not messages:
        raise ValueError("No SysEx messages found in this file.")

    total = len(messages)
    with mido.open_output(port_name) as port:
        for index, raw in enumerate(messages, start=1):
            port.send(mido.Message.from_bytes(list(raw)))
            if progress:
                progress(index, total)
            if index < total:
                time.sleep(INTER_MESSAGE_DELAY)

        if audition:
            time.sleep(LOAD_SETTLE)
            port.send(mido.Message("note_on", note=AUDITION_NOTE,
                                   velocity=AUDITION_VELOCITY,
                                   channel=AUDITION_CHANNEL))
            time.sleep(AUDITION_DURATION)
            port.send(mido.Message("note_off", note=AUDITION_NOTE,
                                   velocity=0, channel=AUDITION_CHANNEL))
    return total


# --------------------------------------------------------------------------- #
# Receiving a voice from the synth (current / edit-buffer voice)
# --------------------------------------------------------------------------- #
# Bulk-dump request for the current voice (device 0x20 = channel 1, model 0x05).
# The address is the voice bulk *header* (0E 0F 00); the reface answers with the
# whole 7-message voice. (Address 00 00 00 is the System block, not the voice.)
DUMP_REQUEST = bytes([0xF0, 0x43, 0x20, 0x7F, 0x1C, 0x05, 0x0E, 0x0F, 0x00, 0xF7])
FOOTER_ADDRESS = (0x0F, 0x0F, 0x00)  # last of the 7 messages in a voice


def get_input_ports():
    if mido is None:
        return []
    try:
        return list(mido.get_input_names())
    except Exception:
        return []


def request_current_voice(in_name, out_name, timeout=3.0, idle=1.0):
    """Ask the reface DX for its current voice and return the raw .syx bytes.

    Sends DUMP_REQUEST on `out_name`, collects incoming SysEx messages on
    `in_name` until the footer block arrives (or it goes idle), then returns the
    concatenated bytes of all 7 messages.
    """
    collected = []
    with mido.open_input(in_name) as inp, mido.open_output(out_name) as out:
        for _ in inp.iter_pending():  # flush anything stale
            pass
        out.send(mido.Message.from_bytes(list(DUMP_REQUEST)))

        start = time.time()
        last = start
        while True:
            got = False
            for msg in inp.iter_pending():
                if msg.type == "sysex":
                    raw = msg.bytes()
                    collected.append(bytes(raw))
                    last = time.time()
                    got = True
                    if len(raw) >= 11 and tuple(raw[8:11]) == FOOTER_ADDRESS:
                        return b"".join(collected)
            now = time.time()
            if collected and now - last > idle:
                break
            if not collected and now - start > timeout:
                break
            if not got:
                time.sleep(0.01)

    if not collected:
        raise TimeoutError(
            "No data received from the synth. Check the MIDI In port and that "
            "the reface DX is connected and powered on.")
    return b"".join(collected)


# --------------------------------------------------------------------------- #
# Decoding a voice dump into a human-readable parameter sheet
# --------------------------------------------------------------------------- #
_PART_MODE = ("Poly", "Mono-Full", "Mono-Legato")
_LFO_WAVE = ("Sine", "Triangle", "Sawtooth Up", "Sawtooth Down", "Square",
             "Sample & Hold 8", "Sample & Hold")
_EFFECT_TYPE = ("Thru", "Distortion", "Touch Wah", "Chorus", "Flanger",
                "Phaser", "Delay", "Reverb")
_KSC_CURVE = ("-LIN", "-EXP", "+EXP", "+LIN")
_FB_TYPE = ("Sawtooth", "Square")
_FREQ_MODE = ("Ratio", "Fixed")
# Effect parameter labels per effect type. Only Thru and Chorus occur in the
# bundled libraries; the rest are best-effort and never validated.
_EFFECT_PARAMS = {
    0: ("---", "---"), 1: ("Drive", "Tone"), 2: ("Freq", "Depth"),
    3: ("Depth", "Rate"), 4: ("Depth", "Rate"), 5: ("Depth", "Rate"),
    6: ("Time", "Feedback"), 7: ("Time", "Depth"),
}

OP_HEADER = (" " * 16 + "OP1".rjust(9) + "OP2".rjust(9)
             + "OP3".rjust(9) + "OP4".rjust(9) + " " * 5)


def _enum(table, index):
    return table[index] if 0 <= index < len(table) else str(index)


def voice_payloads(syx_bytes):
    """Return the data payloads of the 7 messages of a reface DX voice dump.

    Each message is `F0 43 0n 7F 1C bh bl model addr3 <data...> cs F7`; the data
    payload is `msg[11 : 11 + (bytecount - 4)]`.
    """
    msgs = split_sysex(syx_bytes)
    if len(msgs) != 7:
        raise ValueError(f"Expected 7 SysEx messages in a voice, got {len(msgs)}.")
    payloads = []
    for m in msgs:
        count = (m[5] << 7) | m[6]
        payloads.append(m[11:11 + count - 4])
    return payloads  # [header, common, op1, op2, op3, op4, footer]


def voice_name(syx_bytes):
    common = voice_payloads(syx_bytes)[1]
    name = bytes(common[0:10]).decode("latin-1")
    return "".join(c if 32 <= ord(c) < 127 else " " for c in name).rstrip()


def decode_voice(syx_bytes):
    """Decode a voice dump into a dict of common + per-operator parameters."""
    payloads = voice_payloads(syx_bytes)
    common, ops = payloads[1], payloads[2:6]

    c = {
        "name": voice_name(syx_bytes),
        "transpose": common[0x0c] - 64,
        "part_mode": _enum(_PART_MODE, common[0x0d]),
        "porta": common[0x0e],
        "pb_range": common[0x0f] - 64,
        "algorithm": common[0x10] + 1,
        "lfo_wave": _enum(_LFO_WAVE, common[0x11]),
        "lfo_speed": common[0x12],
        "lfo_delay": common[0x13],
        "lfo_pmd": common[0x14],
        "peg_rate": [common[0x15 + i] for i in range(4)],
        "peg_level": [common[0x19 + i] - 64 for i in range(4)],
        "fx1_type": common[0x1d], "fx1_p1": common[0x1e], "fx1_p2": common[0x1f],
        "fx2_type": common[0x20], "fx2_p1": common[0x21], "fx2_p2": common[0x22],
    }

    oplist = []
    for op in ops:
        oplist.append({
            "enable": op[0x00],
            "eg_rate": [op[0x01 + i] for i in range(4)],
            "eg_level": [op[0x05 + i] for i in range(4)],
            "ksc_rate": op[0x09], "ksc_ld": op[0x0a], "ksc_rd": op[0x0b],
            "ksc_lc": op[0x0c], "ksc_rc": op[0x0d],
            "lfo_amd": op[0x0e], "lfo_pm": op[0x0f], "peg_pm": op[0x10],
            "velo": op[0x11], "level": op[0x12], "feedback": op[0x13],
            "fb_type": op[0x14], "freq_mode": op[0x15],
            "coarse": op[0x16], "fine": op[0x17], "detune": op[0x18] - 64,
        })
    return {"common": c, "ops": oplist}


def _ratio_str(coarse, fine):
    if coarse == 0:
        s = f"{0.5 + fine / 200.0:.3f}".rstrip("0")
    else:
        s = f"{coarse + fine / 100.0:.2f}".rstrip("0")
    return s + "0" if s.endswith(".") else s


def _fixed_freq_str(coarse, fine):
    val = 10.0 ** (coarse / 8.0 + fine / 100.0)
    return f"{val:.3f}".rstrip("0").rstrip(".")


def _freq_str(op):
    if op["freq_mode"] == 1:  # Fixed -> frequency in Hz
        return _fixed_freq_str(op["coarse"], op["fine"])
    return _ratio_str(op["coarse"], op["fine"])


def format_voice_sheet(voice):
    """Render a decoded voice as a parameter sheet matching the bundled TXT files."""
    c, ops = voice["common"], voice["ops"]
    out = []

    def cl(label, value, annot=None):
        s = f"{label:<15}= {str(value).rjust(8)}"
        return s + f" ({annot})" if annot is not None else s

    def ol(label, values):
        return f"{label:<15}=" + "".join(str(v).rjust(9) for v in values)

    out.append(f"{'VOICE NAME':<15}= {c['name']}")
    out.append("=" * 28)
    out.append(cl("TRANSPOSE", c["transpose"]))
    out.append(cl("MONO/POLY", c["part_mode"]))
    out.append(cl("PORTA TIME", c["porta"]))
    out.append(cl("PB RANGE", c["pb_range"]))
    out.append(cl("ALGORITHM", c["algorithm"]))
    out.append(cl("LFO WAVE", c["lfo_wave"]))
    out.append(cl("LFO SPEED", c["lfo_speed"]))
    out.append(cl("LFO DELAY", c["lfo_delay"]))
    out.append(cl("LFO PMD", c["lfo_pmd"]))
    for i in range(4):
        out.append(cl(f"PEG RATE {i + 1}", c["peg_rate"][i]))
    for i in range(4):
        out.append(cl(f"PEG LEVEL {i + 1}", c["peg_level"][i]))
    for fx, t, p1, p2 in (("FX1", c["fx1_type"], c["fx1_p1"], c["fx1_p2"]),
                          ("FX2", c["fx2_type"], c["fx2_p1"], c["fx2_p2"])):
        pn = _EFFECT_PARAMS.get(t, ("P1", "P2"))
        out.append(cl(f"{fx} TYPE", t, _enum(_EFFECT_TYPE, t)))
        out.append(cl(f"{fx} PARAM 1", p1, pn[0]))
        out.append(cl(f"{fx} PARAM 2", p2, pn[1]))

    out.append("")
    out.append(OP_HEADER)
    out.append("-" * 52)
    out.append(ol("OP Off/On", ["On" if o["enable"] else "Off" for o in ops]))
    for i in range(4):
        out.append(ol(f"EG RATE {i + 1}", [o["eg_rate"][i] for o in ops]))
    for i in range(4):
        out.append(ol(f"EG LEVEL {i + 1}", [o["eg_level"][i] for o in ops]))
    out.append(ol("RATE SCALING", [o["ksc_rate"] for o in ops]))
    out.append(ol("SCALING LD", [o["ksc_ld"] for o in ops]))
    out.append(ol("SCALING RD", [o["ksc_rd"] for o in ops]))
    out.append(ol("SCALING LC", [_enum(_KSC_CURVE, o["ksc_lc"]) for o in ops]))
    out.append(ol("SCALING RC", [_enum(_KSC_CURVE, o["ksc_rc"]) for o in ops]))
    out.append(ol("LFO AMD", [o["lfo_amd"] for o in ops]))
    out.append(ol("LFO PMD Off/On", ["On" if o["lfo_pm"] else "Off" for o in ops]))
    out.append(ol("PEG Off/On", ["On" if o["peg_pm"] else "Off" for o in ops]))
    out.append(ol("VELO SENS", [o["velo"] for o in ops]))
    out.append(ol("OUT LEVEL", [o["level"] for o in ops]))
    out.append(ol("FEEDBACK", [o["feedback"] for o in ops]))
    out.append(ol("FB TYPE", [_enum(_FB_TYPE, o["fb_type"]) for o in ops]))
    out.append(ol("FREQ MODE", [_enum(_FREQ_MODE, o["freq_mode"]) for o in ops]))
    out.append(ol("RATIO | FREQ", [_freq_str(o) for o in ops]))
    out.append(ol("   freq coarse", [o["coarse"] for o in ops]))
    out.append(ol("   freq fine", [o["fine"] for o in ops]))
    out.append(ol("FREQ DETUNE", [o["detune"] for o in ops]))
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# Library / patch discovery
# --------------------------------------------------------------------------- #
class Patch:
    def __init__(self, name, syx_path, txt_path=None):
        self.name = name
        self.syx_path = syx_path
        self.txt_path = txt_path

    def details(self):
        if self.txt_path and os.path.isfile(self.txt_path):
            try:
                with open(self.txt_path, "r", encoding="utf-8", errors="replace") as fh:
                    return fh.read()
            except Exception as exc:
                return f"(could not read description: {exc})"
        return "(no description file available for this patch)"


def discover_libraries(base_dir):
    """Find library folders (those containing a SYX subfolder).

    Returns a dict of {library_name: {'voices': [Patch, ...]}}.
    """
    libraries = {}
    for entry in sorted(os.listdir(base_dir)):
        lib_dir = os.path.join(base_dir, entry)
        syx_dir = os.path.join(lib_dir, "SYX")
        if not os.path.isdir(syx_dir):
            continue

        txt_dir = os.path.join(lib_dir, "TXT")

        voices = []
        for fname in sorted(os.listdir(syx_dir)):
            if not fname.lower().endswith(".syx"):
                continue
            stem = os.path.splitext(fname)[0]
            txt_path = os.path.join(txt_dir, stem + ".txt")
            txt_path = txt_path if os.path.isfile(txt_path) else None
            voices.append(Patch(stem, os.path.join(syx_dir, fname), txt_path))

        libraries[entry] = {"voices": voices}

    return libraries


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self, libraries):
        super().__init__()
        self.title("DXcontrol - reface DX patch sender")
        # Final size is set in _fit_to_content() once widgets are laid out, so it
        # is correct on any display scaling.

        self.libraries = libraries
        self.sending = False

        # Match Tk's point sizing to the display DPI so fonts render sharply.
        try:
            self.tk.call("tk", "scaling", self.winfo_fpixels("1i") / 72.0)
        except Exception:
            pass

        if os.path.isfile(ICON_PATH):
            try:
                self.iconbitmap(default=ICON_PATH)
            except Exception:
                pass

        # A monospace font matching the Windows Terminal / PowerShell look,
        # used everywhere (widgets + combobox dropdown list).
        self.ui_font = (self._pick_mono(), FONT_SIZE)
        self.option_add("*Font", self.ui_font)
        self.option_add("*TCombobox*Listbox.font", self.ui_font)
        # Keep the native Windows ttk theme so checkboxes, the dropdown button and
        # scrollbars are standard size and DPI-correct (only set the font on them).
        self.style = ttk.Style(self)
        self.style.configure(".", font=self.ui_font)
        self.style.configure("TCombobox", font=self.ui_font)

        self.audition_var = tk.BooleanVar(value=True)
        self.loading = False
        self._build_widgets()
        self._apply_theme()
        self._refresh_ports(initial=True)
        self._populate_list()
        self._set_dark_titlebar()
        self._fit_to_content()

    @staticmethod
    def _pick_mono():
        available = set(tkfont.families())
        for name in MONO_FAMILIES:
            if name in available:
                return name
        return "Courier New"

    # -- layout ------------------------------------------------------------- #
    def _build_widgets(self):
        top = ttk.Frame(self, padding=10)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Library:").grid(row=0, column=0, sticky="w")
        self.library_var = tk.StringVar()
        lib_names = list(self.libraries.keys())
        self.library_combo = ttk.Combobox(
            top, textvariable=self.library_var, values=lib_names,
            state="readonly", width=14,
        )
        if lib_names:
            self.library_combo.current(0)
        self.library_combo.grid(row=0, column=1, padx=(4, 16))
        self.library_combo.bind("<<ComboboxSelected>>", lambda e: self._populate_list())

        ttk.Label(top, text="MIDI Out:").grid(row=0, column=2, sticky="w")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(
            top, textvariable=self.port_var, state="readonly", width=22,
        )
        self.port_combo.grid(row=0, column=3, padx=(4, 8))
        ttk.Button(top, text="Refresh", command=self._refresh_ports).grid(
            row=0, column=4, padx=(0, 16))

        ttk.Checkbutton(
            top, text="Audition (play 1s)", variable=self.audition_var,
        ).grid(row=0, column=5, sticky="w")

        ttk.Label(top, text="MIDI In:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.in_port_var = tk.StringVar()
        self.in_port_combo = ttk.Combobox(
            top, textvariable=self.in_port_var, state="readonly", width=22,
        )
        self.in_port_combo.grid(row=1, column=1, columnspan=3, sticky="we",
                                padx=(4, 8), pady=(6, 0))
        self.load_btn = ttk.Button(
            top, text="Load current voice from synth",
            command=self._load_from_synth)
        self.load_btn.grid(row=1, column=4, columnspan=2, sticky="w", pady=(6, 0))

        # Main split: list on left, details on right
        main = ttk.Frame(self, padding=10)
        main.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        left = ttk.Frame(main)
        left.pack(side=tk.LEFT, fill=tk.Y, expand=False)
        self.listbox = tk.Listbox(left, width=18, activestyle="dotbox",
                                  exportselection=False)
        self.listbox.pack(side=tk.LEFT, fill=tk.Y, expand=True)
        lb_scroll = ttk.Scrollbar(left, orient=tk.VERTICAL,
                                  command=self.listbox.yview)
        lb_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.config(yscrollcommand=lb_scroll.set)
        # Selecting a voice sends it immediately.
        self.listbox.bind("<<ListboxSelect>>", lambda e: self._on_select())

        right = ttk.Frame(main)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0))
        self.detail = tk.Text(right, wrap="none", state="disabled",
                              width=56, height=28, font=self.ui_font)
        self.detail.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        d_scroll = ttk.Scrollbar(right, orient=tk.VERTICAL,
                                 command=self.detail.yview)
        d_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.detail.config(yscrollcommand=d_scroll.set)

        # Bottom: status line
        bottom = ttk.Frame(self, padding=10)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="Select a voice to send it.")
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT)

        if MIDI_IMPORT_ERROR is not None:
            self.status_var.set(
                "MIDI library not available - run start.bat to install it.")

    # -- data --------------------------------------------------------------- #
    def _current_items(self):
        lib = self.library_var.get()
        if lib not in self.libraries:
            return []
        return self.libraries[lib]["voices"]

    def _populate_list(self):
        # Don't auto-select; a selection triggers a send.
        self.listbox.delete(0, tk.END)
        for patch in self._current_items():
            self.listbox.insert(tk.END, patch.name)
        self._show_details()

    def _selected_patch(self):
        items = self._current_items()
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(items):
            return None
        return items[sel[0]]

    def _show_details(self):
        patch = self._selected_patch()
        self.detail.config(state="normal")
        self.detail.delete("1.0", tk.END)
        text = patch.details() if patch is not None else "(no voice selected)"
        self.detail.insert("1.0", text)
        self.detail.config(state="disabled")

    # -- theme -------------------------------------------------------------- #
    def _apply_theme(self):
        """Color the two text areas like a dark terminal. The surrounding controls
        keep the native Windows theme (standard size, light chrome)."""
        self.listbox.configure(
            bg=TEXT_BG, fg=TEXT_FG, selectbackground=TEXT_SEL_BG,
            selectforeground=TEXT_SEL_FG, highlightthickness=0, borderwidth=0)
        self.detail.configure(
            bg=TEXT_BG, fg=TEXT_FG, insertbackground=TEXT_FG,
            selectbackground=TEXT_SEL_BG, selectforeground=TEXT_SEL_FG,
            highlightthickness=0, borderwidth=0)

    def _set_dark_titlebar(self):
        """Ask Windows for a dark title bar (Win 10/11) via the DWM API."""
        try:
            from ctypes import windll, byref, sizeof, c_int
            self.update_idletasks()
            hwnd = windll.user32.GetParent(self.winfo_id())
            value = c_int(1)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new, old)
                windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, byref(value), sizeof(value))
        except Exception:
            pass

    def _fit_to_content(self):
        """Size the window to exactly fit its widgets, and keep that as the
        minimum so the audition control and scrollbars are never clipped.
        """
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        self.minsize(w, h)
        self.geometry(f"{w}x{h}")

    # -- ports -------------------------------------------------------------- #
    @staticmethod
    def _prefer_reface(ports):
        return next((p for p in ports if "reface" in p.lower()), ports[0])

    def _refresh_ports(self, initial=False):
        ports = get_output_ports()
        self.port_combo["values"] = ports
        self.port_var.set(self._prefer_reface(ports) if ports else "")

        in_ports = get_input_ports()
        self.in_port_combo["values"] = in_ports
        self.in_port_var.set(self._prefer_reface(in_ports) if in_ports else "")

        if not ports and not initial:
            self.status_var.set("No MIDI output ports found.")

    # -- sending ------------------------------------------------------------ #
    def _on_select(self):
        """Show the selected voice's details and send it immediately."""
        self._show_details()
        if self.sending:
            return

        patch = self._selected_patch()
        if patch is None:
            return

        if mido is None:
            messagebox.showerror(
                "DXcontrol",
                "The MIDI library isn't installed.\n\n"
                "Close this window and run start.bat, which installs it "
                "automatically.")
            return

        port_name = self.port_var.get()
        if not port_name:
            messagebox.showwarning(
                "DXcontrol",
                "No MIDI output port selected.\n\n"
                "Connect your reface DX, then click Refresh and pick its port.")
            return

        self.sending = True
        self.status_var.set(f"Sending '{patch.name}'...")

        thread = threading.Thread(
            target=self._send_worker, args=(port_name, patch), daemon=True)
        thread.start()

    def _send_worker(self, port_name, patch):
        def progress(sent, total):
            self.after(0, self.status_var.set,
                       f"Sending '{patch.name}'... message {sent}/{total}")
        try:
            count = send_syx_file(port_name, patch.syx_path, progress,
                                  audition=self.audition_var.get())
        except Exception as exc:
            self.after(0, self._send_done, patch, None, str(exc))
        else:
            self.after(0, self._send_done, patch, count, None)

    def _send_done(self, patch, count, error):
        self.sending = False
        if error is not None:
            self.status_var.set("Send failed.")
            messagebox.showerror(
                "DXcontrol", f"Could not send '{patch.name}':\n\n{error}")
            return
        self.status_var.set(
            f"Sent '{patch.name}'. Loaded into the edit buffer - "
            "press Store on the synth to keep it.")

    # -- loading from synth ------------------------------------------------- #
    def _load_from_synth(self):
        if self.sending or self.loading:
            return
        if mido is None:
            messagebox.showerror(
                "DXcontrol",
                "The MIDI library isn't installed.\n\n"
                "Close this window and run start.bat, which installs it "
                "automatically.")
            return
        in_name, out_name = self.in_port_var.get(), self.port_var.get()
        if not in_name or not out_name:
            messagebox.showwarning(
                "DXcontrol",
                "Both a MIDI In and a MIDI Out port are needed to load from the "
                "synth.\n\nConnect the reface DX, click Refresh, and pick its "
                "ports.")
            return

        self.loading = True
        self.load_btn.config(state="disabled")
        self.status_var.set("Requesting current voice from synth...")
        threading.Thread(target=self._load_worker, args=(in_name, out_name),
                         daemon=True).start()

    def _load_worker(self, in_name, out_name):
        try:
            syx = request_current_voice(in_name, out_name)
            sheet = format_voice_sheet(decode_voice(syx))
            name = voice_name(syx) or "Voice"
            paths = save_captured_voice(name, syx, sheet)
        except Exception as exc:
            self.after(0, self._load_done, None, str(exc))
        else:
            self.after(0, self._load_done, (name, paths), None)

    def _load_done(self, result, error):
        self.loading = False
        self.load_btn.config(state="normal")
        if error is not None:
            self.status_var.set("Load failed.")
            messagebox.showerror(
                "DXcontrol", f"Could not load from the synth:\n\n{error}")
            return
        name, (syx_path, _txt) = result
        # Re-scan libraries so the new Reface-User voice appears, then select it.
        self.libraries = discover_libraries(SCRIPT_DIR)
        self.library_combo["values"] = list(self.libraries.keys())
        self.library_var.set(CAPTURE_LIBRARY)
        self._populate_list()
        for i, patch in enumerate(self._current_items()):
            if os.path.normcase(patch.syx_path) == os.path.normcase(syx_path):
                self.listbox.selection_clear(0, tk.END)
                self.listbox.selection_set(i)
                self.listbox.see(i)
                break
        self._show_details()
        self.status_var.set(
            f"Loaded '{name}' from synth -> saved in {CAPTURE_LIBRARY}.")


CAPTURE_LIBRARY = "Reface-User"


def name_to_filename(name):
    safe = "".join(ch if ch.isalnum() or ch in " _-" else "_" for ch in name)
    return safe.strip().replace(" ", "_") or "Voice"


def save_captured_voice(name, syx_bytes, sheet_text):
    """Save a captured voice as .syx + .txt under the Reface-User library."""
    syx_dir = os.path.join(SCRIPT_DIR, CAPTURE_LIBRARY, "SYX")
    txt_dir = os.path.join(SCRIPT_DIR, CAPTURE_LIBRARY, "TXT")
    os.makedirs(syx_dir, exist_ok=True)
    os.makedirs(txt_dir, exist_ok=True)

    base = name_to_filename(name)
    stem = base
    n = 2
    while os.path.exists(os.path.join(syx_dir, stem + ".syx")):
        stem = f"{base}_{n}"
        n += 1

    syx_path = os.path.join(syx_dir, stem + ".syx")
    txt_path = os.path.join(txt_dir, stem + ".txt")
    with open(syx_path, "wb") as fh:
        fh.write(syx_bytes)
    with open(txt_path, "w", encoding="utf-8") as fh:
        fh.write(sheet_text)
    return syx_path, txt_path


def enable_dpi_awareness():
    """Tell Windows we render at native resolution so text stays sharp
    (otherwise the window is bitmap-stretched on scaled displays, blurring text).
    """
    try:
        import ctypes
        try:
            # Per-monitor-v2 (Win 8.1+); crisp on the active monitor's DPI.
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def main():
    enable_dpi_awareness()
    libraries = discover_libraries(SCRIPT_DIR)
    if not libraries:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "DXcontrol",
            "No patch libraries found next to this program.\n\n"
            "Expected folders like Reface-DX21 with a SYX subfolder.")
        return
    app = App(libraries)
    app.mainloop()


if __name__ == "__main__":
    main()
