# Supply catalog: provenance of the eye supplies

Provenance for the `supplies` entries E32 owns in `config/kit-map.json` (E32 8.2). The kit-map schema gives a
catalog entry no citation field, so the sources live here. Retrieved on 15 November 2026. RS IDs are in
[docs/product/RESPONDER-FLOWS.md](../../../product/RESPONDER-FLOWS.md) (sources table); every source below is NASA's
except RS30, and the text says so where it is used.

## Sources

| ID | Document | Where |
|---|---|---|
| RS08 | ISS Integrated Medical Group Medical Checklist, JSC-48522-E4, ISS All Expeditions (NASA, 2001; historical, superseded) | p. 558 (Eye problems, chemical burn), p. 590 (Eye problems, penetrating object), pp. 988 and 1002 (Contents list, US) |
| RS32 | Eye Foreign Body CliFF (2023) (NASA) | p. 8 |
| RS33 | Burn Chemical Eye CliFF (2023) (NASA) | pp. 1, 7 |
| RS30 | Eye injuries (NHS). Not NASA: used only for the rinse duration, which no NASA source states | public page |

## Entries

| supply_id | Why it is listed | Quantity |
|---|---|---|
| `eye_rinse_water` | RS33 p. 1: "Immediate eye irrigation therapy is the most important initial management of chemical eye burns". RS08 p. 558: "Prior to SSE configuration, attempt eye irrigation with Drink Bag, Drink Straw." Named by E28's plan_eye_injury rinse step. | None. No source gives a volume; RS30 (not NASA) gives a duration, "at least 20 minutes". The plan keeps `per_use` null with `to_be_set_by` clinical review |
| `eye_wash_set` | RS08 p. 558: "For definitive eye flush, deploy SSE near SVO-ZV interface", where SSE is the Space Station Eyewash (the same procedure's Figure 1). Equipment, so `consumable` false. | Not entered. RS08 p. 1002 lists "Space Station Eyewash (SSE) CCPK 1" |
| `eye_shield` | RS32 p. 8: "Place a rigid shield over the eye and tape it to the skin covering the rigid periorbital bone so that there is no pressure on the globe", and "Eye shielding is included". RS08 p. 590: "Tape metal Eye Shield over Eye Pad." RS33 p. 7: "Shielding of the eye will also be needed". | Not entered. RS08 p. 988 lists "Eye Shield AMP P4-A6 Metallic eyepatch 1" |
| `towels` | RS08 p. 558: "Use Towels to absorb excess water." Named by the package's eye plan return stage. | Not entered |

## Why no stock rows

RS08 is the only source with packed quantities, and it is a 2001 ISS inventory that NASA has superseded; it is not a
packing list for any of the four environments the product serves. E32 8.2 also says no packed quantity has a source
today, and release check S-L4 rejects a "sourced" stock row until R-E32-03 adds `source_ids`. So every eye supply is
catalogued and not stocked: a plan step that needs it reads not aboard and fails closed (E28 14c) until the flight
surgeon sets the stock as a "team default" row, or R-E32-03 lets a sourced row carry RS08.
