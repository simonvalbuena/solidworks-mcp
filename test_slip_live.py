r"""Live smoke test for src/tools/slip.py against the running SolidWorks — no MCP, no restart.

  & .\.venv\Scripts\python.exe .\test_slip_live.py            # read-only checks
  & .\.venv\Scripts\python.exe .\test_slip_live.py --mutate   # also: tangent edges + delete Sheet2 on the active drawing

Have the target DRAWING active in SolidWorks first.
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
conn._sw_app = win32com.client.Dispatch("SldWorks.Application")  # main-thread test harness


def step(label, fn):
    try:
        r = fn()
        print(f"OK   {label}:\n     {json.dumps(r, default=str)[:600]}")
        return r
    except Exception as e:  # noqa: BLE001
        print(f"FAIL {label}: {type(e).__name__}: {e}")
        return None


print("== read-only")
step("list_sheets", slip._list_sheets)
step("list_notes", lambda: slip._list_notes(None))
step("list_dimensions", lambda: slip._list_dimensions(None))
step("sheet view Name", lambda: str(slip._inv(slip._sheet_view(slip._active_drawing()), "Name")))

if "--mutate" in sys.argv:
    print("\n== mutate (active drawing)")
    step("set_view_display iso visible", lambda: slip._set_view_display("Drawing View3", "hidden_lines_removed", "visible"))
    sheets = slip._list_sheets()["sheets"]
    if "Sheet2" in sheets:
        step("delete_sheet Sheet2", lambda: slip._delete_sheet("Sheet2"))
    else:
        print("skip delete_sheet: no Sheet2")
print("DONE")
