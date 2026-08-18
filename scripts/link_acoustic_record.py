from __future__ import annotations

import argparse
from pathlib import Path

import ifcopenshell
import ifcopenshell.api


def find_link_pset(wall):
    for rel in getattr(wall, "IsDefinedBy", []) or []:
        if not rel.is_a("IfcRelDefinesByProperties"):
            continue
        pset = rel.RelatingPropertyDefinition
        if pset and pset.is_a("IfcPropertySet") and pset.Name == "Pset_AcousticLink":
            return pset
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Link an IFC wall to an external acoustic registry record without embedding the acoustic record in IFC."
    )
    parser.add_argument("input_ifc", type=Path)
    parser.add_argument("output_ifc", type=Path)
    parser.add_argument("--wall-guid", required=True, help="IfcWall GlobalId")
    parser.add_argument("--record-uri", required=True, help="Stable URI of the external acoustic RDF record")
    parser.add_argument("--status", default="REFERENCE_MATCH")
    parser.add_argument(
        "--basis",
        default="External reference acoustic performance record used for IFC-RDF workflow validation.",
    )
    args = parser.parse_args()

    model = ifcopenshell.open(str(args.input_ifc))
    wall = model.by_guid(args.wall_guid)
    if wall is None:
        raise SystemExit(f"GlobalId not found: {args.wall_guid}")
    if not wall.is_a("IfcWall"):
        raise SystemExit(f"{args.wall_guid} resolves to {wall.is_a()}, not IfcWall")

    pset = find_link_pset(wall)
    if pset is None:
        pset = ifcopenshell.api.run("pset.add_pset", model, product=wall, name="Pset_AcousticLink")

    ifcopenshell.api.run(
        "pset.edit_pset",
        model,
        pset=pset,
        properties={
            "AcousticRecordURI": args.record_uri,
            "MappingStatus": args.status,
            "MappingBasis": args.basis,
        },
    )

    model.write(str(args.output_ifc))
    print(f"Linked IfcWall {args.wall_guid}")
    print(f"AcousticRecordURI: {args.record_uri}")
    print(f"Output: {args.output_ifc}")


if __name__ == "__main__":
    main()
