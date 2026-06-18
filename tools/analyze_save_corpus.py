"""analyze_save_corpus.py — infer the .wlsave coordinate convention (C_pos) from real game saves.

No in-game authoring needed: read the corpus of saves the game itself wrote and let the statistics
reveal the convention. Two signals:

  * UP POSITION AXIS — entities rest on surfaces, so the vertical coordinate has the SMALLEST spread
    (clustered near floor/level heights) while the two horizontal coordinates span the whole map.
    Characters are the cleanest probe (they stand on the ground).
  * UP ROTATION CHANNEL — a standing character only turns about the vertical, so the rotation channel
    that is ACTIVE (non-zero, wide spread) while the other two stay ~0 is the rotation about up (= yaw).

Run:  python tools/analyze_save_corpus.py [SAVE_DIR]
Default SAVE_DIR = %LOCALAPPDATA%/WildLifeC/Saved/SandboxSaveGames
"""

import glob
import json
import math
import os
import sys


def _stats(vals):
    n = len(vals)
    if n == 0:
        return {"n": 0}
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    return {"n": n, "min": min(vals), "max": max(vals), "mean": mean,
            "std": math.sqrt(var), "range": max(vals) - min(vals)}


def _collect(files):
    pos = {"x": [], "y": [], "z": []}          # characters (clean up-signal)
    rot = {"pitch": [], "yaw": [], "roll": []}
    pos_props = {"x": [], "y": [], "z": []}     # props (placed objects)
    for f in files:
        try:
            s = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for c in (s.get("characters") or []):
            p, r = c.get("position") or {}, c.get("rotation") or {}
            for a in pos:
                if a in p:
                    pos[a].append(float(p[a]))
            for a in rot:
                if a in r:
                    rot[a].append(float(r[a]))
        for pr in (s.get("props") or []):
            p = pr.get("position") or {}
            for a in pos_props:
                if a in p:
                    pos_props[a].append(float(p[a]))
    return pos, rot, pos_props


def _active_fraction(vals, tol=1.0):
    if not vals:
        return 0.0
    return sum(1 for v in vals if abs(v) > tol) / len(vals)


def main(save_dir):
    files = glob.glob(os.path.join(save_dir, "**", "*.json"), recursive=True)
    pos, rot, pos_props = _collect(files)
    print(f"corpus: {len(files)} saves | characters sampled: {len(pos['x'])} | props sampled: {len(pos_props['x'])}\n")

    print("CHARACTER position spread (std) per axis — the UP axis has the SMALLEST spread:")
    char_std = {a: _stats(pos[a])["std"] for a in pos if pos[a]}
    for a in ("x", "y", "z"):
        st = _stats(pos[a])
        print(f"  {a}: std={st.get('std', 0):14.1f}  range={st.get('range', 0):14.1f}  mean={st.get('mean', 0):12.1f}")
    up_axis = min(char_std, key=char_std.get) if char_std else "?"
    print(f"  -> smallest-spread (UP) position axis: {up_axis}\n")

    print("PROP position spread (std) per axis (cross-check; props can be at any height):")
    for a in ("x", "y", "z"):
        st = _stats(pos_props[a])
        print(f"  {a}: std={st.get('std', 0):14.1f}  range={st.get('range', 0):14.1f}")
    print()

    print("ROTATION channel activity — the UP-rotation channel is ACTIVE while the others stay ~0:")
    for ch in ("pitch", "yaw", "roll"):
        st = _stats(rot[ch])
        print(f"  {ch}: active={_active_fraction(rot[ch]):5.1%}  std={st.get('std', 0):8.2f}  range={st.get('range', 0):8.1f}")
    up_rot = max(rot, key=lambda c: _active_fraction(rot[c])) if any(rot.values()) else "?"
    print(f"  -> most-active (UP) rotation channel: {up_rot}\n")

    print("CONCLUSION (seeds C_pos):")
    print(f"  save up axis  = position '{up_axis}'   (Blender up +Z should map here)")
    print(f"  up rotation   = '{up_rot}'  channel   (yaw-about-up)")


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else os.path.expandvars(r"%LOCALAPPDATA%\WildLifeC\Saved\SandboxSaveGames")
    main(d)
