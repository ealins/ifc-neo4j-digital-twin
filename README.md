# Generic IFC → Neo4j Digital Twin Brain

A reusable, multi-model system that turns raw `.ifc` or `.ifczip` files into a semantic Neo4j graph, exposes the graph through FastAPI, and provides an interactive browser viewer.

The repository is intentionally **evidence-driven**: it enables spatial navigation, MEP tracing, sensor localization, and structural-connectivity views only when the IFC or external mappings provide the relationships needed for those functions. It does not invent pipe networks or engineering load paths.

## What is automated

1. Upload an IFC/IFCZIP in the browser.
2. Validate the extension, size, archive safety, and IFC content.
3. Parse with IfcOpenShell when available; fall back to the built-in STEP parser if necessary.
4. Create a content-derived `model_id` from the filename and SHA-256 hash.
5. Extract semantic nodes, relationships, property sets, materials, types, systems, ports, and spatial placement.
6. Calculate model capability evidence.
7. Import the graph into Neo4j in batches.
8. Store each model separately—uploading a new model does not delete the previous one.
9. Serve the viewer and OpenAPI interface through FastAPI.

## Architecture

```text
IFC / IFCZIP upload
        │
        ▼
IfcOpenShell parser ── fallback STEP parser
        │
        ▼
semantic graph + capability report
        │
        ▼
Neo4j multi-model database  ← external sensors/readings
        │
        ▼
FastAPI REST bridge
        │
        ├── Web graph viewer
        ├── Cesium / IFC.js client
        └── Other operational applications
```

## Start on Windows

Requirements:

- Docker Desktop with Docker Compose
- At least about 4 GB free RAM for Docker; increase this for very large models

Run:

```text
scripts\start.bat
```

The script works from its own folder, so it does not matter whether PowerShell is currently in `C:\Windows\System32`.

Open:

- Viewer: `http://localhost:8000/viewer`
- FastAPI/OpenAPI: `http://localhost:8000/docs`
- Neo4j Browser: `http://localhost:7474`

Default local login:

```text
Username: neo4j
Password: IfcBrain2026!
```

Copy `.env.example` to `.env` and change the password before exposing the service beyond your own machine.

### Password changed after first start

Neo4j stores authentication in its persistent data volume. Changing `.env` does not overwrite the password in an existing volume. Run:

```text
scripts\reset-brain.bat
```

This removes only the volumes owned by the fixed Compose project `ifc-brain-generic`, then starts a clean system.

## Use the viewer

1. Choose an `.ifc` or `.ifczip` file.
2. Click **Upload and build brain**.
3. Select the model from **Active model**.
4. Explore the hierarchy, floors/facility parts, names, GUIDs, IFC classes, and STEP IDs.
5. Select a node to expand its neighborhood or show its spatial path.

No source filename, floor name, GUID, or IFC entity example is hard-coded in the UI.

## FastAPI examples

### Import a model

```bash
curl -X POST "http://localhost:8000/api/models/import" \
  -F "file=@building.ifc"
```

### List models

```bash
curl "http://localhost:8000/api/models"
```

### Get model capabilities

```bash
curl "http://localhost:8000/api/models/MODEL_ID/capabilities"
```

### Search entities

```bash
curl "http://localhost:8000/api/models/MODEL_ID/graph/search?q=Room%20101"
```

### Get an element's spatial path

```bash
curl "http://localhost:8000/api/models/MODEL_ID/graph/spatial-path/IFC_GLOBAL_ID"
```

### Trace a connected MEP network

```bash
curl "http://localhost:8000/api/models/MODEL_ID/trace/mep/IFC_GLOBAL_ID"
```

The endpoint returns HTTP 422 with evidence when the model has no connected MEP graph.

## External sensor workflow

An IFC often contains no live sensor objects. The API can attach an external operational sensor to an IFC room or element.

### Register a sensor

```bash
curl -X POST "http://localhost:8000/api/models/MODEL_ID/sensors" \
  -H "Content-Type: application/json" \
  -d '{
    "sensor_id": "SMOKE-UG2-001",
    "sensor_type": "smoke",
    "name": "Smoke detector UG2",
    "attach_to": "SPACE_GLOBAL_ID",
    "metadata": {"vendor": "Example"}
  }'
```

### Add a reading

```bash
curl -X POST "http://localhost:8000/api/models/MODEL_ID/sensors/SMOKE-UG2-001/readings" \
  -H "Content-Type: application/json" \
  -d '{"value": 94.2, "unit": "ppm", "status": "alarm"}'
```

### Find the alarm location

```bash
curl "http://localhost:8000/api/models/MODEL_ID/sensors/SMOKE-UG2-001/location"
```

The response contains the attached IFC entity and its path through storey/facility, building, site, and project where those relationships exist. A 3D client can use the returned GlobalId or STEP ID to highlight the object.

## CLI

Install locally:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
```

Extract without Neo4j:

```bash
ifc-graph extract path/to/model.ifc --out output/model
```

Import into configured Neo4j:

```bash
set NEO4J_URI=bolt://localhost:7687
set NEO4J_USER=neo4j
set NEO4J_PASSWORD=IfcBrain2026!
ifc-graph import path/to/model.ifc
```

## Semantic coverage

Primary parser coverage includes standard IFC relationships such as:

- `IfcRelAggregates`, `IfcRelNests`
- `IfcRelContainedInSpatialStructure`, `IfcRelReferencedInSpatialStructure`
- `IfcRelVoidsElement`, `IfcRelFillsElement`
- `IfcRelDefinesByType`, `IfcRelDefinesByProperties`
- `IfcRelAssociatesMaterial`, `IfcRelAssociatesClassification`
- `IfcRelConnectsElements`, `IfcRelConnectsPathElements`
- `IfcRelConnectsPorts`, `IfcRelConnectsPortToElement`
- `IfcRelAssignsToGroup`, `IfcRelServicesBuildings`
- `IfcRelSpaceBoundary*`
- selected process, flow-control, and structural-connection relationships

The extractor is schema-aware through IfcOpenShell and is designed for IFC2X3, IFC4, and IFC4X3 STEP files. Exporter quality still matters: a geometry-only or proxy-heavy IFC cannot provide relationships that are absent from the source.

## Capability behavior

| Capability | Enabled when |
|---|---|
| Spatial navigation | Spatial hierarchy/storeys/facility parts are present |
| IFC sensor localization | IFC sensor/control entities and spaces are present |
| External sensor localization | A sensor is registered and attached through the API |
| MEP trace | MEP entities and port/flow/connectivity relationships exist |
| Structural connectivity | Structural entities and IFC connectivity exist |
| Engineering collapse prediction | **Not provided**; requires structural analysis, loads, supports, capacities, and validation |

## Model isolation

Each `IFCEntity` is uniquely identified by:

```text
(model_id, step_id)
```

This avoids collisions because STEP IDs and even GlobalIds may be reused across separate files. The same file content maps to the same `model_id`; a changed file receives a new hash-derived identifier.

## Repository layout

```text
src/ifc_graph/
  api.py             FastAPI application and REST endpoints
  extractor.py       IfcOpenShell semantic extractor
  fallback_step.py   dependency-light STEP fallback
  neo4j_store.py     constraints, batching, model-scoped graph loading
  service.py         automated import orchestration
  cli.py             command-line interface
viewer/               generic interactive graph viewer
scripts/              Windows and Unix start/reset scripts
sample_data/          tiny test IFC, not a real engineering model
tests/                parser and archive-safety tests
```

## Tests

```bash
pip install -r requirements-dev.txt
pip install --no-deps -e .
pytest -q
```

The repository includes GitHub Actions CI.



## License

MIT
