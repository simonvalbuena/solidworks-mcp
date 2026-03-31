"""組立件分析 tools — 讀取配合關係、特徵樹。"""

from __future__ import annotations

import json
import logging
import os

from mcp.server.fastmcp import FastMCP

from errors import SWError, ToolError
from sw_connection import SWConnection

logger = logging.getLogger(__name__)

SW_DOC_ASSEMBLY = 2

DOC_TYPE_NAMES = {
    1: "part",
    2: "assembly",
    3: "drawing",
}

SYSTEM_FEATURE_TYPES = {
    "CommentsFolder", "FavoriteFolder", "HistoryFolder",
    "SelectionSetFolder", "SensorFolder", "LiveSectionFolder",
    "DocsFolder", "DetailCabinet", "EnvFolder",
    "InkMarkupFolder", "EqnFolder", "MaterialFolder",
    "RefPlane", "RefAxis", "OriginProfileFeature",
    "MateGroup", "Reference",
}

MATE_TYPE_MAP = {
    0: "coincident",
    1: "concentric",
    2: "perpendicular",
    3: "parallel",
    4: "tangent",
    5: "distance",
    6: "angle",
    8: "symmetric",
    9: "cam_follower",
    10: "gear",
    11: "width",
    13: "rack_pinion",
    15: "path",
    16: "lock",
    17: "screw",
    18: "linear_coupler",
    19: "universal_joint",
    21: "slot",
    22: "hinge",
}

ENTITY_TYPE_MAP = {
    0: "point",
    1: "line",
    2: "plane",
    3: "cylinder",
    4: "cone",
}

# EntityParams 中 point (index 0-2) 和 radius (index 6-7) 的單位是公尺，
# 需乘 1000 轉為 mm。
_M_TO_MM = 1000.0


def is_system_feature(type_name: str) -> bool:
    """判斷 feature type name 是否為系統特徵（非建模特徵）。"""
    return type_name in SYSTEM_FEATURE_TYPES


def get_mate_type_name(type_int: int) -> str:
    """將 swMateType_e 整數轉為可讀字串。"""
    return MATE_TYPE_MAP.get(type_int, f"unknown({type_int})")


def parse_entity_params(params, ref_type: int) -> dict:
    """解析 IMateEntity2.EntityParams 回傳的 8-double 陣列。

    params: 8 個 double 的陣列/tuple（公尺制）。
    ref_type: IMateEntity2.ReferenceType2（swMateEntityReferenceType_e）。

    回傳 dict 含 entity_type, point, vector, radius1, radius2（mm 制）。
    """
    values = list(params) if params is not None else [0.0] * 8
    if len(values) < 8:
        values.extend([0.0] * (8 - len(values)))

    entity_type = ENTITY_TYPE_MAP.get(ref_type, f"unknown({ref_type})")

    # point (index 0-2): 公尺 → mm
    point = [values[i] * _M_TO_MM for i in range(3)]
    # vector (index 3-5): 無因次方向向量，不需轉換
    vector = [values[i] for i in range(3, 6)]
    # radius (index 6-7): 公尺 → mm
    radius1 = values[6] * _M_TO_MM
    radius2 = values[7] * _M_TO_MM

    return {
        "entity_type": entity_type,
        "point": point,
        "vector": vector,
        "radius1": radius1,
        "radius2": radius2,
    }


def register_tools(mcp: FastMCP, sw: SWConnection) -> None:

    @mcp.tool()
    async def read_assembly_mates(part_name: str) -> str:
        """讀取組立件中指定零件的所有配合關係（Mates）。
        part_name: 目標零件的 component name（例如 "bracket-1"）。
        回傳配合類型、參考幾何、bounding box。"""
        try:
            result = await sw.execute(_read_assembly_mates, part_name)
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"read_assembly_mates 失敗: {e}")

    @mcp.tool()
    async def get_feature_tree(
        doc_name: str | None = None,
        include_all: bool = False,
    ) -> str:
        """讀取文件的特徵樹（Feature Tree）。
        doc_name: 文件名稱，預設活動文件。
        include_all: True 回傳全部特徵，False 只回傳建模特徵。
        回傳特徵列表、bounding box。"""
        try:
            result = await sw.execute(_get_feature_tree, doc_name, include_all)
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"get_feature_tree 失敗: {e}")
        except Exception as e:
            raise ToolError(f"get_feature_tree 未預期錯誤: {e}")


def _read_assembly_mates(part_name: str) -> dict:
    """COM 操作：讀取組立件中指定零件的配合關係。

    使用 MateGroup Feature 遍歷法取得 mate name 和 IMate2 介面。
    """
    sw_conn = SWConnection.get_instance()
    doc = sw_conn.get_active_doc()

    # 確認是組立件
    if doc.GetType != SW_DOC_ASSEMBLY:
        raise SWError("目前文件不是組立件（Assembly）")

    # --- 找 target component（用於取 bounding box）---
    components = doc.GetComponents(False)
    if components is None:
        raise SWError("無法取得組立件元件列表")

    target_comp = None
    for comp in components:
        if comp.Name2 == part_name:
            target_comp = comp
            break

    if target_comp is None:
        names = [c.Name2 for c in components]
        raise SWError(f"找不到零件: {part_name}，可用: {names}")

    # --- 遍歷 MateGroup Feature 取配合 ---
    mates_list = []
    feat = doc.FirstFeature
    while feat is not None:
        try:
            type_name = feat.GetTypeName2
            if type_name == "MateGroup":
                sub_feat = feat.GetFirstSubFeature
                while sub_feat is not None:
                    mate_info = _process_mate_feature(sub_feat, part_name)
                    if mate_info is not None:
                        mates_list.append(mate_info)
                    sub_feat = sub_feat.GetNextSubFeature
        except Exception as e:
            logger.warning("Feature traversal error: %s", e)
        feat = feat.GetNextFeature

    # --- Bounding box ---
    bbox = _get_component_bbox(target_comp)

    result = {
        "part_name": part_name,
        "total_mates": len(mates_list),
        "mates": mates_list,
    }
    if bbox is not None:
        result["bounding_box"] = bbox

    return result


def _process_mate_feature(sub_feat, part_name: str) -> dict | None:
    """處理單一 mate sub-feature，若與 target component 相關則回傳 dict。"""
    try:
        mate = sub_feat.GetSpecificFeature2
    except Exception:
        return None

    if mate is None:
        return None

    # 取 mate type
    try:
        mate_type = mate.Type
    except Exception:
        # 可能是 IMateInPlace 等不支援 Type 的物件
        return None

    # 取 mate entities
    try:
        entity_count = mate.GetMateEntityCount()
    except Exception:
        return None

    # 檢查是否與 target component 相關
    entities = []
    is_related = False
    for i in range(entity_count):
        try:
            entity = mate.MateEntity(i)
            if entity is None:
                continue
            comp = entity.ReferenceComponent
            comp_name = comp.Name2 if comp is not None else "unknown"
            if comp_name == part_name:
                is_related = True

            ref_type = entity.ReferenceType2
            params = entity.EntityParams
            parsed = parse_entity_params(params, ref_type)
            parsed["component"] = comp_name
            entities.append(parsed)
        except Exception:
            continue

    if not is_related:
        return None

    mate_name = sub_feat.Name
    return {
        "name": mate_name,
        "type": get_mate_type_name(mate_type),
        "entities": entities,
    }


def _get_component_bbox(comp) -> dict | None:
    """嘗試從 component 取 bounding box（mm 制）。

    使用 IComponent2::GetBox 取得組裝環境下的完整 bbox，
    可正確處理子組立件和多本體零件。
    """
    try:
        box = comp.GetBox(False, False)
        if box is None:
            return None
        values = list(box)
        if len(values) < 6:
            return None
        return {
            "min": [values[i] * _M_TO_MM for i in range(3)],
            "max": [values[i] * _M_TO_MM for i in range(3, 6)],
        }
    except Exception:
        return None


def _get_feature_tree(doc_name: str | None, include_all: bool) -> dict:
    """COM 操作：讀取文件的特徵樹。"""
    sw_conn = SWConnection.get_instance()

    # --- 取文件 ---
    if doc_name is None:
        doc = sw_conn.get_active_doc()
    else:
        app = sw_conn.get_app()
        docs = app.GetDocuments
        if docs is None:
            raise SWError(f"找不到文件: {doc_name}")
        found = None
        for d in docs:
            title = d.GetTitle
            path_name = d.GetPathName
            basename = os.path.basename(path_name) if path_name else ""
            if title == doc_name or basename == doc_name:
                found = d
                break
        if found is None:
            raise SWError(f"找不到文件: {doc_name}")
        doc = found

    # --- doc type ---
    doc_type = doc.GetType
    doc_type_str = DOC_TYPE_NAMES.get(doc_type, f"unknown({doc_type})")

    # --- 文件名稱 ---
    path_name = doc.GetPathName
    if path_name:
        name = os.path.basename(path_name)
    else:
        name = doc.GetTitle

    # --- 遍歷特徵樹 ---
    features = []
    feat = doc.FirstFeature
    while feat is not None:
        try:
            type_name = feat.GetTypeName2
            if not include_all and is_system_feature(type_name):
                feat = feat.GetNextFeature
                continue
            suppressed = bool(feat.IsSuppressed)
            features.append({
                "name": feat.Name,
                "type": type_name,
                "suppressed": suppressed,
            })
        except Exception as e:
            logger.warning("Feature traversal error: %s", e)
        feat = feat.GetNextFeature

    # --- 組合結果 ---
    result = {
        "doc_name": name,
        "doc_type": doc_type_str,
        "feature_count": len(features),
        "features": features,
    }

    # --- Bounding box ---
    bbox = _get_doc_bbox(doc, doc_type)
    if bbox is not None:
        result["bounding_box"] = bbox

    return result


def _get_doc_bbox(doc, doc_type: int) -> dict | None:
    """取文件的 bounding box（mm 制）。

    Part: 從第一個 visible body 取。
    Assembly: 從所有 component 取聯集。
    Drawing: 不取。
    """
    if doc_type == 3:  # drawing
        return None

    try:
        if doc_type == 1:  # part
            # 透過 SolidBodyFolder feature 取 body（避免 GetBodies2 COM 參數問題）
            feat = doc.FirstFeature
            body = None
            while feat is not None:
                if feat.GetTypeName2 == "SolidBodyFolder":
                    folder = feat.GetSpecificFeature2
                    if folder is not None:
                        bodies = folder.GetBodies
                        if bodies is not None and len(bodies) > 0:
                            body = bodies[0]
                    break
                feat = feat.GetNextFeature
            if body is None:
                return None
            box = body.GetBodyBox()
            if box is None:
                return None
            values = list(box)
            if len(values) < 6:
                return None
            return {
                "min": [values[i] * _M_TO_MM for i in range(3)],
                "max": [values[i] * _M_TO_MM for i in range(3, 6)],
            }

        if doc_type == 2:  # assembly
            components = doc.GetComponents(False)
            if components is None or len(components) == 0:
                return None
            min_vals = [float("inf")] * 3
            max_vals = [float("-inf")] * 3
            has_any = False
            for comp in components:
                comp_bbox = _get_component_bbox(comp)
                if comp_bbox is None:
                    continue
                has_any = True
                for i in range(3):
                    if comp_bbox["min"][i] < min_vals[i]:
                        min_vals[i] = comp_bbox["min"][i]
                    if comp_bbox["max"][i] > max_vals[i]:
                        max_vals[i] = comp_bbox["max"][i]
            if not has_any:
                return None
            return {
                "min": min_vals,
                "max": max_vals,
            }
    except Exception:
        return None

    return None
