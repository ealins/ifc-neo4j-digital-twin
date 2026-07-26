from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.getenv("IFC_DATA_DIR", "/data"))
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "change_this_password")
    neo4j_database: str = os.getenv("NEO4J_DATABASE", "neo4j")
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "1000"))
    allow_csv_fallback: bool = os.getenv("ALLOW_CSV_FALLBACK", "true").lower() == "true"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    def ensure_dirs(self) -> None:
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
