# E29 ground link schedule: provenance

The ground link the hub serves (CONTRACTS 12, section "Ground link") is computed from release content, never code:
`config/environment-profiles.json` (`comms_queue`, `link.sim_schedule`) and `config/environment-hazards.json`
(`earth_delay`). This note records where each value comes from. Source IDs are the catalog IDs of
[NASA-SOURCES.md](../../product/NASA-SOURCES.md); the quotes were read on 14 November 2026 (NASA-SOURCES.md
sections 1 and 2) and are not yet saved under research/.

| Value | Where | Source |
|---|---|---|
| Moon base one-way delay 10 s (`lunar_surface.comms_queue.simulated_one_way_delay_seconds`, basis sourced, `source_ids` `EV01`) | environment-profiles | EV01 (NASA-STD-3001 Vol 2 Rev F), section 9.7.8.3 [V2 9125] rationale, PDF p. 189: "Even small communication delays (e.g., 6 to 10 seconds on the surface of the moon)". The standard does not say whether that range is one way or round trip; the upper bound is used as the one-way delay, which is the slower (conservative) reading for when queued items leave and arrive |
| Moon base planned line-of-sight gaps (`lunar_surface.link.sim_schedule`, gap_kind `planned_line_of_sight`, planned true) | environment-profiles | N07, p. 2: Earth is "visible from the lunar South Pole for only about half of every sidereal month, the time it takes the Moon to orbit once around Earth (approximately 14 days)"; N08, pp. 1 and 7: without relays, direct-to-Earth coverage of South Pole sites is "43% best case and 17% worse case". These speak to visibility only, not to delay |
| Gap length 13.66 days, repeating every 27.32 days | environment-profiles | N07 gives about half of a sidereal month, approximately 14 days; the golden in `eval/scenarios/E29` checks the window against those 14 days. The exact length uses the sidereal month of 27.321661 days, a standard astronomical constant that N07 does not state: it is the only non-NASA value here, labelled as such in the golden |
| Window phase (first gap starts 2026-11-20T00:00:00Z, four cycles) | environment-profiles | Team choice for the SIM schedule (`link.basis` team default): the length of each gap at a given site depends on the site and on relays (N08; ENVIRONMENTS.md section 19, "What the sources do not give") |
| Mars base one-way delay 1200 s (`mars_surface.comms_queue`, basis sourced) | environment-profiles | EV25: "communication delay of up to 20 minutes one way"; N09: Mars messages take "between four and 24 minutes" one way |
| Mars solar conjunction gap 2025-12-29T00:00:00Z to 2026-01-16T00:00:00Z (gap_kind `conjunction`, planned true, `link.basis` sourced) | environment-profiles | N11: NASA "will not have contact with any Mars missions" from 29 December 2025 to 16 January 2026; N10: conjunction comes "approximately every 26 months" |
| Next Mars conjunction | not configured | No source read gives its dates; "approximately every 26 months" (N10) is not a schedule, so no window is invented |
| Station and transit delays and schedules | environment-profiles | Unchanged from the package example (team default, no source: ENVIRONMENTS.md says the sources read give no LEO delay figure) |
| `delay_text`, `delay_source_ids` | environment-hazards | `earth_delay` of each environment, parenthesised source IDs moved to `delay_source_ids` (CONTRACTS 12). `config/environment-hazards.json` is a byte copy of `docs/product/environment-hazards.json` |
| Link words, gap words, duration words, catch-up words | ui-copy | `config/ui-copy.json` is the package example `contracts/api/examples/config/ui-copy.example.json` (UX-DESIGN 1.13) |

None of these sources makes the ground a step (NASA-SOURCES.md section 4, "The ground is a reader"): the schedule
only decides when queued ground messages and handoffs leave.
