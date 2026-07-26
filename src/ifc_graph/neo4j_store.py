from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from neo4j import GraphDatabase

from .extractor import RELATIONSHIP_ALLOWLIST

LABEL_ALLOWLIST = {
    "SpatialElement", "Product", "Type", "PropertySet", "Material", "System",
    "Classification", "Entity",
}


def chunks(items: list[dict[str, Any]], size: int = 750) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


class Neo4jStore:
    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j"):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database

    def close(self) -> None:
        self.driver.close()

    def verify(self) -> None:
        self.driver.verify_connectivity()

    def execute(self, query: str, **params: Any) -> list[dict[str, Any]]:
        with self.driver.session(database=self.database) as session:
            return [record.data() for record in session.run(query, **params)]

    def create_schema(self) -> None:
        statements = [
            "CREATE CONSTRAINT ifc_model_id IF NOT EXISTS FOR (m:IFCModel) REQUIRE m.model_id IS UNIQUE",
            "CREATE CONSTRAINT ifc_entity_key IF NOT EXISTS FOR (n:IFCEntity) REQUIRE (n.model_id, n.step_id) IS UNIQUE",
            "CREATE INDEX ifc_entity_guid IF NOT EXISTS FOR (n:IFCEntity) ON (n.model_id, n.global_id)",
            "CREATE INDEX ifc_entity_class IF NOT EXISTS FOR (n:IFCEntity) ON (n.model_id, n.ifc_class)",
            "CREATE INDEX ifc_entity_storey IF NOT EXISTS FOR (n:IFCEntity) ON (n.model_id, n.storey_step_id)",
            "CREATE CONSTRAINT sensor_key IF NOT EXISTS FOR (s:Sensor) REQUIRE (s.model_id, s.sensor_id) IS UNIQUE",
        ]
        with self.driver.session(database=self.database) as session:
            for statement in statements:
                session.run(statement).consume()

    def upsert_model(self, model_id: str, summary: dict[str, Any], sha256: str) -> None:
        imported_at = datetime.now(timezone.utc).isoformat()
        payload = {
            "model_id": model_id,
            "source_file": summary.get("source_file"),
            "schema": summary.get("schema"),
            "parser": summary.get("parser"),
            "sha256": sha256,
            "semantic_nodes": int(summary.get("semantic_nodes", 0)),
            "semantic_relationships": int(summary.get("semantic_relationships", 0)),
            "imported_at": imported_at,
            "summary_json": json.dumps(summary, ensure_ascii=False),
        }
        self.execute(
            """
            MERGE (m:IFCModel {model_id: $model_id})
            SET m += $payload
            """,
            model_id=model_id,
            payload=payload,
        )

    def delete_model(self, model_id: str) -> None:
        self.execute("MATCH (n:IFCEntity {model_id:$model_id}) DETACH DELETE n", model_id=model_id)
        self.execute("MATCH (s:Sensor {model_id:$model_id}) DETACH DELETE s", model_id=model_id)
        self.execute("MATCH (m:IFCModel {model_id:$model_id}) DETACH DELETE m", model_id=model_id)

    def load_model(
        self,
        model_id: str,
        nodes: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
        summary: dict[str, Any],
        sha256: str,
        replace: bool = True,
    ) -> None:
        self.create_schema()
        if replace:
            self.delete_model(model_id)
        self.upsert_model(model_id, summary, sha256)
        self.load_nodes(model_id, nodes)
        self.load_relationships(model_id, relationships)

    def load_nodes(self, model_id: str, nodes: list[dict[str, Any]]) -> None:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in nodes:
            label = row.get("label") or "Entity"
            if label not in LABEL_ALLOWLIST:
                label = "Entity"
            clean: dict[str, Any] = {"model_id": model_id}
            for key, value in row.items():
                if value in ("", None):
                    continue
                if key in {"step_id", "storey_step_id", "building_step_id"}:
                    clean[key] = int(value)
                elif key in {"x", "y", "z", "elevation"}:
                    clean[key] = float(value)
                else:
                    clean[key] = value
            grouped[label].append(clean)

        with self.driver.session(database=self.database) as session:
            for label, rows in grouped.items():
                query = f"""
                UNWIND $rows AS row
                MERGE (n:IFCEntity:{label} {{model_id: row.model_id, step_id: row.step_id}})
                SET n += row
                """
                for batch in chunks(rows):
                    session.run(query, rows=batch).consume()

    def load_relationships(self, model_id: str, relationships: list[dict[str, Any]]) -> None:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in relationships:
            rel_type = str(row["relationship"])
            if rel_type not in RELATIONSHIP_ALLOWLIST:
                continue
            grouped[rel_type].append({
                "model_id": model_id,
                "source": int(row["source_step_id"]),
                "target": int(row["target_step_id"]),
                "relation_step_id": int(row.get("relation_step_id") or 0),
                "source_relation": row.get("source_relation"),
            })

        with self.driver.session(database=self.database) as session:
            for rel_type, rows in grouped.items():
                query = f"""
                UNWIND $rows AS row
                MATCH (a:IFCEntity {{model_id: row.model_id, step_id: row.source}})
                MATCH (b:IFCEntity {{model_id: row.model_id, step_id: row.target}})
                MERGE (a)-[r:{rel_type} {{relation_step_id: row.relation_step_id}}]->(b)
                SET r.source_relation = row.source_relation
                """
                for batch in chunks(rows):
                    session.run(query, rows=batch).consume()

    def model_exists(self, model_id: str) -> bool:
        rows = self.execute("MATCH (m:IFCModel {model_id:$model_id}) RETURN count(m) AS count", model_id=model_id)
        return bool(rows and rows[0]["count"])
