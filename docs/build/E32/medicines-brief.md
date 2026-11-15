# Medicines aboard: design brief (E32-06)

Replaces `reports/crew-health-medications-2026-11-14/DESIGN-BRIEF.md`, which docs/tdd/E32.md cites but which was never
committed. Written from docs/tdd/E32.md sections 3.4 to 3.8, 4.3, 4.4, 5.2, 5.3, 8.4 and 10, docs/ENGINE-SPEC.md
sections 3.6, 3.9, 11 and 12, the public functions of `pharmacy/pipeline.py` and `pharmacy/types.py`, and the sourced
values in [research/dosing-safety.md](research/dosing-safety.md). Owner decisions of 14 November 2026 are marked.

## Problem

Exploration crews treat without the ground: N13 ("No real-time communications + blackouts", "Expanded onboard medical
care (crew/vehicle-reliant)", slides 3 and 5) and N18 p. 6-2 ("when orbiter LOS, begin appropriate treatment; call
Surgeon as soon as possible"). Medicines will outlive their labeled expiry (RT22 p. 3) while orbit data show they
mostly keep potency (RT22 p. 5). The crew needs the label dose of a reviewed rule, never more than any limit, from a lot
that is safe to use, with every number traceable, and nothing that waits for the ground.

## Decisions

1. **The engine proposes, a person confirms.** The dose engine computes one amount per proposal from `treatments.json`
   rules and the lot's potency estimate. The crew medical officer confirms; the acting medical officer confirms only a
   product whose `confirm_by` is `any_trained_crew`, at the label dose (E32 3.7). RT01 p. 3: two CMOs per increment.
2. **Every safety limit is the hub's.** Per administration, per period, cumulative and catastrophic limits are
   enforced on the final amount at proposal and again inside the confirming transaction (INV-M1). No person and no
   model can raise one; the only route past a refusal is a manual administration, which is still bounded by stock,
   flagged and audited (decision 9).
3. **Units travel with values.** UCUM codes on every amount; mass converts to mass and volume to volume, exactly in
   decimals; arbitrary units never convert (`unit_incompatible`).
4. **Allergy and interaction hard stops.** "An allergy or interaction hard stop needs the medical officer's override
   with a reason, recorded." Override eligibility is separate from confirmation eligibility.
5. **Potency compensation is on by default (owner decision), bounded and gated.** Compensation raises the amount
   toward the target at the lot's median potency and is capped at the remaining allowance, with the shortfall stated.
   It is never applied to a `do_not_use` lot. In LIVE it applies only to products with a
   `live_compensation_authorizations` entry; otherwise the label dose is given with the estimate and the shortfall
   (owner decision R-E32-10, V104).
   Spec correction 2 (decided here): with a rounding tolerance of 0 and whole tablets, the nearest amount rarely
   equals the target, and E32 3.6 refused it (`no_deliverable_amount`), so compensation never produced a dose. The
   compensated path now takes the largest number of increments whose expected delivery at the median stays at or
   under every target, and the `potency_shortfall` alert states the gap (HZ6, E32-T-74); it refuses only when one
   increment already delivers more than a target. No source gives a rounding tolerance, so it stays 0 and the
   engine never rounds up past a target.
6. **Accounting never undercounts, and an in-specification lot is its label.** Every limit counts
   `Acc_i(a) = a * S_i * max(1, u_i)`, never below the label (E32 3.6), with one sourced exception (spec correction
   1, decided here): when the product carries a compendial acceptance range for its labeled amount
   (`treatments.json` `products[].assay_acceptance`) and the lot's upper estimate `u_i` is within it, each increment
   counts at its labeled amount. Without the exception the module refused promethazine at its NASA dose for a lot
   whose upper estimate was 1.0117, since one 25 mg tablet then counted as 25.29 mg against the 25 mg maximum.
   Sources, read 2026-11-14: USP01 (Acetaminophen Tablets, Definition: "NLT 90.0% and NMT 110.0% of the labeled
   amount"), USP02 (Ibuprofen Tablets, the same range), USP03 (Promethazine Hydrochloride Tablets: "NLT 95.0% and
   NMT 110.0%"), and RT22 p. 5, where NASA judges the flown medicines by the same yardstick ("All medications but one
   (Melatonin) found to contain API within USP guidelines (90-110%) of label claim"). This is an inference, labeled
   as one: no source says in so many words that a tablet within the range delivers the labeled dose. USP's General
   Notices say the acceptance criteria "allow for analytical error, for unavoidable variations in manufacturing and
   compounding, and for deterioration to an extent considered acceptable", and a product within its monograph range
   meets the legal strength standard for its label (FD&C Act 501(b)). The range is a pooled lot assay, which matches
   the engine's lot-level estimate, not a per-tablet bound. Above the range, or for a product with no sourced range
   (`assay_acceptance: null`), the upper estimate counts as before and the refusal names the binding limit; the
   officer's route past it is a manual administration (decision 9), recorded with every limit it breaks.
7. **Lots are excluded synchronously.** Recalled, adversely inspected, expired and `do_not_use` lots are excluded at
   proposal and at confirmation from the hub's own records, whatever the worker's state (INV-M3).
8. **Missing facts refuse.** An unknown allergy list, a missing context part, a missing or stale weight for a
   per-kilogram rule, an unsourced limit: each refuses with its code, never a default. Unreviewed rules refuse in LIVE.
9. **Manual administration is a recorded fact, not a bypass (B06, settled here).** It records what a person already
   gave outside the engine. It is allowed only to the crew medical officer and the acting medical officer, never for
   the subject by themself. Its lot must exist, be in a zone of the active environment and hold the amount
   (`SUPPLY_BELOW_ZERO` otherwise, through the supplies stock port, so stock never goes negative). The product must
   be in the formulary and the amount's unit must convert to the strength unit. It is flagged `outside_engine: true`,
   counts toward every later limit window and interval with its accounting amount, and its response carries the
   checks it failed (`limit_findings`: which per-administration, period or cumulative limits it exceeded, excluded lot
   reasons, allergies) so the record is honest. It cannot be used to confirm a proposal and never changes a rule.
10. **The assistant never sees a dose.** No tool maps to a medicines operation; plans, flows, steps and model input
    and output carry no dose (EI6 as restated in R-E32-10).
11. **Clinical sign-off is visible.** Every rule and document carries `review_state`; the lots board, options and
    proposals show it. A `draft` rule works in SIM and REPLAY and refuses in LIVE (`rule_not_reviewed_live`).
12. **Replayable assessments.** Each assessment stores its inputs and the pharmacy bundle SHA-256 so `pharmacy.replay`
    can reproduce it bit for bit (ENGINE-SPEC 12).
13. **FHIR export stays unavailable** until E29 (B07): `exportMedicationAdministration` returns
    `CONTEXT_INAPPLICABLE`.
14. **No ground step.** Ground consultation is information (`consult_ground_first`, N18 p. 6-2), never a wait.

## Interim choices of this build

- `medicines.json` and `treatments.json` are validated against their schemas and the M-L checks at module start and
  their SHA-256 and version are stored on every proposal and administration. They are not E01 release documents yet:
  adding names to `ConfigName` makes `config/adapters/verification.py` require them in every release and
  `config/api/router.py` require a basis summary for each, both outside E32's paths (R-E32-12, R-E32-13 "Interim: not
  activated").
- The potency engine runs in a child process (`medicines/adapters/potency_child.py`) under the pharmacy interpreter
  named by the hub setting `medicines.engine_python`, with a deadline, a memory cap sampled inside the child, and a
  lowered priority (E32 5.3). Runs are scheduled through E01's TaskQueue; a hub composed without one answers the
  schedule with `CAPABILITY_UNAVAILABLE` and says why.
