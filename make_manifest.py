#!/usr/bin/env python3
"""Regenerate manifest.json, the index the web app (index.html) reads.

GitHub Pages can't list directories, so the web app needs a manifest listing
every library and its voices. Run this whenever you add or remove .syx files
(e.g. after dropping captured voices into a Reface-User/SYX folder):

    py make_manifest.py
"""

import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def build():
    libraries = {}
    for entry in sorted(os.listdir(SCRIPT_DIR)):
        lib_dir = os.path.join(SCRIPT_DIR, entry)
        syx_dir = os.path.join(lib_dir, "SYX")
        if not os.path.isdir(syx_dir):
            continue
        txt_dir = os.path.join(lib_dir, "TXT")
        voices = []
        for fname in sorted(os.listdir(syx_dir)):
            if not fname.lower().endswith(".syx"):
                continue
            stem = os.path.splitext(fname)[0]
            txt_rel = f"{entry}/TXT/{stem}.txt"
            has_txt = os.path.isfile(os.path.join(txt_dir, stem + ".txt"))
            voices.append({
                "name": stem,
                "syx": f"{entry}/SYX/{fname}",
                "txt": txt_rel if has_txt else None,
            })
        libraries[entry] = voices
    return {"libraries": libraries}


def main():
    manifest = build()
    path = os.path.join(SCRIPT_DIR, "manifest.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    total = sum(len(v) for v in manifest["libraries"].values())
    print(f"Wrote manifest.json: {len(manifest['libraries'])} libraries, "
          f"{total} voices.")


if __name__ == "__main__":
    main()
