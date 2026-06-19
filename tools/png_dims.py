"""Read PNG IHDR (dims + color type) from each texture in two .wlsave zips, cheaply."""
import struct
import sys
import zipfile
from collections import Counter

COLOR = {0: "Gray", 2: "RGB", 3: "Palette", 4: "GrayA", 6: "RGBA"}


def ihdr(zf, name):
    """(width, height, bitdepth, colortype) from the PNG IHDR — reads only the header."""
    with zf.open(name) as fh:
        head = fh.read(33)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = struct.unpack(">II", head[16:24])
    bitdepth = head[24]
    colortype = head[25]
    return w, h, bitdepth, colortype


def tex_index(path):
    zf = zipfile.ZipFile(path)
    out = {}
    for n in zf.namelist():
        if "/Textures/" in n and n.lower().endswith(".png"):
            try:
                out[n.split("/")[-1]] = ihdr(zf, n)
            except Exception:
                out[n.split("/")[-1]] = None
    return zf, out


def main():
    _, a = tex_index(sys.argv[1])
    _, b = tex_index(sys.argv[2])

    def dist(idx, label):
        dims = Counter()
        ctype = Counter()
        for v in idx.values():
            if v:
                dims[(v[0], v[1])] += 1
                ctype[COLOR.get(v[3], v[3])] += 1
        print(f"\n[{label}]  {len(idx)} png")
        print("  dimensions (top 12):")
        for (w, h), c in dims.most_common(12):
            print(f"    {w}x{h:<6} : {c}")
        print("  color type:")
        for k, c in ctype.most_common():
            print(f"    {k:8} : {c}")

    dist(a, "FILE1")
    dist(b, "FILE2")

    shared = set(a) & set(b)
    print(f"\n--- shared textures whose DIMENSIONS changed (top 25 by area growth) ---")
    rows = []
    for n in shared:
        va, vb = a[n], b[n]
        if va and vb and (va[0], va[1]) != (vb[0], vb[1]):
            rows.append((vb[0]*vb[1] - va[0]*va[1], n, va, vb))
    rows.sort(reverse=True)
    for _, n, va, vb in rows[:25]:
        print(f"    {va[0]}x{va[1]} {COLOR.get(va[3])}  ->  {vb[0]}x{vb[1]} {COLOR.get(vb[3])}   {n}")
    print(f"  ({len(rows)} shared textures changed dimensions)")


if __name__ == "__main__":
    main()
