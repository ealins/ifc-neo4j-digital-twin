from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str) -> str:
    cleaned = SAFE_NAME_RE.sub("_", Path(name).name).strip("._")
    return cleaned or "model.ifc"


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def make_model_id(filename: str, sha256: str) -> str:
    stem = SAFE_NAME_RE.sub("-", Path(filename).stem.lower()).strip("-")[:40] or "ifc-model"
    return f"{stem}-{sha256[:12]}"


def resolve_ifc_input(path: Path, work_dir: Path) -> Path:
    suffix = path.suffix.lower()
    if suffix == ".ifc":
        return path
    if suffix != ".ifczip":
        raise ValueError("Only .ifc and .ifczip files are supported.")

    extract_dir = work_dir / "ifczip"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)
    with zipfile.ZipFile(path) as archive:
        candidates = []
        for member in archive.infolist():
            member_path = Path(member.filename)
            if member.is_dir() or member_path.suffix.lower() != ".ifc":
                continue
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError("Unsafe path found inside IFCZIP.")
            candidates.append(member)
        if not candidates:
            raise ValueError("The IFCZIP does not contain an .ifc file.")
        if len(candidates) > 1:
            raise ValueError("The IFCZIP contains multiple IFC files; upload one model at a time.")
        member = candidates[0]
        target = extract_dir / safe_filename(Path(member.filename).name)
        with archive.open(member) as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination)
        return target


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    wrapped = getattr(value, "wrappedValue", None)
    if wrapped is not None:
        return json_safe(wrapped)
    return str(value)


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
