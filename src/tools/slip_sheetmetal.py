"""Sheet-metal Tab: sheet_metal_tab.

A SolidWorks "Tab" is not a separate API feature: it is IFeatureManager.InsertSheetMetalBaseFlange2
on a closed sketch lying on a face of an existing sheet-metal body with Merge = True (the feature
then shows as "Tab<n>", type SMBaseFlange). Thickness, bend radius and K-factor are taken from the
part's Sheet-Metal feature so the tab inherits the part's parameters, like the interactive command.
Signature (sldworks.tlb, SW 2025):
  InsertSheetMetalBaseFlange2(Thickness, ThickenDir, Radius, ExtrudeDist1, ExtrudeDist2, FlipExtruDir,
      EndCondition1, EndCondition2, DirToUse, PCBA, UseDefaultRelief, ReliefType, ReliefWidth,
      ReliefDepth, ReliefRatio, UseReliefRatio, Merge, UseFeatScope, UseAutoSelect)
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from errors import SWError
from sw_connection import SWConnection
from tools import slip
from tools import slip_tube
from tools import slip_weldment as sw_w

logger = logging.getLogger(__name__)


def _sheet_metal_params(doc):
    """Thickness / bend radius / K-factor of the first Sheet-Metal feature."""
    for name, ftype in sw_w._feature_types(doc):
        if ftype == "SheetMetal":
            d = slip._inv(sw_w._feature(doc, name), "GetDefinition")
            return {"feature": name,
                    "thickness": float(slip._inv(d, "Thickness")),
                    "bend_radius": float(slip._inv(d, "BendRadius")),
                    "k_factor": float(slip._inv(d, "KFactor"))}
    raise SWError("the part has no Sheet-Metal feature (make the base flange first)")


def _sheet_metal_tab(sketch_name, reverse):
    app, doc = sw_w._active_part()
    log = []
    skm = slip._inv(doc, "SketchManager")
    try:
        if slip._inv(skm, "ActiveSketch") is not None:
            slip._inv(skm, "InsertSketch", True)
            log.append("closed active sketch")
    except Exception as ex:  # noqa: BLE001
        log.append(f"close sketch: {str(ex)[:60]}")
    p = _sheet_metal_params(doc)
    before = {n for n, _t in sw_w._feature_types(doc)}
    bodies_before = sw_w._body_report(doc).get("bodies")
    slip._inv(doc, "ClearSelection2", True)
    feat = sw_w._feature(doc, sketch_name)
    if not bool(slip._inv(feat, "Select2", False, 0)):
        raise SWError(f"could not select sketch {sketch_name!r}")
    fm = slip._inv(doc, "FeatureManager")
    cba = slip._inv(fm, "CreateCustomBendAllowance")
    try:
        slip._put(cba, "Type", 2)          # swBendAllowanceKFactor
        slip._put(cba, "KFactor", p["k_factor"])
    except Exception as ex:  # noqa: BLE001
        log.append(f"bend allowance: {str(ex)[:60]}")
    res = slip._inv(fm, "InsertSheetMetalBaseFlange2",
                    p["thickness"], bool(reverse), p["bend_radius"], 0.0, 0.0, False,
                    0, 0, 0, cba, True, 1, 0.00635, 0.00635, 0.5, True,
                    True, False, True)
    if res is None:
        raise SWError("InsertSheetMetalBaseFlange2 (merge) returned None — is the sketch closed and on "
                      "a face of the sheet-metal body, touching it?")
    try:
        slip._inv(doc, "EditRebuild3")
    except Exception:  # noqa: BLE001
        pass
    new = [(n, t) for n, t in sw_w._feature_types(doc) if n not in before]
    rep = sw_w._body_report(doc)
    out = {"status": "done", "feature": str(slip._inv(res, "Name")), "new_features": new,
           "sheet_metal": p, "bodies_before": bodies_before, "log": log, **rep}
    if rep.get("bodies") and bodies_before and rep["bodies"] > bodies_before:
        out["warning"] = ("body count went up: the profile does not touch the body — flip "
                          "reverse_thickness or check the sketch face")
    return out


def register_tools(mcp: FastMCP, sw: SWConnection) -> None:

    @mcp.tool()
    async def sheet_metal_tab(sketch_name: str, reverse_thickness: bool = False) -> str:
        """Sheet-metal TAB in the ACTIVE part: a closed sketch on a face of the sheet-metal body
        becomes a merged base flange ("Tab<n>") with the part's thickness, bend radius and K-factor.
        reverse_thickness flips the thickness direction (default: into the body, as the
        interactive Tab does). Reports body count before/after so a non-merged result is caught."""
        return await slip_tube._run(sw, "sheet_metal_tab", _sheet_metal_tab, sketch_name, reverse_thickness)
