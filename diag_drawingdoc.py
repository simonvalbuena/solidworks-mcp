"""Diagnose how to reach IDrawingDoc / sheet-view members from pywin32.

Run in the solidworks-mcp venv with SolidWorks open and a DRAWING active:
  & "$env:USERPROFILE\\source\\solidworks-mcp\\.venv\\Scripts\\python.exe" diag_drawingdoc.py
Paste the whole output back.
"""
import glob
import os
import sys
import traceback

import pythoncom
import win32com.client
from win32com.client import gencache

pythoncom.CoInitialize()

def show(label, fn):
    try:
        v = fn()
        s = repr(v)
        print(f"  OK   {label}: {s[:160]}")
        return v
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL {label}: {type(e).__name__}: {str(e)[:200]}")
        return None

print("== 1. connect")
app_early = show("EnsureDispatch(SldWorks.Application)", lambda: gencache.EnsureDispatch("SldWorks.Application"))
app_late = show("Dispatch(SldWorks.Application)", lambda: win32com.client.Dispatch("SldWorks.Application"))
app = app_early or app_late
if app is None:
    sys.exit("SolidWorks not reachable")
show("RevisionNumber", lambda: app.RevisionNumber)

print("\n== 2. active document (early-bound app)")
doc = show("ActiveDoc", lambda: app.ActiveDoc)
if doc is None:
    sys.exit("open a drawing first")
print("  type(doc) =", type(doc), " class:", getattr(doc, "__class__", None))
show("GetType (3 = drawing)", lambda: doc.GetType)
show("GetType()", lambda: doc.GetType())
show("GetTitle", lambda: doc.GetTitle)

print("\n== 3. IDrawingDoc members directly on doc")
for name in ("GetSheetNames", "GetCurrentSheet", "GetViews", "GetFirstView", "ActivateSheet"):
    show(f"getattr {name}", lambda n=name: getattr(doc, n))
show("doc.GetSheetNames()", lambda: doc.GetSheetNames())
show("doc.GetSheetNames (prop)", lambda: doc.GetSheetNames)
show("doc.GetCurrentSheet()", lambda: doc.GetCurrentSheet())
show("doc.GetFirstView()", lambda: doc.GetFirstView())
show("doc.GetViews()", lambda: doc.GetViews())

print("\n== 4. CastTo IDrawingDoc")
dd = show("CastTo(doc,'IDrawingDoc')", lambda: win32com.client.CastTo(doc, "IDrawingDoc"))
if dd is not None:
    print("  type(dd) =", type(dd))
    show("dd.GetSheetNames()", lambda: dd.GetSheetNames())
    show("dd.GetSheetNames (prop)", lambda: dd.GetSheetNames)
    show("dd.GetCurrentSheet()", lambda: dd.GetCurrentSheet())
    show("dd.GetFirstView()", lambda: dd.GetFirstView())
    show("dd.GetFirstView (prop)", lambda: dd.GetFirstView)
    show("dd.GetViews()", lambda: dd.GetViews())
    show("dd.GetViews (prop)", lambda: dd.GetViews)

print("\n== 5. late-bound app path")
if app_late is not None:
    ldoc = show("late app.ActiveDoc", lambda: app_late.ActiveDoc)
    if ldoc is not None:
        print("  type(ldoc) =", type(ldoc))
        show("ldoc.GetSheetNames()", lambda: ldoc.GetSheetNames())
        show("ldoc.GetSheetNames (prop)", lambda: ldoc.GetSheetNames)
        show("ldoc.GetCurrentSheet()", lambda: ldoc.GetCurrentSheet())
        show("ldoc.GetFirstView()", lambda: ldoc.GetFirstView())
        show("ldoc.GetFirstView (prop)", lambda: ldoc.GetFirstView)
        show("ldoc.GetViews()", lambda: ldoc.GetViews())
        show("ldoc.GetViews (prop)", lambda: ldoc.GetViews)

print("\n== 6. QueryInterface by IID from sldworks.tlb")
tlbs = glob.glob(r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS*\sldworks.tlb")
try:
    tlbs.insert(0, os.path.join(os.path.dirname(app.GetExecutablePath), "sldworks.tlb"))
except Exception:  # noqa: BLE001
    pass
print("  candidates:", tlbs)
iid = None
for path in tlbs:
    if not os.path.exists(path):
        continue
    try:
        tlb = pythoncom.LoadTypeLib(path)
        for i in range(tlb.GetTypeInfoCount()):
            if tlb.GetDocumentation(i)[0] == "IDrawingDoc":
                iid = tlb.GetTypeInfo(i).GetTypeAttr().iid
                print(f"  IDrawingDoc IID from {path}: {iid}")
                break
    except Exception as e:  # noqa: BLE001
        print("  typelib load failed:", path, e)
    if iid:
        break
if iid:
    raw = show("doc._oleobj_.QueryInterface(iid)", lambda: doc._oleobj_.QueryInterface(iid))
    if raw is not None:
        qd = show("Dispatch(raw)", lambda: win32com.client.Dispatch(raw))
        if qd is not None:
            print("  type(qd) =", type(qd))
            show("qd.GetSheetNames()", lambda: qd.GetSheetNames())
            show("qd.GetSheetNames (prop)", lambda: qd.GetSheetNames)
            show("qd.GetCurrentSheet()", lambda: qd.GetCurrentSheet())
            show("qd.GetFirstView()", lambda: qd.GetFirstView())
            show("qd.GetFirstView (prop)", lambda: qd.GetFirstView)
            show("qd.GetViews()", lambda: qd.GetViews())
    raw2 = show("QueryInterface(iid, IID_IDispatch)", lambda: doc._oleobj_.QueryInterface(iid, pythoncom.IID_IDispatch))

print("\n== 7. raw Invoke on doc (GetIDsOfNames + flags)")
ole = doc._oleobj_
for name in ("GetSheetNames", "GetCurrentSheet", "GetFirstView", "GetViews", "IGetFirstView"):
    try:
        dispid = ole.GetIDsOfNames(0, name)
        print(f"  {name}: dispid={dispid}")
    except Exception as e:  # noqa: BLE001
        print(f"  {name}: GetIDsOfNames FAIL {e}")
        continue
    for flag, fl in ((1, "METHOD"), (2, "PROPGET"), (3, "METHOD|PROPGET")):
        show(f"    Invoke {name} {fl}", lambda d=dispid, f=flag: ole.Invoke(d, 0, f, True))

print("\n== 8. what DOES work: Extension + feature tree")
ext = show("doc.Extension", lambda: doc.Extension)
show("doc.FirstFeature", lambda: doc.FirstFeature)
show("doc.FirstFeature.GetTypeName2", lambda: doc.FirstFeature.GetTypeName2)
feat = doc.FirstFeature
if feat is not None:
    show("DrSheet -> GetSpecificFeature2", lambda: feat.GetSpecificFeature2())
    spec = None
    try:
        spec = feat.GetSpecificFeature2()
    except Exception:  # noqa: BLE001
        try:
            spec = feat.GetSpecificFeature2
        except Exception:  # noqa: BLE001
            pass
    if spec is not None:
        print("  type(sheet spec) =", type(spec))
        show("sheet.GetName()", lambda: spec.GetName())
        show("sheet.GetName (prop)", lambda: spec.GetName)
        show("sheet.GetViews()", lambda: spec.GetViews())
        show("sheet.GetViews (prop)", lambda: spec.GetViews)
        show("sheet.GetProperties2()", lambda: spec.GetProperties2())
print("\nDONE")
