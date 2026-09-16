#!/usr/bin/env python3
"""Condense an inspect_drawing JSON into a compact, ledger-friendly digest (stdout)."""
import json
import sys
from collections import Counter


def short(s, n=70):
    s = (s or "").replace("\r\n", " / ").replace("\n", " / ")
    return s if len(s) <= n else s[: n - 1] + "…"


BRIEF = False


def main(path):
    d = json.load(open(path, encoding="utf-8"))
    print(f"== {d.get('title')}  [{d.get('path','').split(chr(92))[-1]}]")
    cp = d.get("drawing_custom_props", {})
    keys = ("TITLE", "REVISION", "ENG OF RECORD", "APPROVED BY", "APPROVED DATE", "MATERIAL", "THICKNESS",
            "BEND_RADIUS", "FINISH", "TYPE", "MODULE", "COMPONENT")
    print("props:", "; ".join(f"{k}={cp[k]}" for k in keys if cp.get(k) not in (None, "", "-")))
    for m, mi in (d.get("models") or {}).items():
        print(f"model {m}: type={mi.get('type')} features={mi.get('feature_type_counts')}")
        if mi.get("custom_props"):
            print("   model props:", "; ".join(f"{k}={v}" for k, v in mi["custom_props"].items()))
    for sh in d.get("sheets", []):
        print(f"-- sheet {sh.get('name')} paper={sh.get('paper_size_code')} size={sh.get('size_mm')} "
              f"scale={sh.get('sheet_scale')} template={sh.get('template')} first_angle={sh.get('first_angle')}")
        # notes: skip title-block boilerplate
        boiler = ("NOTES:", "FABRICATION DRAWING", "SHEET ", "SIZE:", "SCALE:", "THIRD ANGLE", "DWG NO.",
                  " ALL SHEETS", "REV", "COPYRIGHT", "TITLE", "UNLESS OTHERWISE", "TOLERANCES:", "COMPONENT",
                  "VARIANT", "MODULE", "ENGINEER OF RECORD", "APPROVED BY", "WEIGHT:", "LEGACY P/N",
                  "HIGH END OF THICKNESS", "135 SQ FT", "Paint required", " LBS", "TOTAL WEIGHT",
                  "DO NOT SCALE", "DWG.  NO.", "SIZE", "NAME", "DATE", "COMMENTS:", "Q.A.", "MFG APPR.", "ENG APPR.",
                  "CHECKED", "DRAWN", "FINISH", "MATERIAL", "INTERPRET GEOMETRIC", "DIMENSIONS ARE IN INCHES",
                  "PROPRIETARY", "THE INFORMATION CONTAINED", "CAD MODEL REVISION", "S. VALBUENA")
        for n in sh.get("notes", []):
            t = n.get("text", "")
            if any(t.startswith(b) for b in boiler):
                continue
            if n.get("linked_text", "").startswith("$PRP") and len(t) < 30:
                continue
            flags = []
            if n.get("leader"):
                flags.append("leader")
            if n.get("balloon"):
                flags.append(f"balloon{n.get('balloon_style','')}")
            if n.get("attached_types"):
                flags.append("→" + ",".join(n["attached_types"]))
            print(f"   note [{','.join(flags)}] h={n.get('font_height_mm')} @{n.get('pos_mm')}: {short(t, 160)}")
        for t in sh.get("tables", []):
            cells = t.get("cells") or []
            nonempty = [r for r in cells if any(c.strip() for c in r)]
            if len(nonempty) <= 1 and t.get("type") == "general":
                continue
            print(f"   table {t.get('type')} {t.get('rows')}x{t.get('cols')} '{t.get('title')}':")
            for r in nonempty[:12]:
                print("      | " + " | ".join(short(c, 40) for c in r))
        for v in sh.get("views", []):
            dims = v.get("dimensions", [])
            dtxt = []
            for dm in dims:
                s = f"{dm.get('value')}"
                if dm.get("display_type") not in (None, "linear"):
                    s += f"[{dm.get('display_type')}]"
                if dm.get("parenthesis") or dm.get("reference"):
                    s = f"({s})"
                if dm.get("prefix") or dm.get("suffix"):
                    s += f"{{{dm.get('prefix','')}|{dm.get('suffix','')}}}"
                if dm.get("callout_above") or dm.get("callout_below"):
                    s += f"«{dm.get('callout_above','')}/{dm.get('callout_below','')}»"
                at = dm.get("attached_types")
                if at:
                    s += "→" + "+".join(a[:4] for a in at)
                dtxt.append(s)
            flags = []
            if v.get("flat_pattern"):
                flags.append(f"FLAT(bends={v.get('bend_lines')})")
            if v.get("break_lines"):
                flags.append(f"BROKEN({v['break_lines']})")
            if v.get("is_section"):
                flags.append("SECTION")
            if v.get("is_detail"):
                flags.append("DETAIL")
            tan = v.get("tangent_edges")
            if d.get("tangent_map") != "calibrated":  # legacy JSON written with the inverted map
                tan = {"visible": "removed", "removed": "visible"}.get(tan, tan)
            print(f"   view {v.get('name')}: {v.get('feature_type')}/{v.get('type')} base={v.get('base_view','-')} "
                  f"orient={v.get('orientation','')!r} cfg={v.get('referenced_config')} model={v.get('referenced_model')} "
                  f"scale={v.get('scale')} rot={v.get('angle_deg')} "
                  f"tan={tan} {' '.join(flags)} pos={v.get('position_mm')}")
            if dtxt and BRIEF:
                kinds = Counter((dm.get("display_type") or "linear") for dm in dims)
                paren = sum(1 for dm in dims if dm.get("parenthesis") or dm.get("reference"))
                callouts = sum(1 for dm in dims if dm.get("callout_above") or dm.get("callout_below"))
                print(f"      dims({len(dtxt)}): {dict(kinds)} paren={paren} callouts={callouts}; sample: " + ", ".join(dtxt[:6]))
            elif dtxt:
                print(f"      dims({len(dtxt)}): " + ", ".join(dtxt))
            vnotes = v.get("notes", [])
            if BRIEF:
                c = Counter((("balloon%s" % n.get("balloon_style", "")) if n.get("balloon") else "note", short(n.get("text", ""), 40)) for n in vnotes)
                if c:
                    print("      notes: " + "; ".join(f"{k[0]} '{k[1]}' x{v}" if v > 1 else f"{k[0]} '{k[1]}'" for k, v in c.items()))
            else:
                for n in vnotes:
                    flags = []
                    if n.get("leader"):
                        flags.append("leader")
                    if n.get("balloon"):
                        flags.append(f"balloon{n.get('balloon_style','')}")
                    if n.get("attached_types"):
                        flags.append("→" + ",".join(n["attached_types"]))
                    print(f"      note [{','.join(flags)}] h={n.get('font_height_mm')}: {short(n.get('text',''), 120)}")
            for k in ("weld_symbols", "center_marks", "center_lines"):
                if v.get(k):
                    print(f"      {k}: {v[k]}")
            for t in v.get("tables", []):
                print(f"      table {t.get('type')} {t.get('rows')}x{t.get('cols')} '{t.get('title')}'")
                for r in (t.get("cells") or [])[:8]:
                    print("         | " + " | ".join(short(c, 30) for c in r))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--brief"]
    BRIEF = "--brief" in sys.argv
    for p in args:
        main(p)
        print()
