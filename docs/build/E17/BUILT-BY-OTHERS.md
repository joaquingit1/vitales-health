# Pieces built by other epics

| Path | Piece | Design | Builder |
|---|---|---|---|
| `model/schemas/model-release.schema.json` | Release schema needed by E18's model reference reader. Includes the model, weights, runtime, decoding, provenance and evaluation reference fields. E17 must complete activation and controller validation. | E17 section 3.4; E18 section 3.6 | E18 T1 |

E18 checks numeric provenance using JSON Pointer keys in `basis`. This resolves the unspecified key format without accepting an unexplained numeric value. No actual release or model weights are supplied by this schema.
