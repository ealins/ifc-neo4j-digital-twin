from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from .extractor import extract_graph, write_export
from .neo4j_store import Neo4jStore
from .settings import Settings
from .utils import make_model_id, resolve_ifc_input, safe_filename, sha256_file, write_json


class ModelService:
    def __init__(self, settings: Settings, store: Neo4jStore):
        self.settings = settings
        self.store = store
        self.import_lock = asyncio.Lock()
        self.settings.ensure_dirs()

    def model_dir(self, model_id: str) -> Path:
        return self.settings.models_dir / model_id

    def read_local_summary(self, model_id: str) -> dict[str, Any] | None:
        path = self.model_dir(model_id) / "summary.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def list_local_models(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for directory in self.settings.models_dir.iterdir():
            if not directory.is_dir():
                continue
            summary = self.read_local_summary(directory.name)
            if summary:
                results.append({"model_id": directory.name, **summary})
        results.sort(key=lambda item: item.get("imported_at", ""), reverse=True)
        return results

    async def import_file(self, uploaded_path: Path, original_name: str, replace: bool = True) -> dict[str, Any]:
        async with self.import_lock:
            checksum = sha256_file(uploaded_path)
            model_id = make_model_id(original_name, checksum)
            target_dir = self.model_dir(model_id)
            target_dir.mkdir(parents=True, exist_ok=True)
            source_name = safe_filename(original_name)
            preserved_upload = target_dir / source_name
            if uploaded_path.resolve() != preserved_upload.resolve():
                shutil.copy2(uploaded_path, preserved_upload)
            resolved_ifc = resolve_ifc_input(preserved_upload, target_dir)
            nodes, relationships, summary = await asyncio.to_thread(extract_graph, resolved_ifc)
            summary.update({
                "model_id": model_id,
                "source_file": source_name,
                "sha256": checksum,
            })
            for node in nodes:
                node["model_id"] = model_id
            for relationship in relationships:
                relationship["model_id"] = model_id
            write_export(target_dir, nodes, relationships, summary)
            await asyncio.to_thread(
                self.store.load_model,
                model_id,
                nodes,
                relationships,
                summary,
                checksum,
                replace,
            )
            db_rows = self.store.execute(
                "MATCH (m:IFCModel {model_id:$model_id}) RETURN m.imported_at AS imported_at",
                model_id=model_id,
            )
            if db_rows:
                summary["imported_at"] = db_rows[0].get("imported_at")
                write_json(target_dir / "summary.json", summary)
            return summary

    def delete_model(self, model_id: str) -> None:
        self.store.delete_model(model_id)
        directory = self.model_dir(model_id)
        if directory.exists():
            shutil.rmtree(directory)
