from pathlib import Path

from ifc_graph.extractor import extract_graph


def test_minimal_ifc_extracts_spatial_graph():
    path = Path(__file__).parents[1] / "sample_data" / "minimal_building.ifc"
    nodes, edges, summary = extract_graph(path)
    classes = {node["ifc_class"] for node in nodes}
    relations = {edge["relationship"] for edge in edges}
    assert summary["schema"] == "IFC4"
    assert {"IFCPROJECT", "IFCSITE", "IFCBUILDING", "IFCBUILDINGSTOREY", "IFCSPACE", "IFCDOOR"} <= classes
    assert "BELONGS_TO_SPATIAL" in relations
    assert "CONTAINED_IN" in relations
    door = next(node for node in nodes if node["ifc_class"] == "IFCDOOR")
    assert door["storey_name"] == "Ground Floor"
