from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from pathlib import Path

from .extractor import extract_graph, write_export
from .neo4j_store import Neo4jStore
from .service import ModelService
from .settings import settings
from .utils import make_model_id, resolve_ifc_input, safe_filename, sha256_file


def extract_command(path: Path, out: Path) -> None:
    work = out / "_work"
    work.mkdir(parents=True, exist_ok=True)
    resolved = resolve_ifc_input(path, work)
    nodes, edges, summary = extract_graph(resolved)
    checksum = sha256_file(path)
    model_id = make_model_id(path.name, checksum)
    summary.update({"model_id": model_id, "sha256": checksum, "source_file": path.name})
    for node in nodes:
        node["model_id"] = model_id
    for edge in edges:
        edge["model_id"] = model_id
    write_export(out, nodes, edges, summary)
    shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


async def import_command(path: Path) -> None:
    settings.ensure_dirs()
    store = Neo4jStore(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password, settings.neo4j_database)
    try:
        store.verify()
        service = ModelService(settings, store)
        result = await service.import_file(path, safe_filename(path.name), replace=True)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="ifc-graph", description="Generic IFC → Neo4j semantic graph pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    extract_parser = sub.add_parser("extract", help="Extract IFC graph to CSV/JSON without Neo4j")
    extract_parser.add_argument("ifc", type=Path)
    extract_parser.add_argument("--out", type=Path, default=Path("output"))

    import_parser = sub.add_parser("import", help="Extract and import an IFC into configured Neo4j")
    import_parser.add_argument("ifc", type=Path)

    args = parser.parse_args()
    if args.command == "extract":
        extract_command(args.ifc, args.out)
    else:
        asyncio.run(import_command(args.ifc))


if __name__ == "__main__":
    main()
