"""Quantify size levers in a .wlsave: bytes by dimension, by color type, by channel role."""
import json
import struct
import sys
import zipfile
from collections import defaultdict

COLOR = {0: "Gray", 2: "RGB", 3: "Palette", 4: "GrayA", 6: "RGBA"}
COLOR_CHANNELS = {"diffuseTexturePath", "emissiveTexturePath"}
DATA_CHANNELS = {"normalTexturePath", "roughnessTexturePath", "metallicTexturePath", "heightTexturePath"}


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.1f}{u}"
        n /= 1024


def ihdr(zf, name):
    with zf.open(name) as fh:
        head = fh.read(33)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = struct.unpack(">II", head[16:24])
    return w, h, head[24], head[25]


def main():
    path = sys.argv[1]
    zf = zipfile.ZipFile(path)

    size_by_base = {}
    info_by_base = {}
    for i in zf.infolist():
        if "/Textures/" in i.filename:
            b = i.filename.split("/")[-1]
            size_by_base[b] = i.file_size
            if b.lower().endswith(".png"):
                try:
                    info_by_base[b] = ihdr(zf, i.filename)
                except Exception:
                    info_by_base[b] = None

    # role of each bundled texture basename, from the JSON channel references
    data = None
    for n in zf.namelist():
        if n.endswith(".json"):
            data = json.loads(zf.read(n))
            break
    role = {}   # basename -> 'color' | 'data'
    if data:
        for m in data.get("customMaterials") or []:
            for field in COLOR_CHANNELS | DATA_CHANNELS:
                p = m.get(field)
                if p:
                    base = p.split("/")[-1]
                    r = "color" if field in COLOR_CHANNELS else "data"
                    # a texture used as both -> treat as data (safer / keep PNG)
                    role[base] = "data" if role.get(base) == "data" or r == "data" else "color"

    total = sum(size_by_base.values())
    print(f"{path}")
    print(f"  textures total: {human(total)}  ({len(size_by_base)} files)")

    by_dim = defaultdict(lambda: [0, 0])
    for b, info in info_by_base.items():
        if info:
            k = f"{info[0]}x{info[1]}"
            by_dim[k][0] += 1
            by_dim[k][1] += size_by_base[b]
    print("\n  bytes by dimension (top 10):")
    for k, (c, s) in sorted(by_dim.items(), key=lambda kv: -kv[1][1])[:10]:
        print(f"    {k:12} {c:5} files   {human(s):>10}   ({100*s/total:.0f}% of tex)")

    by_color = defaultdict(lambda: [0, 0])
    for b, info in info_by_base.items():
        if info:
            k = COLOR.get(info[3], str(info[3]))
            by_color[k][0] += 1
            by_color[k][1] += size_by_base[b]
    print("\n  bytes by PNG color type:")
    for k, (c, s) in sorted(by_color.items(), key=lambda kv: -kv[1][1]):
        print(f"    {k:8} {c:5} files   {human(s):>10}")

    by_role = defaultdict(lambda: [0, 0])
    for b, s in size_by_base.items():
        r = role.get(b, "unreferenced")
        by_role[r][0] += 1
        by_role[r][1] += s
    print("\n  bytes by channel role (from JSON refs):")
    for k, (c, s) in sorted(by_role.items(), key=lambda kv: -kv[1][1]):
        print(f"    {k:13} {c:5} files   {human(s):>10}   ({100*s/total:.0f}%)")

    # crude savings estimates
    color_bytes = by_role["color"][1]
    big = sum(size_by_base[b] for b, info in info_by_base.items()
              if info and max(info[0], info[1]) > 1024)
    big_q = sum(size_by_base[b]*0.25 for b, info in info_by_base.items()
                if info and max(info[0], info[1]) > 1024)  # downscale >1024 -> 1024 (area/4)
    print("\n  --- rough savings levers ---")
    print(f"    JPG q90 for COLOR textures (~12% of PNG):  -{human(color_bytes*0.88)}  (color now {human(color_bytes)})")
    print(f"    cap resolution at 1024 (>1024 -> /4 area):  -{human(big - big_q)}  ({len([1 for b,info in info_by_base.items() if info and max(info[0],info[1])>1024])} files >1024)")


if __name__ == "__main__":
    main()
