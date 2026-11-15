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

1. **The engine proposes, a person confirms (SYSTEM 8.4 rule 3, Q14).** The dose engine computes one amount per
   proposal from `treatments.json` rules and the lot's potency estimate. Who confirms comes from E31's
   `ClinicalAuthorityQuery` in the confirming transaction, never from the caller. The crew medical officer confirms
   any proposal. The accepted acting medical officer confirms the recommended dose, compensated included, when the
   product's `confirm_by` is `any_trained_crew` or the subject's active plan step names this medicine for this
   condition (E28, `PlanMedicines`); never an allergy or interaction override. The administration and its audit
   entry record `confirmed_as` and, when acting, the `role_assignment_id`. RT01 p. 3: two CMOs per increment.
   Training: no skill in `skills-catalog.json` covers medication and no product names a required skill, so the
   `any_trained_crew` permission stands for "with the required training" until the catalog has one (a gap, stated).
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
   It is never applied to a `do_not_use` lot. It applies in LIVE as in SIM and REPLAY with no outside authorization
   (Q11 removed the `live_compensation_authorizations` gate); every engine gate still applies.
   Spec correction 2 (decided here): with a rounding tolerance of 0 and whole tablets, the nearest amount rarely
   equals the target, and E32 3.6 refused it (`no_deliverable_amount`), so compensation never produced a dose. The
   compensated path now takes the largest number of increments whose expected delivery at the median stays at or
   under every target, and the `potency_shortfall` alert states the gap (HZ6, E32-T-74); it refuses only when one
   increment already delivers more than a target. No source gives a rounding tolerance, so it stays 0 and the
   engine never rounds up past a target.
6. **Accounting never undercounts.** Every limit counts `Acc_i(a) = a * S_i * max(1, u_i)` on every path, never
   below the label and never at the label when the lot's upper estimate is above it (E32 3.6). A proposed spec
   correction that would have counted a lot inside its USP acceptance range at its label was rejected by the
   coordinator: no source says a tablet within the range delivers the labeled dose, and a refusal is the safe
   outcome. A lot whose upper estimate puts one increment over a limit is refused with the binding limit named; the
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
15. **Visibility before disclosure (SYSTEM 8.3).** Every read, every person event and every replayed saved result
    needs the subject or the subject's consent for that field (`ConsentQueries.allows`, scope `("medicines",
    context|proposals|administrations|names)`). Officer status, an acting assignment or a help session grants no
    chart read. A confirm or correct error carries the proposal or the record only after that check; otherwise it is
    `UNAUTHORIZED_SCOPE` with no `current_state`. Commands whose answer holds chart content need the grant as well as
    the authority.
16. **Idempotency identity (SYSTEM 5.8).** `If-Match` is one of the identity parameters of
    `confirmDoseProposal`, so the same key with another revision is a conflict, never a replay; a replay is rechecked
    under the current grant before its result or reference is returned.
17. **Clock discontinuity (E32 C1 to C4, SYSTEM 5.2).** Each medicines command compares wall time against the
    injected monotonic clock since this process's previous command. A forward jump over
    `medicines.clock_forward_tolerance_seconds` (team default 60 s) is stored in `medicines_clock_check` with both deltas,
    the reading it was measured from and the clock module's correction head; until a correction newer than that head
    is in `ClockCorrections.history`, proposals refuse `clock_discontinuity` and confirmations supersede. A new
    process has no common monotonic origin, so its first reading starts a new chain.

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
- Audit policies, replication tables and the medicines migration are registered by the module's own composition
  until E01's registry hooks land on main (m11); the switch is a registration change, not a schema change.
- Assessment inputs carry assays and inspections only: `sensor_series` stays empty until a habitat port and
  `channel_bindings` exist, and the engine is told so rather than given a default storage history (rule 5).
- Finding from the real engine, reported and not tuned (AGENTS section 10): with no sensor series, the shipped bundle
  rates `lot-apap-001` and `lot-pmz-tab-001` `do_not_use` at an as-of of 2028-03-01 (0.05 quantile near 55% of
  label, below the lower limit, plus degradant and coverage reasons). At the fixture's as-of of 2027-11-01 the real
  engine reproduces the fixture states within 0.001 on every quantile. A station running the default bundle today
  therefore excludes those lots until assays arrive; that is the engine's answer and E32 shows it as such.
- Consent and plan medicines are structural copies of ports not on main: `ConsentQueries` (E02, as measurement's
  copy) and `PlanMedicines` (E28: its steps carry `medicine.named_as` text only, no product ID, so no plan port
  answers this yet). Absent, they answer no: until E02 binds consent only the subject reads their medicines, so the
  officer cannot use the chart in production; until E28 answers, the acting officer confirms only `any_trained_crew`
  products. Without the clock module, a detected jump stays marked.
- Weight comes through medicines' own `WeightReader` port. Medicines may not import records' port module (it imports
  records' domain, and the import contract forbids that), so the app assembly binds it:
  `WeightReader = RecordsWeightReader(RecordsStore)`, a structural copy of `RecordsStore.observations` checked by a
  signature test. Until the assembly carries that binding, weight stays absent and per-kilogram rules refuse
  `weight_missing`.
