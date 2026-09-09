r"""Live harness for src/tools/slip_batch.py (no MCP, no restart). Drawing must be active.

  .\.venv\Scripts\python.exe .\batch_tool.py geom                    # all views, outline+summary only
  .\.venv\Scripts\python.exe .\batch_tool.py geom "Drawing View1" full
  .\.venv\Scripts\python.exe .\batch_tool.py dims "Drawing View1" dims.json
  .\.venv\Scripts\python.exe .\batch_tool.py finish [iso_view] [finish_text]
"""
import json
import os
import sys

import pythoncom
import win32com.client

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
pythoncom.CoInitialize()

from sw_connection import SWConnection  # noqa: E402
from tools import slip_batch as sb  # noqa: E402

conn = SWConnection()
conn._sw_app = win32com.client.Dispatch("SldWorks.Application")

cmd = sys.argv[1] if len(sys.argv) > 1 else "geom"
try:
    if cmd == "geom":
        view = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "all" else None
        full = len(sys.argv) > 3 and sys.argv[3] == "full"
        print(json.dumps(sb._view_geometry(view, full), indent=1, default=str))
    elif cmd == "dims":
        dims = json.load(open(sys.argv[3], encoding="utf-8"))
        print(json.dumps(sb._add_dimensions(sys.argv[2], dims), indent=1, default=str))
    elif cmd == "finish":
        iso = sys.argv[2] if len(sys.argv) > 2 else "Drawing View3"
        fin = sys.argv[3] if len(sys.argv) > 3 else None
        print(json.dumps(sb._finish_slip_r00(iso, fin, True), indent=1, default=str))
    else:
        print(__doc__)
except Exception as ex:  # noqa: BLE001
    import traceback; traceback.print_exc()
    print(f"FAIL {type(ex).__name__}: {ex}")
