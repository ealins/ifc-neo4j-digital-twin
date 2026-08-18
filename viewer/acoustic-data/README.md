# Acoustic registry data

This folder contains the externally maintained acoustic registry used by the IFC–RDF linking prototype.

## Files

- `acoustic_registry_v1.ttl` — authoritative RDF/Turtle prototype registry with PROV-O provenance.
- `acoustic_registry_v1.json` — viewer-oriented JSON projection of the same selected record.
- `pilot_link_manifest.json` — documents the Bau 1 pilot wall-to-record correspondence and its mapping status.

## Viewer upload format

The browser's **Upload acoustic registry JSON** control accepts a JSON object with a top-level `records` object keyed by the exact URI stored in the IFC `Pset_AcousticLink.AcousticRecordURI` property.

Minimal example:

```json
{
  "records": {
    "https://example.org/acoustic/record/001": {
      "uri": "https://example.org/acoustic/record/001",
      "acoustic_metric": "Rw",
      "value": 52,
      "unit": "dB",
      "assembly": {"name": "Reference wall assembly"},
      "source": {"organisation": "Example laboratory"},
      "provenance": []
    }
  }
}
```

The JSON file is a presentation/query projection. The Turtle file remains the RDF research artifact.

## Research boundary

The bundled VaBDat-derived record is an **external reference acoustic performance record**. It is not presented as an in-situ acoustic measurement of the linked Bau 1 wall.
