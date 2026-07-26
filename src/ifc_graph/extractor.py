from __future__ import annotations

import csv
import json
import logging
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

from .fallback_step import build_graph as fallback_build_graph
from .utils import json_safe, write_json

LOGGER = logging.getLogger(__name__)

RELATIONSHIP_ALLOWLIST = {
    "BELONGS_TO_SPATIAL", "NESTED_IN", "CONTAINED_IN", "REFERENCED_IN",
    "HAS_OPENING", "FILLS_OPENING", "CONNECTED_TO", "PORT_CONNECTED_TO",
    "PORT_OF", "ASSIGNED_TO_GROUP", "IS_TYPED_BY", "HAS_PROPERTY_SET",
    "HAS_MATERIAL", "BOUNDED_BY", "SERVICES", "ASSIGNED_TO_PROCESS",
    "ASSIGNED_TO_PRODUCT", "STRUCTURAL_CONNECTION", "CONTROLS_FLOW",
    "PRECEDES", "CLASSIFIED_AS", "DECLARED_IN", "REALIZED_BY",
}

SPATIAL_CLASSES = {
    "IFCPROJECT", "IFCSITE", "IFCBUILDING", "IFCBUILDINGSTOREY", "IFCSPACE",
    "IFCFACILITY", "IFCFACILITYPART", "IFCBRIDGE", "IFCROAD", "IFCRAILWAY",
    "IFCMARINEFACILITY", "IFCEXTERNALSPATIALELEMENT", "IFCSPATIALZONE",
}


def _is_sensor_class(cls: str) -> bool:
    return any(token in cls for token in ("SENSOR", "ACTUATOR", "CONTROLLER", "ALARM"))


def _is_mep_class(cls: str) -> bool:
    return cls.startswith((
        "IFCPIPE", "IFCDUCT", "IFCFLOW", "IFCDISTRIBUTION", "IFCCABLE",
        "IFCPUMP", "IFCVALVE", "IFCBOILER", "IFCCHILLER", "IFCFAN",
        "IFCAIRTERMINAL", "IFCLIGHTFIXTURE", "IFCELECTRIC", "IFCOUTLET",
        "IFCPROTECTIVEDEVICE", "IFCSWITCHINGDEVICE", "IFCTANK", "IFCPORT",
    )) or cls == "IFCDISTRIBUTIONPORT"


def _is_structural_class(cls: str) -> bool:
    return cls.startswith((
        "IFCCOLUMN", "IFCBEAM", "IFCMEMBER", "IFCFOOTING", "IFCPILE",
        "IFCPLATE", "IFCREINFORC", "IFCSTRUCTURAL",
    ))


def _typology(ifc_class: str) -> str:
    cls = ifc_class.upper()
    if cls in SPATIAL_CLASSES:
        return "Space" if cls == "IFCSPACE" else "Spatial"
    if _is_sensor_class(cls):
        return "Sensor"
    if _is_mep_class(cls):
        return "MEP"
    if _is_structural_class(cls):
        return "Structural"
    if any(token in cls for token in (
        "WALL", "WINDOW", "DOOR", "SLAB", "ROOF", "STAIR", "RAILING",
        "CURTAINWALL", "COVERING", "FURNISH", "BUILDINGELEMENTPROXY",
    )):
        return "Architectural"
    return "Other"


def _semantic_label(entity: Any, ifc_class: str) -> str:
    cls = ifc_class.upper()
    try:
        if entity.is_a("IfcSpatialElement") or entity.is_a("IfcSpatialStructureElement"):
            return "SpatialElement"
    except Exception:
        pass
    if cls in SPATIAL_CLASSES:
        return "SpatialElement"
    try:
        if entity.is_a("IfcTypeObject"):
            return "Type"
        if entity.is_a("IfcPropertySetDefinition"):
            return "PropertySet"
        if entity.is_a("IfcGroup"):
            return "System"
        if entity.is_a("IfcProduct") or entity.is_a("IfcPort"):
            return "Product"
    except Exception:
        pass
    if cls.startswith("IFCMATERIAL"):
        return "Material"
    if "CLASSIFICATION" in cls:
        return "Classification"
    return "Entity"


def _is_semantic(entity: Any) -> bool:
    cls = entity.is_a().upper()
    if cls.startswith("IFCREL"):
        return False
    if cls.startswith((
        "IFCGEOMETRIC", "IFCREPRESENTATION", "IFCPROFILE", "IFCSTYLE",
        "IFCCOLOUR", "IFCTOPOLOGICAL", "IFCBOOLEAN", "IFCTEXTURE",
        "IFCPRESENTATION", "IFCMEASUREWITHUNIT", "IFCUNITASSIGNMENT",
    )):
        return False
    try:
        return bool(
            entity.is_a("IfcRoot")
            or entity.is_a("IfcMaterialDefinition")
            or entity.is_a("IfcClassification")
            or entity.is_a("IfcClassificationReference")
        )
    except Exception:
        return hasattr(entity, "GlobalId") or cls.startswith("IFCMATERIAL")


def _placement(entity: Any) -> tuple[float | None, float | None, float | None]:
    placement = getattr(entity, "ObjectPlacement", None)
    if not placement:
        return None, None, None
    try:
        from ifcopenshell.util.placement import get_local_placement

        matrix = get_local_placement(placement)
        return float(matrix[0][3]), float(matrix[1][3]), float(matrix[2][3])
    except Exception:
        return None, None, None


def _element_properties(entity: Any) -> dict[str, Any]:
    try:
        from ifcopenshell.util.element import get_psets

        raw = get_psets(entity, psets_only=False, qtos_only=False, should_inherit=True)
        return json_safe(raw)
    except Exception:
        return {}


def _root_name(entity: Any, ifc_class: str) -> tuple[str | None, str | None, str | None]:
    global_id = getattr(entity, "GlobalId", None)
    name = getattr(entity, "Name", None)
    description = getattr(entity, "Description", None)
    if not name and ifc_class.upper().startswith("IFCMATERIAL"):
        name = getattr(entity, "Name", None) or getattr(entity, "Category", None)
    return global_id, name, description


def _entity_refs(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (tuple, list, set)):
        return [v for v in value if hasattr(v, "id")]
    return [value] if hasattr(value, "id") else []


def _rel_edges(rel: Any) -> list[tuple[Any, Any, str]]:
    cls = rel.is_a()
    out: list[tuple[Any, Any, str]] = []

    def many(sources: Iterable[Any], targets: Iterable[Any], rel_type: str) -> None:
        for source in sources:
            for target in targets:
                if source is not None and target is not None:
                    out.append((source, target, rel_type))

    if cls in {"IfcRelAggregates", "IfcRelNests"}:
        rel_type = "BELONGS_TO_SPATIAL" if cls == "IfcRelAggregates" else "NESTED_IN"
        many(_entity_refs(getattr(rel, "RelatedObjects", None)), _entity_refs(getattr(rel, "RelatingObject", None)), rel_type)
    elif cls == "IfcRelContainedInSpatialStructure":
        many(_entity_refs(rel.RelatedElements), _entity_refs(rel.RelatingStructure), "CONTAINED_IN")
    elif cls == "IfcRelReferencedInSpatialStructure":
        many(_entity_refs(rel.RelatedElements), _entity_refs(rel.RelatingStructure), "REFERENCED_IN")
    elif cls == "IfcRelVoidsElement":
        many(_entity_refs(rel.RelatingBuildingElement), _entity_refs(rel.RelatedOpeningElement), "HAS_OPENING")
    elif cls == "IfcRelFillsElement":
        many(_entity_refs(rel.RelatedBuildingElement), _entity_refs(rel.RelatingOpeningElement), "FILLS_OPENING")
    elif cls in {"IfcRelConnectsElements", "IfcRelConnectsPathElements"}:
        many(_entity_refs(rel.RelatingElement), _entity_refs(rel.RelatedElement), "CONNECTED_TO")
    elif cls == "IfcRelConnectsPorts":
        many(_entity_refs(rel.RelatingPort), _entity_refs(rel.RelatedPort), "PORT_CONNECTED_TO")
    elif cls == "IfcRelConnectsPortToElement":
        many(_entity_refs(rel.RelatingPort), _entity_refs(rel.RelatedElement), "PORT_OF")
    elif cls == "IfcRelAssignsToGroup":
        many(_entity_refs(rel.RelatedObjects), _entity_refs(rel.RelatingGroup), "ASSIGNED_TO_GROUP")
    elif cls == "IfcRelDefinesByType":
        many(_entity_refs(rel.RelatedObjects), _entity_refs(rel.RelatingType), "IS_TYPED_BY")
    elif cls == "IfcRelDefinesByProperties":
        many(_entity_refs(rel.RelatedObjects), _entity_refs(rel.RelatingPropertyDefinition), "HAS_PROPERTY_SET")
    elif cls == "IfcRelAssociatesMaterial":
        many(_entity_refs(rel.RelatedObjects), _entity_refs(rel.RelatingMaterial), "HAS_MATERIAL")
    elif cls.startswith("IfcRelSpaceBoundary"):
        many(_entity_refs(getattr(rel, "RelatingSpace", None)), _entity_refs(getattr(rel, "RelatedBuildingElement", None)), "BOUNDED_BY")
    elif cls == "IfcRelServicesBuildings":
        many(_entity_refs(rel.RelatingSystem), _entity_refs(rel.RelatedBuildings), "SERVICES")
    elif cls == "IfcRelAssignsToProcess":
        many(_entity_refs(rel.RelatedObjects), _entity_refs(rel.RelatingProcess), "ASSIGNED_TO_PROCESS")
    elif cls == "IfcRelAssignsToProduct":
        many(_entity_refs(rel.RelatedObjects), _entity_refs(rel.RelatingProduct), "ASSIGNED_TO_PRODUCT")
    elif cls == "IfcRelConnectsStructuralMember":
        many(_entity_refs(rel.RelatingStructuralMember), _entity_refs(rel.RelatedStructuralConnection), "STRUCTURAL_CONNECTION")
    elif cls == "IfcRelFlowControlElements":
        many(_entity_refs(rel.RelatedControlElements), _entity_refs(rel.RelatingFlowElement), "CONTROLS_FLOW")
    elif cls == "IfcRelSequence":
        many(_entity_refs(rel.RelatingProcess), _entity_refs(rel.RelatedProcess), "PRECEDES")
    elif cls == "IfcRelAssociatesClassification":
        many(_entity_refs(rel.RelatedObjects), _entity_refs(rel.RelatingClassification), "CLASSIFIED_AS")
    elif cls == "IfcRelDeclares":
        many(_entity_refs(rel.RelatedDefinitions), _entity_refs(rel.RelatingContext), "DECLARED_IN")
    elif cls == "IfcRelConnectsWithRealizingElements":
        many(_entity_refs(rel.RelatingElement), _entity_refs(rel.RelatedElement), "CONNECTED_TO")
        many(_entity_refs(rel.RealizingElements), _entity_refs(rel.RelatingElement), "REALIZED_BY")
    return out


def _capability_summary(entity_counts: Counter[str], relationship_counts: Counter[str]) -> dict[str, Any]:
    upper_counts = Counter({k.upper(): v for k, v in entity_counts.items()})
    sensor_count = sum(v for k, v in upper_counts.items() if _is_sensor_class(k))
    mep_count = sum(v for k, v in upper_counts.items() if _is_mep_class(k))
    structural_count = sum(v for k, v in upper_counts.items() if _is_structural_class(k))
    spaces = upper_counts.get("IFCSPACE", 0)
    mep_edges = relationship_counts.get("PORT_CONNECTED_TO", 0) + relationship_counts.get("PORT_OF", 0) + relationship_counts.get("CONTROLS_FLOW", 0)
    structural_edges = relationship_counts.get("STRUCTURAL_CONNECTION", 0) + relationship_counts.get("CONNECTED_TO", 0)
    return {
        "spatial_navigation": {
            "available": bool(spaces or upper_counts.get("IFCBUILDINGSTOREY", 0)),
            "evidence": {"spaces": spaces, "storeys": upper_counts.get("IFCBUILDINGSTOREY", 0)},
        },
        "sensor_localization": {
            "available": sensor_count > 0 and spaces > 0,
            "evidence": {"sensor_entities": sensor_count, "spaces": spaces},
            "note": "External sensors can also be registered through the API and attached to any IFC entity.",
        },
        "mep_trace": {
            "available": mep_count > 0 and mep_edges > 0,
            "evidence": {"mep_entities": mep_count, "connectivity_edges": mep_edges},
        },
        "structural_connectivity": {
            "available": structural_count > 0 and structural_edges > 0,
            "engineering_verified": False,
            "evidence": {"structural_entities": structural_count, "connectivity_edges": structural_edges},
            "note": "IFC connectivity is not a substitute for structural analysis or a verified load path.",
        },
    }


def extract_with_ifcopenshell(ifc_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    import ifcopenshell

    model = ifcopenshell.open(str(ifc_path))
    schema = getattr(model, "schema", None)
    all_entities = list(model)
    entity_counts: Counter[str] = Counter(entity.is_a().upper() for entity in all_entities)
    semantic = {entity.id(): entity for entity in all_entities if _is_semantic(entity)}

    nodes: list[dict[str, Any]] = []
    node_by_id: dict[int, dict[str, Any]] = {}
    for step_id, entity in sorted(semantic.items()):
        ifc_class = entity.is_a().upper()
        global_id, name, description = _root_name(entity, ifc_class)
        x, y, z = _placement(entity)
        label = _semantic_label(entity, ifc_class)
        row: dict[str, Any] = {
            "step_id": step_id,
            "node_id": f"#{step_id}",
            "global_id": global_id,
            "name": name or f"Unnamed {ifc_class}",
            "description": description,
            "ifc_class": ifc_class,
            "label": label,
            "typology": _typology(ifc_class),
            "x": x,
            "y": y,
            "z": z,
        }
        elevation = getattr(entity, "Elevation", None)
        if elevation is not None:
            try:
                row["elevation"] = float(elevation)
            except Exception:
                pass
        if label in {"Product", "SpatialElement", "Type"}:
            props = _element_properties(entity)
            if props:
                row["properties_json"] = json.dumps(props, ensure_ascii=False)
        nodes.append(row)
        node_by_id[step_id] = row

    edges: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str, int]] = set()
    for rel in all_entities:
        if not rel.is_a().startswith("IfcRel"):
            continue
        try:
            candidates = _rel_edges(rel)
        except Exception as exc:
            LOGGER.debug("Skipping malformed relationship #%s %s: %s", rel.id(), rel.is_a(), exc)
            continue
        for source, target, rel_type in candidates:
            if rel_type not in RELATIONSHIP_ALLOWLIST:
                continue
            source_id, target_id = source.id(), target.id()
            if source_id not in semantic or target_id not in semantic:
                continue
            key = (source_id, target_id, rel_type, rel.id())
            if key in seen:
                continue
            seen.add(key)
            edges.append({
                "source_step_id": source_id,
                "source_node_id": f"#{source_id}",
                "target_step_id": target_id,
                "target_node_id": f"#{target_id}",
                "relationship": rel_type,
                "source_relation": rel.is_a().upper(),
                "relation_step_id": rel.id(),
            })

    parents: dict[int, list[int]] = defaultdict(list)
    for edge in edges:
        if edge["relationship"] in {"CONTAINED_IN", "BELONGS_TO_SPATIAL", "NESTED_IN", "REFERENCED_IN"}:
            parents[edge["source_step_id"]].append(edge["target_step_id"])

    def ancestor(start: int, wanted: set[str]) -> int | None:
        queue: deque[int] = deque(parents.get(start, []))
        visited: set[int] = set()
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            node = node_by_id.get(current)
            if node and node["ifc_class"] in wanted:
                return current
            queue.extend(parents.get(current, []))
        return None

    for node in nodes:
        sid = node["step_id"]
        storey_id = ancestor(sid, {"IFCBUILDINGSTOREY", "IFCFACILITYPART"})
        building_id = ancestor(sid, {"IFCBUILDING", "IFCFACILITY", "IFCBRIDGE", "IFCROAD", "IFCRAILWAY", "IFCMARINEFACILITY"})
        if storey_id and storey_id in node_by_id:
            node["storey_step_id"] = storey_id
            node["storey_name"] = node_by_id[storey_id]["name"]
        if building_id and building_id in node_by_id:
            node["building_step_id"] = building_id
            node["building_name"] = node_by_id[building_id]["name"]

    relationship_counts = Counter(edge["relationship"] for edge in edges)
    product_counts = Counter(node["ifc_class"] for node in nodes if node["label"] == "Product")
    typology_counts = Counter(node["typology"] for node in nodes if node["label"] == "Product")
    storeys = [node for node in nodes if node["ifc_class"] in {"IFCBUILDINGSTOREY", "IFCFACILITYPART"}]
    storeys.sort(key=lambda item: (item.get("elevation") is None, item.get("elevation") or 0, item["name"]))

    by_storey: dict[str, Counter[str]] = defaultdict(Counter)
    for node in nodes:
        if node.get("storey_name") and node["label"] == "Product":
            by_storey[node["storey_name"]][node["ifc_class"]] += 1

    summary = {
        "source_file": ifc_path.name,
        "schema": schema,
        "parser": f"IfcOpenShell {getattr(ifcopenshell, 'version', 'unknown')}",
        "total_step_entities": len(all_entities),
        "semantic_nodes": len(nodes),
        "semantic_relationships": len(edges),
        "entity_type_count": len(entity_counts),
        "key_ifc_entity_counts": dict(entity_counts.most_common(60)),
        "product_counts": dict(product_counts.most_common()),
        "typology_counts": dict(typology_counts),
        "relationship_counts": dict(relationship_counts),
        "storeys": [
            {"step_id": item["step_id"], "global_id": item.get("global_id"), "name": item["name"], "elevation": item.get("elevation")}
            for item in storeys
        ],
        "products_by_storey": {name: dict(counts.most_common()) for name, counts in by_storey.items()},
        "capabilities": _capability_summary(entity_counts, relationship_counts),
        "warnings": [],
    }
    return nodes, edges, summary


def extract_graph(ifc_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    try:
        nodes, edges, summary = extract_with_ifcopenshell(ifc_path)
    except (ImportError, ModuleNotFoundError) as exc:
        LOGGER.warning("IfcOpenShell unavailable; using STEP fallback: %s", exc)
        nodes, edges, summary = fallback_build_graph(ifc_path)
        summary["parser"] = "Built-in IFC STEP fallback"
        entity_counts = Counter(node.get("ifc_class", "") for node in nodes)
        relationship_counts = Counter(edge.get("relationship", "") for edge in edges)
        summary["capabilities"] = _capability_summary(entity_counts, relationship_counts)
        summary["warnings"] = [
            "IfcOpenShell was unavailable, so the built-in STEP parser was used.",
            "Fallback coordinates are placement translations only; rotations and geometry are not evaluated.",
        ]
        summary.pop("limitations", None)
    if not nodes:
        raise ValueError("No semantic IFC entities were extracted from the file.")
    return nodes, edges, summary


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_export(out_dir: Path, nodes: list[dict[str, Any]], edges: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "nodes.csv", nodes)
    write_csv(out_dir / "relationships.csv", edges)
    write_json(out_dir / "summary.json", summary)
