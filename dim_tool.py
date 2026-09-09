r"""List / edit display dimensions on the active drawing via src/tools/slip.py (no MCP needed).

  .\.venv\Scripts\python.exe .\dim_tool.py list
  .\.venv\Scripts\python.exe .\dim_tool.py ref "RD1@Drawing View2@108543.Drawing"      # show parentheses
  .\.venv\Scripts\python.exe .\dim_tool.py unref "RD1@Drawing View2@108543.Drawing"    # remove them
  .\.venv\Scripts\python.exe .\dim_tool.py move "RD3@Drawing View1@108543.Drawing" 258 340
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

cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
try:
    if cmd == "list":
        print(json.dumps(slip._list_dimensions(None), indent=1, default=str))
    elif cmd in ("ref", "unref"):
        print(json.dumps(slip._set_dimension_reference(sys.argv[2], cmd == "ref"), indent=1, default=str))
    elif cmd == "move":
        print(json.dumps(slip._move_dimension_text(sys.argv[2], float(sys.argv[3]), float(sys.argv[4])), indent=1, default=str))
    else:
        print(__doc__)
except Exception as ex:  # noqa: BLE001
    print(f"FAIL {type(ex).__name__}: {ex}")
