"""Ad-hoc .wlsave size analyzer — compare bundle contents (textures / models / json)."""
import json
import os
import sys
import zipfile
from collections import defaultdict


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
        n /= 1024


def analyze(path):
    z = zipfile.ZipFile(path)
    infos = z.infolist()
    total_uncomp = sum(i.file_size for i in infos)
    total_comp = sum(i.compress_size for i in infos)

    buckets = defaultdict(lambda: {"count": 0, "size": 0, "comp": 0})
    ext_buckets = defaultdict(lambda: {"count": 0, "size": 0})
    tex_files = {}      # basename -> size
    biggest = []
    json_entry = None

    for i in infos:
        name = i.filename
        parts = name.split("/")
        if "/Textures/" in name:
            bucket = "Textures"
            base = parts[-1]
            ext = os.path.splitext(base)[1].lower()
            ext_buckets[ext]["count"] += 1
            ext_buckets[ext]["size"] += i.file_size
            tex_files[base] = i.file_size
        elif "/Models/" in name:
            bucket = "Models"
        elif name.endswith(".json"):
            bucket = "JSON"
            json_entry = i
        else:
            bucket = "Other"
        buckets[bucket]["count"] += 1
        buckets[bucket]["size"] += i.file_size
        buckets[bucket]["comp"] += i.compress_size
        biggest.append((i.file_size, name))

    # Parse the save JSON for material/prop counts.
    mats = props = None
    mat_with_tex = defaultdict(int)
    if json_entry is not None:
        try:
            data = json.loads(z.read(json_entry.filename))
            cm = data.get("customMaterials") or []
            mats = len(cm)
            props = len(data.get("props") or [])
            for m in cm:
                for field in ("diffuseTexturePath", "normalTexturePath", "metallicTexturePath",
                              "roughnessTexturePath", "emissiveTexturePath", "heightTexturePath"):
                    if m.get(field):
                        mat_with_tex[field] += 1
        except Exception as e:
            print("  (json parse failed:", e, ")")

    print("=" * 70)
    print(f"{os.path.basename(path)}  —  archive {human(os.path.getsize(path))}")
    print(f"  uncompressed total: {human(total_uncomp)}   (compressed in zip: {human(total_comp)})")
    print(f"  entries: {len(infos)}   materials: {mats}   props: {props}")
    print("  --- by bucket (uncompressed / compressed) ---")
    for b in ("Textures", "Models", "JSON", "Other"):
        if buckets[b]["count"]:
            d = buckets[b]
            print(f"    {b:9} {d['count']:6} files   {human(d['size']):>10} / {human(d['comp']):>10}")
    print("  --- textures by extension ---")
    for ext, d in sorted(ext_buckets.items(), key=lambda kv: -kv[1]["size"]):
        print(f"    {ext or '(none)':8} {d['count']:6} files   {human(d['size']):>10}")
    print("  --- materials referencing a texture (by channel) ---")
    for field, n in sorted(mat_with_tex.items(), key=lambda kv: -kv[1]):
        print(f"    {field:22} {n}")
    print("  --- 15 biggest entries ---")
    for size, name in sorted(biggest, reverse=True)[:15]:
        print(f"    {human(size):>10}  {name}")
    return {
        "tex_files": tex_files,
        "buckets": dict(buckets),
        "ext": dict(ext_buckets),
        "mats": mats, "props": props,
        "archive": os.path.getsize(path),
        "uncomp": total_uncomp,
    }


def main():
    a = analyze(sys.argv[1])
    b = analyze(sys.argv[2])
    print("=" * 70)
    print("COMPARISON  (file1 -> file2)")
    print(f"  archive:    {human(a['archive'])} -> {human(b['archive'])}   (x{b['archive']/a['archive']:.2f})")
    print(f"  materials:  {a['mats']} -> {b['mats']}")
    print(f"  props:      {a['props']} -> {b['props']}")
    ta, tb = set(a["tex_files"]), set(b["tex_files"])
    print(f"  textures:   {len(ta)} -> {len(tb)}")
    print(f"    only in file1: {len(ta - tb)}")
    print(f"    only in file2: {len(tb - ta)}")
    print(f"    in both:       {len(ta & tb)}")
    # size delta on shared textures (format/resolution change)
    shared = ta & tb
    grew = [(b["tex_files"][n] - a["tex_files"][n], n) for n in shared
            if b["tex_files"][n] != a["tex_files"][n]]
    grew.sort(reverse=True)
    if grew:
        print(f"  --- shared textures that changed size (top 15 grew) ---")
        for delta, n in grew[:15]:
            print(f"    {human(a['tex_files'][n]):>10} -> {human(b['tex_files'][n]):>10}  ({'+' if delta>=0 else ''}{human(abs(delta))})  {n}")
    # biggest textures unique to file2
    only2 = [(b["tex_files"][n], n) for n in (tb - ta)]
    only2.sort(reverse=True)
    if only2:
        print(f"  --- biggest textures ONLY in file2 (top 15) ---")
        for size, n in only2[:15]:
            print(f"    {human(size):>10}  {n}")


if __name__ == "__main__":
    main()
