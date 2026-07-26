from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ENTITY_RE = re.compile(r"^#(?P<id>\d+)\s*=\s*(?P<type>[A-Z0-9_]+)\s*\((?P<args>.*)\)$", re.S)
REF_RE = re.compile(r"#(\d+)")

SPATIAL_TYPES = {"IFCPROJECT", "IFCSITE", "IFCBUILDING", "IFCBUILDINGSTOREY", "IFCSPACE"}
TYPE_SUFFIXES = ("TYPE",)
RELATION_TYPES = {
    "IFCRELAGGREGATES",
    "IFCRELCONTAINEDINSPATIALSTRUCTURE",
    "IFCRELVOIDSELEMENT",
    "IFCRELFILLSELEMENT",
    "IFCRELCONNECTSPATHELEMENTS",
    "IFCRELCONNECTSELEMENTS",
    "IFCRELCONNECTSPORTS",
    "IFCRELCONNECTSPORTTOELEMENT",
    "IFCRELASSIGNSTOGROUP",
    "IFCRELDEFINESBYTYPE",
    "IFCRELDEFINESBYPROPERTIES",
    "IFCRELASSOCIATESMATERIAL",
    "IFCRELSPACEBOUNDARY",
    "IFCRELSPACEBOUNDARY1STLEVEL",
    "IFCRELSPACEBOUNDARY2NDLEVEL",
}

@dataclass
class Entity:
    step_id: int
    ifc_type: str
    args_raw: str
    args: list[str]


def split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "'":
            if in_string and i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            in_string = not in_string
        elif not in_string:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append(text[start:i].strip())
                start = i + 1
        i += 1
    parts.append(text[start:].strip())
    return parts


def unquote(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if value in {"$", "*", ""}:
        return None
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return value


def refs(value: str | None) -> list[int]:
    if not value:
        return []
    return [int(x) for x in REF_RE.findall(value)]


def first_ref(value: str | None) -> int | None:
    r = refs(value)
    return r[0] if r else None


def parse_number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return None


def parse_ifc_value(value: str | None) -> Any:
    if value is None:
        return None
    v = value.strip()
    if v in {"$", "*"}:
        return None
    if v.startswith("'") and v.endswith("'"):
        return unquote(v)
    if v.startswith(".") and v.endswith("."):
        if v == ".T.":
            return True
        if v == ".F.":
            return False
        return v.strip(".")
    m = re.match(r"[A-Z0-9_]+\((.*)\)$", v, re.S)
    if m:
        return parse_ifc_value(m.group(1))
    try:
        return float(v) if any(c in v.upper() for c in (".", "E")) else int(v)
    except ValueError:
        return v


def read_step_entities(path: Path) -> tuple[dict[int, Entity], str | None]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    schema_match = re.search(r"FILE_SCHEMA\s*\(\('([^']+)'\)\)", text, re.I)
    schema = schema_match.group(1) if schema_match else None
    data_match = re.search(r"\bDATA\s*;(.*)ENDSEC\s*;", text, re.I | re.S)
    if not data_match:
        raise ValueError("No IFC DATA section found")
    data = data_match.group(1)

    entities: dict[int, Entity] = {}
    buf: list[str] = []
    in_string = False
    for ch in data:
        if ch == "'":
            in_string = not in_string
        if ch == ";" and not in_string:
            record = "".join(buf).strip()
            buf = []
            if not record.startswith("#"):
                continue
            m = ENTITY_RE.match(record)
            if not m:
                continue
            step_id = int(m.group("id"))
            ifc_type = m.group("type").upper()
            args_raw = m.group("args")
            entities[step_id] = Entity(step_id, ifc_type, args_raw, split_top_level(args_raw))
        else:
            buf.append(ch)
    return entities, schema


def root_identity(entity: Entity) -> tuple[str | None, str | None, str | None]:
    # IfcRoot GlobalIds use the 22-character compressed IFC GUID alphabet.
    first = unquote(entity.args[0]) if len(entity.args) > 0 else None
    guid = first if first and re.fullmatch(r"[0-9A-Za-z_$]{22}", first) else None
    if guid:
        name = unquote(entity.args[2]) if len(entity.args) > 2 else None
        description = unquote(entity.args[3]) if len(entity.args) > 3 else None
        return guid, name, description
    # Selected non-IfcRoot semantic resources use their first textual field as a name.
    if entity.ifc_type in {"IFCMATERIAL", "IFCMATERIALCONSTITUENT", "IFCMATERIALLAYER"}:
        return None, unquote(entity.args[0]) if entity.args else None, unquote(entity.args[1]) if len(entity.args) > 1 else None
    if entity.ifc_type in {"IFCMATERIALCONSTITUENTSET", "IFCMATERIALLAYERSET"}:
        return None, unquote(entity.args[1]) if len(entity.args) > 1 else None, unquote(entity.args[2]) if len(entity.args) > 2 else None
    return None, None, None


def typology(ifc_type: str) -> str:
    architectural = {
        "IFCWALL", "IFCWALLSTANDARDCASE", "IFCWINDOW", "IFCDOOR", "IFCSLAB",
        "IFCCOVERING", "IFCROOF", "IFCCURTAINWALL", "IFCSTAIR", "IFCRAILING",
        "IFCFURNISHINGELEMENT", "IFCBUILDINGELEMENTPROXY",
    }
    structural = {"IFCCOLUMN", "IFCBEAM", "IFCMEMBER", "IFCFOOTING", "IFCPILE", "IFCPLATE"}
    if ifc_type in architectural:
        return "Architectural"
    if ifc_type in structural or "STRUCTURAL" in ifc_type:
        return "Structural"
    if any(token in ifc_type for token in ("IFCPIPE", "IFCDUCT", "IFCFLOW", "IFCDISTRIBUTION", "IFCCABLE", "IFCPUMP", "IFCVALVE", "IFCBOILER", "IFCCHILLER")):
        return "MEP"
    if "SENSOR" in ifc_type or "CONTROLLER" in ifc_type or "ACTUATOR" in ifc_type:
        return "Sensor"
    if ifc_type == "IFCSPACE":
        return "Space"
    return "Other"


def is_semantic_node(entity: Entity) -> bool:
    t = entity.ifc_type
    if t in SPATIAL_TYPES:
        return True
    if t.startswith("IFCREL"):
        return False
    if t in {"IFCPROPERTYSET", "IFCELEMENTQUANTITY", "IFCMATERIAL", "IFCMATERIALLAYER", "IFCMATERIALLAYERSET", "IFCMATERIALCONSTITUENT", "IFCMATERIALCONSTITUENTSET", "IFCDISTRIBUTIONSYSTEM", "IFCSYSTEM", "IFCGROUP", "IFCZONE"}:
        return True
    if t.endswith(TYPE_SUFFIXES):
        return True
    # Product-like elements generally carry a GlobalId in arg 0 and placement in arg 5.
    if len(entity.args) >= 6 and unquote(entity.args[0]) and not t.startswith(("IFCGEOMETRIC", "IFCREPRESENTATION", "IFCPROFILE", "IFCSTYLE", "IFCCOLOUR")):
        return True
    return False


def placement_xyz(step_id: int | None, entities: dict[int, Entity], cache: dict[int, tuple[float, float, float]], stack: set[int] | None = None) -> tuple[float, float, float]:
    if step_id is None:
        return (0.0, 0.0, 0.0)
    if step_id in cache:
        return cache[step_id]
    stack = stack or set()
    if step_id in stack:
        return (0.0, 0.0, 0.0)
    stack.add(step_id)
    ent = entities.get(step_id)
    if not ent:
        return (0.0, 0.0, 0.0)

    if ent.ifc_type == "IFCLOCALPLACEMENT":
        parent = first_ref(ent.args[0]) if len(ent.args) > 0 else None
        relative = first_ref(ent.args[1]) if len(ent.args) > 1 else None
        px, py, pz = placement_xyz(parent, entities, cache, stack)
        rx, ry, rz = placement_xyz(relative, entities, cache, stack)
        result = (px + rx, py + ry, pz + rz)
    elif ent.ifc_type in {"IFCAXIS2PLACEMENT3D", "IFCAXIS2PLACEMENT2D"}:
        loc = first_ref(ent.args[0]) if ent.args else None
        result = placement_xyz(loc, entities, cache, stack)
    elif ent.ifc_type == "IFCCARTESIANPOINT":
        nums = [parse_number(x) for x in split_top_level(ent.args[0].strip()[1:-1])] if ent.args and ent.args[0].startswith("(") else []
        vals = [(n if n is not None else 0.0) for n in nums]
        result = tuple((vals + [0.0, 0.0, 0.0])[:3])  # type: ignore[assignment]
    else:
        result = (0.0, 0.0, 0.0)
    cache[step_id] = result
    stack.remove(step_id)
    return result


def relation_edges(entity: Entity) -> list[tuple[int, int, str, dict[str, Any]]]:
    a = entity.args
    t = entity.ifc_type
    out: list[tuple[int, int, str, dict[str, Any]]] = []
    meta = {"source_relation": t, "relation_step_id": entity.step_id}

    def add_many(srcs: Iterable[int], dsts: Iterable[int], rel: str, reverse: bool = False):
        for s in srcs:
            for d in dsts:
                out.append((d, s, rel, meta) if reverse else (s, d, rel, meta))

    if t == "IFCRELAGGREGATES" and len(a) >= 6:
        parent = refs(a[4]); children = refs(a[5]); add_many(children, parent, "BELONGS_TO_SPATIAL")
    elif t == "IFCRELCONTAINEDINSPATIALSTRUCTURE" and len(a) >= 6:
        elements = refs(a[4]); container = refs(a[5]); add_many(elements, container, "CONTAINED_IN")
    elif t == "IFCRELVOIDSELEMENT" and len(a) >= 6:
        add_many(refs(a[4]), refs(a[5]), "HAS_OPENING")
    elif t == "IFCRELFILLSELEMENT" and len(a) >= 6:
        add_many(refs(a[5]), refs(a[4]), "FILLS_OPENING")
    elif t in {"IFCRELCONNECTSPATHELEMENTS", "IFCRELCONNECTSELEMENTS"} and len(a) >= 7:
        add_many(refs(a[5]), refs(a[6]), "CONNECTED_TO")
    elif t == "IFCRELCONNECTSPORTS" and len(a) >= 6:
        add_many(refs(a[4]), refs(a[5]), "PORT_CONNECTED_TO")
    elif t == "IFCRELCONNECTSPORTTOELEMENT" and len(a) >= 6:
        add_many(refs(a[4]), refs(a[5]), "PORT_OF")
    elif t == "IFCRELASSIGNSTOGROUP" and len(a) >= 6:
        add_many(refs(a[4]), refs(a[5]), "ASSIGNED_TO_GROUP")
    elif t == "IFCRELDEFINESBYTYPE" and len(a) >= 6:
        add_many(refs(a[4]), refs(a[5]), "IS_TYPED_BY")
    elif t == "IFCRELDEFINESBYPROPERTIES" and len(a) >= 6:
        add_many(refs(a[4]), refs(a[5]), "HAS_PROPERTY_SET")
    elif t == "IFCRELASSOCIATESMATERIAL" and len(a) >= 6:
        add_many(refs(a[4]), refs(a[5]), "HAS_MATERIAL")
    elif t.startswith("IFCRELSPACEBOUNDARY") and len(a) >= 6:
        space = refs(a[4]); element = refs(a[5]); add_many(space, element, "BOUNDED_BY")
    return out


def build_graph(ifc_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    entities, schema = read_step_entities(ifc_path)
    entity_counts = Counter(e.ifc_type for e in entities.values())
    placement_cache: dict[int, tuple[float, float, float]] = {}

    prop_values: dict[int, dict[str, Any]] = defaultdict(dict)
    for e in entities.values():
        if e.ifc_type == "IFCPROPERTYSINGLEVALUE" and len(e.args) >= 3:
            prop_values[e.step_id] = {"name": unquote(e.args[0]), "value": parse_ifc_value(e.args[2])}

    pset_props: dict[int, dict[str, Any]] = defaultdict(dict)
    for e in entities.values():
        if e.ifc_type == "IFCPROPERTYSET" and len(e.args) >= 5:
            for pid in refs(e.args[4]):
                item = prop_values.get(pid)
                if item and item.get("name"):
                    pset_props[e.step_id][str(item["name"])] = item.get("value")

    semantic_ids = {eid for eid, e in entities.items() if is_semantic_node(e)}
    nodes: list[dict[str, Any]] = []
    node_by_id: dict[int, dict[str, Any]] = {}
    for eid in sorted(semantic_ids):
        e = entities[eid]
        guid, name, description = root_identity(e)
        label = "SpatialElement" if e.ifc_type in SPATIAL_TYPES else ("Type" if e.ifc_type.endswith(TYPE_SUFFIXES) else "Entity")
        if e.ifc_type == "IFCPROPERTYSET":
            label = "PropertySet"
        elif e.ifc_type.startswith("IFCMATERIAL"):
            label = "Material"
        elif e.ifc_type in {"IFCSYSTEM", "IFCDISTRIBUTIONSYSTEM", "IFCGROUP", "IFCZONE"}:
            label = "System"
        elif e.ifc_type not in SPATIAL_TYPES and not e.ifc_type.endswith(TYPE_SUFFIXES):
            label = "Product"

        object_placement_ref = first_ref(e.args[5]) if len(e.args) > 5 else None
        x, y, z = placement_xyz(object_placement_ref, entities, placement_cache)
        row: dict[str, Any] = {
            "step_id": eid,
            "node_id": f"#{eid}",
            "global_id": guid,
            "name": name or f"Unnamed {e.ifc_type}",
            "description": description,
            "ifc_class": e.ifc_type,
            "label": label,
            "typology": typology(e.ifc_type),
            "x": round(x, 6),
            "y": round(y, 6),
            "z": round(z, 6),
        }
        if e.ifc_type == "IFCBUILDINGSTOREY" and len(e.args) >= 10:
            row["elevation"] = parse_number(e.args[9])
        if e.ifc_type == "IFCPROPERTYSET":
            row["properties_json"] = json.dumps(pset_props.get(eid, {}), ensure_ascii=False)
        nodes.append(row)
        node_by_id[eid] = row

    edges: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for e in entities.values():
        if e.ifc_type not in RELATION_TYPES and not e.ifc_type.startswith("IFCRELSPACEBOUNDARY"):
            continue
        for source, target, rel_type, meta in relation_edges(e):
            if source not in semantic_ids or target not in semantic_ids:
                continue
            key = (source, target, rel_type)
            if key in seen:
                continue
            seen.add(key)
            edges.append({
                "source_step_id": source,
                "source_node_id": f"#{source}",
                "target_step_id": target,
                "target_node_id": f"#{target}",
                "relationship": rel_type,
                **meta,
            })

    # Attach direct storey and building path for convenient API/UI queries.
    parents: dict[int, list[int]] = defaultdict(list)
    for edge in edges:
        if edge["relationship"] in {"CONTAINED_IN", "BELONGS_TO_SPATIAL"}:
            parents[edge["source_step_id"]].append(edge["target_step_id"])

    def find_ancestor(start: int, wanted: str) -> int | None:
        queue = list(parents.get(start, [])); visited = set()
        while queue:
            cur = queue.pop(0)
            if cur in visited:
                continue
            visited.add(cur)
            ent = entities.get(cur)
            if ent and ent.ifc_type == wanted:
                return cur
            queue.extend(parents.get(cur, []))
        return None

    for n in nodes:
        sid = n["step_id"]
        storey_id = find_ancestor(sid, "IFCBUILDINGSTOREY")
        building_id = find_ancestor(sid, "IFCBUILDING")
        if storey_id and storey_id in node_by_id:
            n["storey_step_id"] = storey_id
            n["storey_name"] = node_by_id[storey_id]["name"]
        if building_id and building_id in node_by_id:
            n["building_step_id"] = building_id
            n["building_name"] = node_by_id[building_id]["name"]

    product_counts = Counter(n["ifc_class"] for n in nodes if n["label"] == "Product")
    typology_counts = Counter(n["typology"] for n in nodes if n["label"] == "Product")
    relationship_counts = Counter(e["relationship"] for e in edges)
    storeys = [n for n in nodes if n["ifc_class"] == "IFCBUILDINGSTOREY"]
    storeys.sort(key=lambda x: (x.get("elevation") is None, x.get("elevation") or 0))

    by_storey: dict[str, Counter[str]] = defaultdict(Counter)
    for n in nodes:
        if n.get("storey_name") and n["label"] == "Product":
            by_storey[n["storey_name"]][n["ifc_class"]] += 1

    summary = {
        "source_file": ifc_path.name,
        "schema": schema,
        "total_step_entities": len(entities),
        "semantic_nodes": len(nodes),
        "semantic_relationships": len(edges),
        "entity_type_count": len(entity_counts),
        "key_ifc_entity_counts": dict(entity_counts.most_common(40)),
        "product_counts": dict(product_counts.most_common()),
        "typology_counts": dict(typology_counts),
        "relationship_counts": dict(relationship_counts),
        "storeys": [{"step_id": s["step_id"], "global_id": s["global_id"], "name": s["name"], "elevation": s.get("elevation")} for s in storeys],
        "products_by_storey": {k: dict(v.most_common()) for k, v in by_storey.items()},
        "capability_evidence": {
            "sensor_entities": sum(v for k, v in entity_counts.items() if "SENSOR" in k),
            "mep_entities": sum(v for k, v in entity_counts.items() if any(k.startswith(x) for x in ("IFCPIPE", "IFCDUCT", "IFCFLOW", "IFCDISTRIBUTION", "IFCPUMP", "IFCVALVE", "IFCCABLE"))),
            "structural_members": sum(entity_counts.get(k, 0) for k in ("IFCCOLUMN", "IFCBEAM", "IFCMEMBER", "IFCFOOTING", "IFCPILE", "IFCPLATE")),
            "spaces": entity_counts.get("IFCSPACE", 0),
            "doors": entity_counts.get("IFCDOOR", 0),
            "windows": entity_counts.get("IFCWINDOW", 0),
        },
        "limitations": [
            "No sensor entities were found in this IFC; sensor-to-room navigation needs external sensor records or IFC sensor objects.",
            "No pipe/duct/flow/distribution entities were found; leak tracing cannot be demonstrated from this file.",
            "The IFC contains structural members, but it does not encode a verified engineering load-path graph. Structural impact results must remain heuristic until structural-analysis connectivity is supplied.",
            "Coordinates are placement translations only in the fallback parser; rotations and full geometry are intentionally not evaluated.",
        ],
    }
    return nodes, edges, summary


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)


def write_cypher(path: Path) -> None:
    text = """// Run from Neo4j Browser after placing nodes.csv and relationships.csv in Neo4j's import directory.\n\nCREATE CONSTRAINT ifc_step_id IF NOT EXISTS FOR (n:IFCEntity) REQUIRE n.step_id IS UNIQUE;\nCREATE INDEX ifc_global_id IF NOT EXISTS FOR (n:IFCEntity) ON (n.global_id);\n\nLOAD CSV WITH HEADERS FROM 'file:///nodes.csv' AS row\nMERGE (n:IFCEntity {step_id: toInteger(row.step_id)})\nSET n.node_id = row.node_id,\n    n.global_id = CASE WHEN row.global_id = '' THEN null ELSE row.global_id END,\n    n.name = row.name,\n    n.description = row.description,\n    n.ifc_class = row.ifc_class,\n    n.semantic_label = row.label,\n    n.typology = row.typology,\n    n.x = toFloat(row.x), n.y = toFloat(row.y), n.z = toFloat(row.z),\n    n.elevation = CASE WHEN row.elevation = '' THEN null ELSE toFloat(row.elevation) END,\n    n.storey_step_id = CASE WHEN row.storey_step_id = '' THEN null ELSE toInteger(row.storey_step_id) END,\n    n.storey_name = row.storey_name,\n    n.building_name = row.building_name,\n    n.properties_json = row.properties_json;\n\nLOAD CSV WITH HEADERS FROM 'file:///relationships.csv' AS row\nMATCH (a:IFCEntity {step_id: toInteger(row.source_step_id)})\nMATCH (b:IFCEntity {step_id: toInteger(row.target_step_id)})\nCALL apoc.merge.relationship(a, row.relationship, {relation_step_id: toInteger(row.relation_step_id)}, {source_relation: row.source_relation}, b, {}) YIELD rel\nRETURN count(rel);\n"""
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a semantic graph export from IFC without geometry primitives.")
    parser.add_argument("ifc", type=Path)
    parser.add_argument("--out", type=Path, default=Path("output"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    nodes, edges, summary = build_graph(args.ifc)
    write_csv(args.out / "nodes.csv", nodes)
    write_csv(args.out / "relationships.csv", edges)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    write_cypher(args.out / "neo4j_import.cypher")
    print(json.dumps({"nodes": len(nodes), "relationships": len(edges), "summary": str(args.out / 'summary.json')}, indent=2))


if __name__ == "__main__":
    main()
