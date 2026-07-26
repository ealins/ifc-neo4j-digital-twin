from pathlib import Path
from zipfile import ZipFile

import pytest

from ifc_graph.utils import make_model_id, resolve_ifc_input, safe_filename


def test_safe_filename_and_model_id():
    assert safe_filename("../../My Building (1).ifc") == "My_Building_1_.ifc"
    assert make_model_id("My Building.ifc", "a" * 64) == "my-building-aaaaaaaaaaaa"


def test_single_ifc_zip(tmp_path: Path):
    source = tmp_path / "model.ifc"
    source.write_text("ISO-10303-21;", encoding="utf-8")
    archive = tmp_path / "model.ifczip"
    with ZipFile(archive, "w") as zf:
        zf.write(source, "nested/model.ifc")
    resolved = resolve_ifc_input(archive, tmp_path / "work")
    assert resolved.name == "model.ifc"


def test_multi_ifc_zip_is_rejected(tmp_path: Path):
    archive = tmp_path / "bad.ifczip"
    with ZipFile(archive, "w") as zf:
        zf.writestr("a.ifc", "a")
        zf.writestr("b.ifc", "b")
    with pytest.raises(ValueError, match="multiple IFC"):
        resolve_ifc_input(archive, tmp_path / "work")
