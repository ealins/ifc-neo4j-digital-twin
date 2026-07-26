from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .neo4j_store import Neo4jStore
from .service import ModelService
from .settings import settings
from .utils import safe_filename

store: Neo4jStore | None = None
service: ModelService | None = None

NODE_FIELDS = (
    "model_id", "step_id", "global_id", "name", "description", "ifc_class",
    "label", "typology", "x", "y", "z", "elevation", "storey_step_id",
    "storey_name", "building_step_id", "building_name", "properties_json",
)

SPATIAL_RELATIONS = "BELONGS_TO_SPATIAL|NESTED_IN|CONTAINED_IN|REFERENCED_IN"
MEP_RELATIONS = "PORT_CONNECTED_TO|PORT_OF|CONNECTED_TO|CONTROLS_FLOW|ASSIGNED_TO_GROUP|SERVICES"
STRUCTURAL_RELATIONS = "STRUCTURAL_CONNECTION|CONNECTED_TO|REALIZED_BY"


class SensorCreate(BaseModel):
    sensor_id: str = Field(min_length=1, max_length=200)
    sensor_type: str = Field(min_length=1, max_length=100)
    name: str | None = Field(default=None, max_length=300)
    attach_to: str = Field(description="IFC GlobalId or STEP ID of the attached element/space")
    metadata: dict[str, Any] = Field(default_factory=dict)


class SensorReadingCreate(BaseModel):
    value: float | str | bool
    unit: str | None = Field(default=None, max_length=50)
    timestamp: datetime | None = None
    status: str | None = Field(default=None, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


def get_store() -> Neo4jStore:
    if store is None:
        raise HTTPException(status_code=503, detail="Neo4j is not connected")
    return store


def get_service() -> ModelService:
    if service is None:
        raise HTTPException(status_code=503, detail="Model service is unavailable")
    return service


def ensure_model(model_id: str) -> None:
    if not get_store().model_exists(model_id):
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' was not found")


def _properties(value: Any) -> dict[str, Any]:
    result = dict(value)
    raw = result.get("properties_json")
    if raw:
        try:
            result["properties"] = json.loads(raw)
        except Exception:
            result["properties"] = {}
    return {key: result.get(key) for key in NODE_FIELDS if key in result} | ({"properties": result["properties"]} if "properties" in result else {})


def graph_for_ids(model_id: str, ids: list[int], edge_limit: int = 2500) -> dict[str, Any]:
    unique_ids = sorted(set(int(value) for value in ids))
    if not unique_ids:
        return {"nodes": [], "edges": []}
    db = get_store()
    node_rows = db.execute(
        """
        MATCH (n:IFCEntity {model_id:$model_id})
        WHERE n.step_id IN $ids
        RETURN properties(n) AS node
        """,
        model_id=model_id,
        ids=unique_ids,
    )
    edge_rows = db.execute(
        """
        MATCH (a:IFCEntity {model_id:$model_id})-[r]->(b:IFCEntity {model_id:$model_id})
        WHERE a.step_id IN $ids AND b.step_id IN $ids
        RETURN a.step_id AS source, b.step_id AS target, type(r) AS relationship,
               r.source_relation AS source_relation, r.relation_step_id AS relation_step_id
        LIMIT $limit
        """,
        model_id=model_id,
        ids=unique_ids,
        limit=edge_limit,
    )
    return {
        "nodes": [_properties(row["node"]) for row in node_rows],
        "edges": [
            {
                "source": int(row["source"]),
                "target": int(row["target"]),
                "relationship": row["relationship"],
                "source_relation": row.get("source_relation"),
                "relation_step_id": int(row.get("relation_step_id") or 0),
            }
            for row in edge_rows
        ],
    }


def payload(model_id: str, ids: list[int], title: str, cypher: str, mode: str, **extra: Any) -> dict[str, Any]:
    graph = graph_for_ids(model_id, ids)
    return {
        **graph,
        "model_id": model_id,
        "title": title,
        "mode": mode,
        "backend": "neo4j",
        "cypher": cypher,
        "node_count": len(graph["nodes"]),
        "edge_count": len(graph["edges"]),
        **extra,
    }


def resolve_entity(model_id: str, identifier: str) -> dict[str, Any]:
    cleaned = identifier.lstrip("#")
    rows = get_store().execute(
        """
        MATCH (n:IFCEntity {model_id:$model_id})
        WHERE n.global_id = $identifier OR toString(n.step_id) = $cleaned
        RETURN properties(n) AS node LIMIT 1
        """,
        model_id=model_id,
        identifier=identifier,
        cleaned=cleaned,
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Entity '{identifier}' was not found in model '{model_id}'")
    return _properties(rows[0]["node"])


@asynccontextmanager
async def lifespan(_: FastAPI):
    global store, service
    settings.ensure_dirs()
    last_error: Exception | None = None
    for attempt in range(30):
        try:
            candidate = Neo4jStore(
                settings.neo4j_uri,
                settings.neo4j_user,
                settings.neo4j_password,
                settings.neo4j_database,
            )
            candidate.verify()
            candidate.create_schema()
            store = candidate
            service = ModelService(settings, candidate)
            break
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(min(1 + attempt * 0.25, 5))
    if store is None:
        raise RuntimeError(f"Could not connect to Neo4j: {last_error}")
    yield
    store.close()


app = FastAPI(
    title="Generic IFC Neo4j Digital Twin API",
    version="1.0.0",
    description=(
        "Upload IFC/IFCZIP models, extract semantic relationships, import them into Neo4j, "
        "query spatial/MEP/structural connectivity, and attach operational sensors."
    ),
    lifespan=lifespan,
)

VIEWER_DIR = Path(os.getenv("VIEWER_DIR", Path(__file__).resolve().parents[2] / "viewer"))
if VIEWER_DIR.exists():
    app.mount("/viewer-assets", StaticFiles(directory=VIEWER_DIR), name="viewer-assets")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/viewer")


@app.get("/viewer", include_in_schema=False)
def viewer() -> FileResponse:
    index = VIEWER_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Viewer files are missing")
    return FileResponse(index)


@app.get("/api/status")
def status() -> dict[str, Any]:
    db = get_store()
    row = db.execute(
        "MATCH (m:IFCModel) WITH count(m) AS models MATCH (n:IFCEntity) WITH models,count(n) AS nodes OPTIONAL MATCH ()-[r]->() RETURN models,nodes,count(r) AS relationships"
    )[0]
    return {
        "backend": "neo4j",
        "neo4j_connected": True,
        "models": int(row["models"]),
        "nodes": int(row["nodes"]),
        "relationships": int(row["relationships"]),
        "max_upload_mb": settings.max_upload_mb,
    }


@app.get("/api/models")
def list_models() -> list[dict[str, Any]]:
    rows = get_store().execute(
        """
        MATCH (m:IFCModel)
        RETURN m.model_id AS model_id, m.source_file AS source_file, m.schema AS schema,
               m.parser AS parser, m.sha256 AS sha256, m.semantic_nodes AS semantic_nodes,
               m.semantic_relationships AS semantic_relationships, m.imported_at AS imported_at,
               m.summary_json AS summary_json
        ORDER BY m.imported_at DESC
        """
    )
    results = []
    for row in rows:
        summary = json.loads(row.pop("summary_json") or "{}")
        results.append({**row, "capabilities": summary.get("capabilities", {}), "storeys": summary.get("storeys", [])})
    return results


@app.post("/api/models/import", status_code=201)
async def import_model(
    file: UploadFile = File(...),
    replace_same_file: bool = Query(True, description="Replace the graph if the same content-derived model_id already exists"),
) -> dict[str, Any]:
    filename = safe_filename(file.filename or "model.ifc")
    if Path(filename).suffix.lower() not in {".ifc", ".ifczip"}:
        raise HTTPException(status_code=415, detail="Upload a .ifc or .ifczip file")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    target = settings.inbox_dir / filename
    size = 0
    try:
        with target.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_mb} MB limit")
                output.write(chunk)
        result = await get_service().import_file(target, filename, replace=replace_same_file)
        return result
    finally:
        await file.close()
        target.unlink(missing_ok=True)


@app.get("/api/models/{model_id}")
def get_model(model_id: str) -> dict[str, Any]:
    ensure_model(model_id)
    rows = get_store().execute("MATCH (m:IFCModel {model_id:$model_id}) RETURN properties(m) AS model", model_id=model_id)
    model = dict(rows[0]["model"])
    model["summary"] = json.loads(model.pop("summary_json", "{}"))
    return model


@app.delete("/api/models/{model_id}", status_code=204)
def delete_model(model_id: str) -> None:
    ensure_model(model_id)
    get_service().delete_model(model_id)


@app.get("/api/models/{model_id}/capabilities")
def capabilities(model_id: str) -> dict[str, Any]:
    model = get_model(model_id)
    return {
        "model_id": model_id,
        "capabilities": model["summary"].get("capabilities", {}),
        "warnings": model["summary"].get("warnings", []),
    }


@app.get("/api/models/{model_id}/storeys")
def storeys(model_id: str) -> list[dict[str, Any]]:
    ensure_model(model_id)
    rows = get_store().execute(
        """
        MATCH (s:IFCEntity {model_id:$model_id})
        WHERE s.ifc_class IN ['IFCBUILDINGSTOREY','IFCFACILITYPART']
        OPTIONAL MATCH (e:IFCEntity {model_id:$model_id})-[:CONTAINED_IN|BELONGS_TO_SPATIAL|NESTED_IN|REFERENCED_IN]->(s)
        RETURN s.step_id AS step_id, s.global_id AS global_id, s.name AS name,
               s.elevation AS elevation, count(e) AS direct_elements
        ORDER BY s.elevation, s.name
        """,
        model_id=model_id,
    )
    return rows


@app.get("/api/models/{model_id}/graph/overview")
def graph_overview(model_id: str) -> dict[str, Any]:
    ensure_model(model_id)
    cypher = "MATCH (n:IFCEntity {model_id:$model_id}) WHERE n.label='SpatialElement' RETURN n"
    rows = get_store().execute(
        """
        MATCH (n:IFCEntity {model_id:$model_id})
        WHERE n.label = 'SpatialElement' OR n.ifc_class IN ['IFCPROJECT','IFCSITE','IFCBUILDING','IFCBUILDINGSTOREY','IFCSPACE','IFCFACILITY','IFCFACILITYPART']
        RETURN n.step_id AS id LIMIT 800
        """,
        model_id=model_id,
    )
    ids = [int(row["id"]) for row in rows]
    return payload(model_id, ids, "Spatial hierarchy", cypher, "overview")


@app.get("/api/models/{model_id}/graph/storey/{storey_id}")
def graph_storey(
    model_id: str,
    storey_id: int,
    ifc_class: str | None = None,
    limit: int = Query(300, ge=1, le=1500),
) -> dict[str, Any]:
    ensure_model(model_id)
    cls = ifc_class.upper() if ifc_class else None
    rows = get_store().execute(
        """
        MATCH (storey:IFCEntity {model_id:$model_id, step_id:$storey_id})
        OPTIONAL MATCH (element:IFCEntity {model_id:$model_id})-[:CONTAINED_IN|BELONGS_TO_SPATIAL|NESTED_IN|REFERENCED_IN]->(storey)
        WHERE $ifc_class IS NULL OR element.ifc_class = $ifc_class
        RETURN storey.name AS storey_name, collect(element.step_id)[0..$limit] AS element_ids
        """,
        model_id=model_id,
        storey_id=storey_id,
        ifc_class=cls,
        limit=limit,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Storey not found")
    ids = [storey_id] + [int(value) for value in rows[0]["element_ids"] if value is not None]
    return payload(
        model_id,
        ids,
        f"{rows[0]['storey_name']} · {cls or 'all elements'}",
        "MATCH (element)-[:CONTAINED_IN|BELONGS_TO_SPATIAL|NESTED_IN|REFERENCED_IN]->(storey)",
        "storey",
        focus_id=storey_id,
    )


@app.get("/api/models/{model_id}/graph/search")
def graph_search(
    model_id: str,
    q: str = Query(min_length=1),
    ifc_class: str | None = None,
    limit: int = Query(50, ge=1, le=300),
) -> dict[str, Any]:
    ensure_model(model_id)
    cls = ifc_class.upper() if ifc_class else None
    rows = get_store().execute(
        """
        MATCH (n:IFCEntity {model_id:$model_id})
        WHERE ($ifc_class IS NULL OR n.ifc_class = $ifc_class)
          AND (toLower(coalesce(n.name,'')) CONTAINS toLower($q)
               OR toLower(coalesce(n.description,'')) CONTAINS toLower($q)
               OR n.global_id = $q OR toString(n.step_id) = $cleaned
               OR toLower(n.ifc_class) CONTAINS toLower($q))
        RETURN n.step_id AS id LIMIT $limit
        """,
        model_id=model_id,
        ifc_class=cls,
        q=q,
        cleaned=q.lstrip("#"),
        limit=limit,
    )
    ids = [int(row["id"]) for row in rows]
    return payload(model_id, ids, f"Search: {q}", "MATCH (n) WHERE ... RETURN n", "search")


@app.get("/api/models/{model_id}/graph/entity/{identifier}")
def graph_entity(
    model_id: str,
    identifier: str,
    depth: int = Query(1, ge=1, le=3),
    limit: int = Query(500, ge=1, le=2000),
) -> dict[str, Any]:
    entity = resolve_entity(model_id, identifier)
    query = f"""
        MATCH p=(start:IFCEntity {{model_id:$model_id, step_id:$step_id}})-[*0..{depth}]-(neighbor:IFCEntity {{model_id:$model_id}})
        WITH DISTINCT neighbor LIMIT $limit
        RETURN neighbor.step_id AS id
    """
    rows = get_store().execute(query, model_id=model_id, step_id=entity["step_id"], limit=limit)
    ids = [int(row["id"]) for row in rows]
    return payload(
        model_id,
        ids,
        f"Neighborhood of {entity.get('name') or identifier}",
        query.strip(),
        "entity",
        focus_id=entity["step_id"],
    )


@app.get("/api/models/{model_id}/entities/{identifier}")
def entity_details(model_id: str, identifier: str) -> dict[str, Any]:
    entity = resolve_entity(model_id, identifier)
    rows = get_store().execute(
        """
        MATCH (n:IFCEntity {model_id:$model_id, step_id:$step_id})
        OPTIONAL MATCH (n)-[out]->(out_node:IFCEntity {model_id:$model_id})
        WITH n, collect({relationship:type(out), entity:properties(out_node)}) AS outgoing
        OPTIONAL MATCH (in_node:IFCEntity {model_id:$model_id})-[inc]->(n)
        RETURN properties(n) AS entity, outgoing,
               collect({relationship:type(inc), entity:properties(in_node)}) AS incoming
        """,
        model_id=model_id,
        step_id=entity["step_id"],
    )[0]
    return {
        "entity": _properties(rows["entity"]),
        "outgoing": [{"relationship": item["relationship"], "entity": _properties(item["entity"])} for item in rows["outgoing"] if item["entity"]],
        "incoming": [{"relationship": item["relationship"], "entity": _properties(item["entity"])} for item in rows["incoming"] if item["entity"]],
    }


@app.get("/api/models/{model_id}/graph/spatial-path/{identifier}")
def spatial_path(model_id: str, identifier: str) -> dict[str, Any]:
    entity = resolve_entity(model_id, identifier)
    query = f"""
        MATCH p=(start:IFCEntity {{model_id:$model_id, step_id:$step_id}})-[:{SPATIAL_RELATIONS}*0..10]->(ancestor:IFCEntity {{model_id:$model_id}})
        RETURN [node IN nodes(p) | node.step_id] AS ids
        ORDER BY length(p) DESC LIMIT 1
    """
    rows = get_store().execute(query, model_id=model_id, step_id=entity["step_id"])
    ids = [int(value) for value in (rows[0]["ids"] if rows else [entity["step_id"]])]
    return payload(
        model_id,
        ids,
        f"Spatial path: {entity.get('name') or identifier}",
        query.strip(),
        "spatial-path",
        focus_id=entity["step_id"],
        path_ids=ids,
    )


@app.get("/api/models/{model_id}/trace/mep/{identifier}")
def trace_mep(model_id: str, identifier: str, max_hops: int = Query(10, ge=1, le=25)) -> dict[str, Any]:
    entity = resolve_entity(model_id, identifier)
    caps = capabilities(model_id)["capabilities"].get("mep_trace", {})
    if not caps.get("available"):
        raise HTTPException(status_code=422, detail={"message": "This IFC does not contain a connected MEP graph.", "evidence": caps.get("evidence", {})})
    query = f"""
        MATCH p=(start:IFCEntity {{model_id:$model_id, step_id:$step_id}})-[:{MEP_RELATIONS}*0..{max_hops}]-(n:IFCEntity {{model_id:$model_id}})
        WHERE n.typology='MEP' OR n.label='System'
        UNWIND nodes(p) AS node
        RETURN DISTINCT node.step_id AS id LIMIT 1500
    """
    rows = get_store().execute(query, model_id=model_id, step_id=entity["step_id"])
    ids = [int(row["id"]) for row in rows]
    return payload(model_id, ids, f"MEP trace: {entity.get('name')}", query.strip(), "mep-trace", focus_id=entity["step_id"])


@app.get("/api/models/{model_id}/impact/structural/{identifier}")
def structural_impact(model_id: str, identifier: str, max_hops: int = Query(3, ge=1, le=8)) -> dict[str, Any]:
    entity = resolve_entity(model_id, identifier)
    if entity.get("typology") != "Structural":
        raise HTTPException(status_code=422, detail="The selected IFC entity is not classified as structural")
    query = f"""
        MATCH p=(start:IFCEntity {{model_id:$model_id, step_id:$step_id}})-[:{STRUCTURAL_RELATIONS}*0..{max_hops}]-(n:IFCEntity {{model_id:$model_id}})
        WHERE n.typology='Structural'
        UNWIND nodes(p) AS node
        RETURN DISTINCT node.step_id AS id LIMIT 1000
    """
    rows = get_store().execute(query, model_id=model_id, step_id=entity["step_id"])
    ids = [int(row["id"]) for row in rows]
    result = payload(model_id, ids, f"Structural connectivity around {entity.get('name')}", query.strip(), "structural", focus_id=entity["step_id"])
    result["warning"] = "Connectivity-only result. This is not an engineering load-path or collapse calculation."
    return result


@app.post("/api/models/{model_id}/sensors", status_code=201)
def register_sensor(model_id: str, sensor: SensorCreate) -> dict[str, Any]:
    target = resolve_entity(model_id, sensor.attach_to)
    rows = get_store().execute(
        """
        MATCH (target:IFCEntity {model_id:$model_id, step_id:$step_id})
        MERGE (sensor:Sensor {model_id:$model_id, sensor_id:$sensor_id})
        SET sensor.sensor_type=$sensor_type, sensor.name=$name,
            sensor.metadata_json=$metadata_json, sensor.updated_at=$now
        MERGE (sensor)-[:ATTACHED_TO]->(target)
        RETURN properties(sensor) AS sensor, properties(target) AS target
        """,
        model_id=model_id,
        step_id=target["step_id"],
        sensor_id=sensor.sensor_id,
        sensor_type=sensor.sensor_type,
        name=sensor.name or sensor.sensor_id,
        metadata_json=json.dumps(sensor.metadata, ensure_ascii=False),
        now=datetime.now(timezone.utc).isoformat(),
    )
    return {"sensor": dict(rows[0]["sensor"]), "attached_to": _properties(rows[0]["target"])}


@app.post("/api/models/{model_id}/sensors/{sensor_id}/readings", status_code=201)
def add_sensor_reading(model_id: str, sensor_id: str, reading: SensorReadingCreate) -> dict[str, Any]:
    timestamp = (reading.timestamp or datetime.now(timezone.utc)).isoformat()
    rows = get_store().execute(
        """
        MATCH (sensor:Sensor {model_id:$model_id, sensor_id:$sensor_id})
        CREATE (reading:SensorReading {
          reading_id: randomUUID(), model_id:$model_id, value:$value, unit:$unit,
          timestamp:$timestamp, status:$status, metadata_json:$metadata_json
        })
        MERGE (sensor)-[:HAS_READING]->(reading)
        SET sensor.last_value=$value, sensor.last_unit=$unit,
            sensor.last_timestamp=$timestamp, sensor.last_status=$status
        RETURN properties(reading) AS reading
        """,
        model_id=model_id,
        sensor_id=sensor_id,
        value=reading.value,
        unit=reading.unit,
        timestamp=timestamp,
        status=reading.status,
        metadata_json=json.dumps(reading.metadata, ensure_ascii=False),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Sensor not found")
    return dict(rows[0]["reading"])


@app.get("/api/models/{model_id}/sensors/{sensor_id}/location")
def sensor_location(model_id: str, sensor_id: str) -> dict[str, Any]:
    query = f"""
        MATCH (sensor:Sensor {{model_id:$model_id, sensor_id:$sensor_id}})-[:ATTACHED_TO]->(target:IFCEntity {{model_id:$model_id}})
        OPTIONAL MATCH p=(target)-[:{SPATIAL_RELATIONS}*0..10]->(ancestor:IFCEntity {{model_id:$model_id}})
        WITH sensor,target,p ORDER BY length(p) DESC LIMIT 1
        RETURN properties(sensor) AS sensor, target.step_id AS target_id,
               CASE WHEN p IS NULL THEN [target.step_id] ELSE [n IN nodes(p) | n.step_id] END AS path_ids
    """
    rows = get_store().execute(query, model_id=model_id, sensor_id=sensor_id)
    if not rows:
        raise HTTPException(status_code=404, detail="Sensor not found")
    ids = [int(value) for value in rows[0]["path_ids"]]
    result = payload(model_id, ids, f"Sensor location: {sensor_id}", query.strip(), "sensor-location", focus_id=rows[0]["target_id"], path_ids=ids)
    result["sensor"] = dict(rows[0]["sensor"])
    return result
