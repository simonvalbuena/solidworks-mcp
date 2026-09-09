r"""Dump / edit sheet notes on the active drawing via src/tools/slip.py (no MCP needed).

  .\.venv\Scripts\python.exe .\notes_tool.py dump                 # full linked text of every note
  .\.venv\Scripts\python.exe .\notes_tool.py apply edits.json     # [{"note": "...", "find": "...", "replace": "..."}, ...]
"""
import json
import os
import sys

import pythoncom
import win32com.client

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
pythoncom.CoInitialize()

from sw_connection import SWConnection  # noqa: E402
from tools import slip  # noqa: E402

conn = SWConnection()
conn._sw_app = win32com.client.Dispatch("SldWorks.Application")

cmd = sys.argv[1] if len(sys.argv) > 1 else "dump"

if cmd == "dump":
    res = slip._list_notes(None)
    print(f"sheet={res['sheet']} notes={res['count']}")
    for n in res["notes"]:
        lt = n.get("linked_text") or ""
        if not lt.strip():
            continue
        print(f"\n--- {n['name']} ---")
        print(repr(lt))
elif cmd == "apply":
    edits = json.load(open(sys.argv[2], encoding="utf-8"))
    for e in edits:
        try:
            r = slip._replace_in_note(e["note"], e["find"], e.get("replace", ""))
            print(f"OK   {e['note']}: {r['occurrences']}x {e['find'][:50]!r}")
        except Exception as ex:  # noqa: BLE001
            print(f"FAIL {e['note']}: {ex}")
    print("\nafter:")
    print(repr(slip._note_info(slip._find_note(slip._active_drawing(), edits[0]["note"]))["linked_text"]))
else:
    print(__doc__)
