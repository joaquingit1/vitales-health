# Dosing safety: sourced values for the E32 medicines engine

Replaces `research/dosing-safety.md`, which docs/tdd/E32.md cites but which was never committed. Retrieved on
14 November 2026. NASA documents come first and are cited by their catalog ID in
[docs/product/NASA-SOURCES.md](../../../product/NASA-SOURCES.md); a drug label is cited only for a fact the NASA
documents do not give, and the text says so each time.

This note is research for configuration, not medical advice. The NASA briefs say they "share clinical knowledge and
provide best practices, not to deliver direct medical care recommendations or guidance for individuals" (RT22, every
page footer), and the catalog rule "cite each source only for what it says" (NASA-SOURCES.md section 4) applies. Every
rule built from these values ships with `review_state` `draft`, so a LIVE mission refuses it
(`rule_not_reviewed_live`) until a clinician reviews it.

## 1. Sources

| ID | Document | Where | URL |
|---|---|---|---|
| N18 | Shuttle Medical Checklist, JSC-48031 Rev K (archived NASA copy) | pp. 2-4, 4-38, 4-39, 4-40, 5-3, 6-2, 6-3, 6-7, 6-9, 6-10, 6-12 | http://web.archive.org/web/20170212012224/https://www.nasa.gov/centers/johnson/pdf/177788main_MedicalCklist.pdf |
| RT04 | OCHMO-MTB-004 Space Adaptation Sickness (SAS), Rev A, 25 Sep 2025 | p. 7 | https://www.nasa.gov/wp-content/uploads/2025/09/ochmo-mtb-004-space-adaptation-sickness-sas.pdf |
| RT22 | OCHMO-MTB-012 Pharmaceutical Care, Rev B | pp. 1, 3, 5, 10 | https://www.nasa.gov/wp-content/uploads/2026/01/ochmo-mtb-012-pharmaceutical-care.pdf |
| RT01 | OCHMO-TB-044 Medical Operations: Spaceflight Considerations | p. 3 | https://www.nasa.gov/wp-content/uploads/2025/08/ochmo-tb-044-medical-operations.pdf |
| LBL01 | DailyMed, TYLENOL REGULAR STRENGTH (acetaminophen) tablet, Kenvue Brands, set ID 1622f694-4d63-4c56-8737-fae31f0ecfb7, published 28 May 2026 (not NASA) | Drug Facts: Directions, Liver warning, Do not use, Ask a doctor | https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=1622f694-4d63-4c56-8737-fae31f0ecfb7 |
| LBL02 | DailyMed, Ibuprofen Tablets USP (Rx), set ID 793e4b36-8208-4fa3-9f4f-9e9e72648280, revised 05/2021 (not NASA) | Dosage and Administration; Contraindications | https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=793e4b36-8208-4fa3-9f4f-9e9e72648280 |
| LBL03 | DailyMed, Promethazine Hydrochloride Tablets USP 12.5, 25 and 50 mg (Rx), set ID e16238dc-9292-49ac-b80f-8afeccb88bc5, published 2 Nov 2026 (not NASA) | Dosage and Administration: Nausea and Vomiting; Contraindications | https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=e16238dc-9292-49ac-b80f-8afeccb88bc5 |

Page numbers of N18 are the checklist's own page labels (for example "6-10"), matched against its table of contents
(MILD MOTION SICKNESS 4-38, NAUSEA/VOMITING 4-39, NOSEBLEED 4-40, PAIN RELIEF 5-3, MEDICATION SIDE EFFECTS 6-2) and its
cross-references ("see WARNING, 6-10").

## 1.1 Facts, one per line

### What NASA gives

1. **Pain relief order and interval (N18 p. 5-3, "PAIN RELIEF (IN INCREASING ORDER OF STRENGTH)").** "Tylenol (Drug
   Subpack-Spine) Dose: 1-2 tabs every 4-6 hr as needed"; "Motrin (Drug Subpack-Spine) (see WARNING, 6-9) Dose: 1 tab
   every 4-6 hr as needed w/food and drink". HEADACHE points to this page ("HEADACHE (see PAIN RELIEF, 5-3)").
   NASA gives tablets, not milligrams: the checklist does not state the strength of the flown tablet, so the
   milligram dose comes from a label (items 12 to 14) and the tablet count of N18 is not converted.
2. **Ibuprofen and aspirin allergy (N18 p. 6-9).** "WARNING Do not take Motrin if allergic to aspirin".
3. **Nosebleed (N18 p. 4-40).** "WARNING Do not administer Ascriptin or Motrin. Use Tylenol for pain."
4. **Promethazine daily maximum (N18 p. 6-10).** "Phenergan may have an additive sedative effect w/other central
   nervous system depressants (see general WARNING, 6-2). ... Do not exceed 100 mg in a 24-hr period".
5. **Promethazine interval (N18 pp. 4-38 and 4-39).** "Phenergan (oral) (Drug Subpack-15) ... Dose: 1-2 tabs every 4-6
   hr as needed" under MILD MOTION SICKNESS and NAUSEA/VOMITING.
6. **Sedative combinations (N18 p. 6-2, general WARNING).** "Following drugs should not be used together as they may
   cause excessive drowsiness in combination: Ambien, Benadryl, Claritin, Demerol, Duragesic Patch (Fentanyl), Morphine
   Sulfate, Phenergan, Restoril, Valium, Vicodin".
7. **Asterisked medicines and loss of signal (N18 p. 6-2).** "*Contact Surgeon before giving any medication marked
   w/asterisk. In emergency or when orbiter LOS, begin appropriate treatment; call Surgeon as soon as possible".
   Phenergan is asterisked; Tylenol and Motrin are not. The product maps the asterisk to `consult_ground_first`
   (information, never a wait, rule 7) and keeps asterisked products to `confirm_by` `crew_medical_officer`.
8. **Promethazine is NASA's choice for space motion sickness (RT04 p. 7).** "NASA treatment of choice and most
   effective in-flight SMS treatment found to date is promethazine"; "Terrestrially, promethazine is dosed at
   25-50mg"; "In more severe cases of SMS, IM or PR may be preferred as vomiting can interfere with oral dosing."
9. **Two medical officers (RT01 p. 3).** "Program designates/trains two CMOs for each increment." Cited only for the
   role, as the catalog rule requires.
10. **Potency on orbit (RT22 p. 5).** Wotring 550-day ISS study: acetaminophen 325 mg 97.0 % of label, ibuprofen 400 mg
    99.9 %, aspirin 325 mg 96.5 %; "All medications but one (Melatonin) found to contain API within USP guidelines
    (90-110%) of label claim." This is why the engine treats an assessment as an estimate with quantiles and never
    lowers an accounting amount below the label (E32 3.6).
11. **Expiry beyond the mission (RT22 pp. 3 and 10).** "long duration spaceflight planned for 1-5 years which will be
    beyond the current average medication expiration dates of 1-2 years"; "Neither shelf-life nor expiration date
    state that medications are harmful if used beyond the identified date". The product still excludes a lot at its
    labeled expiry (E32 3.5 step 4); RT22 explains why the potency engine exists, not a reason to lift that exclusion.

### What only a label gives (NASA documents above are silent)

12. **Acetaminophen 325 mg dose (LBL01, Directions).** "adults and children 12 years and over take 2 tablets every 4 to
    6 hours while symptoms last do not take more than 10 tablets in 24 hours, unless directed by a doctor do not use
    for more than 10 days unless directed by a doctor". Liver warning: "The maximum daily dose of this product is 10
    tablets (3,250 mg) in 24 hours for adults". Do not use "with any other drug containing acetaminophen" and "if you
    are allergic to acetaminophen". Ask a doctor before use "if the user has liver disease" and "if the user is taking
    the blood thinning drug warfarin". Used because N18 gives tablets without a strength and no daily maximum.
13. **Ibuprofen 400 mg dose (LBL02, Dosage and Administration).** "Mild to moderate pain: 400 mg every 4 to 6 hours as
    necessary for relief of pain"; "Do not exceed 3200 mg total daily dose". Contraindications: "known hypersensitivity
    to Ibuprofen"; "should not be given to patients who have experienced asthma, urticaria, or allergic-type reactions
    after taking aspirin or other NSAIDs". Used because N18 gives "1 tab" without a strength and no daily maximum.
14. **Promethazine 25 mg dose (LBL03, Nausea and Vomiting).** "The average effective dose of promethazine hydrochloride
    for the active therapy of nausea and vomiting in children or adults is 25 mg. ... 12.5 mg to 25 mg doses may be
    repeated, as necessary, at four- to six-hour intervals." Contraindications: "individuals known to be hypersensitive
    or to have had an idiosyncratic reaction to promethazine or to other phenothiazines". Used for the single dose in
    milligrams, which N18 gives only as tablets; the 24-hour maximum stays NASA's (item 4).

## 1.2 Values the engine uses, with their origin

| Rule | Value | Origin |
|---|---|---|
| `apap_pain` dose | 650 mg (2 tablets of 325 mg) | LBL01 Directions |
| `apap_pain` max per administration | 650 mg | LBL01 Directions ("take 2 tablets") |
| `apap_pain` max per 24 h | 3,250 mg | LBL01 Liver warning |
| `apap_pain` min interval | 4 h (14,400 s) | LBL01 Directions; N18 p. 5-3 "every 4-6 hr" agrees |
| `apap_pain` course | 10 days (864,000 s) | LBL01 Directions |
| `ibu_pain` dose and max per administration | 400 mg | LBL02 Dosage and Administration |
| `ibu_pain` max per 24 h | 3,200 mg | LBL02 Dosage and Administration |
| `ibu_pain` min interval | 4 h | LBL02; N18 p. 5-3 agrees |
| `pmz_nausea` dose and max per administration | 25 mg | LBL03; RT04 p. 7 "25-50mg" agrees at the low end |
| `pmz_nausea` max per 24 h | 100 mg | N18 p. 6-10 |
| `pmz_nausea` min interval | 4 h | N18 pp. 4-38, 4-39; LBL03 agrees |

Absent on purpose, so the engine refuses or states the gap instead of inventing a value:

- `min_effective` for every ingredient: no source gives a minimum effective single amount. It is `null`; the
  `potency_shortfall` alert then says the minimum effective amount is not sourced, and never says "effective".
- `max_cumulative`: no source gives a course or mission total for these rules; `null`.
- `catastrophic_limits`: no source gives a per-administration catastrophic amount for these three ingredients (LBL01's
  "more than 4,000 mg of acetaminophen in 24 hours" is a 24-hour figure, and 3,250 mg already binds). None is listed,
  so `max_per_administration` is the only single-dose cap.
- Weight-based rules: no source above gives a per-kilogram adult dose, so no rule has `dose_per_kg` and `weight` is
  `null`. A per-kilogram rule added later is refused by check M-L5 until `weight` bounds with sources exist.
- Every other formulary product (aspirin, amoxicillin and clavulanate, promethazine injection, both epinephrine
  products, ascorbic acid, levofloxacin) has no rule. N18 gives doses for some of them (for example Augmentin "1 tab 2X/
  day for 7 days" on p. 2-6 and Phenergan injectable "Inject 1/2 to 1 cc intramuscularly every 6 hr" on p. 4-39), but
  without the flown strength or a daily maximum for the products in config/pharmacy/formulary.json, so their rules are
  left for clinical review. `listTreatmentOptions` shows only reviewed-formulary rules; a manual administration stays
  available for them (E32 4.4), still bounded by stock and recorded outside the engine.

## 1.3 Alerts and their origin

| Alert | Trigger | Origin |
|---|---|---|
| `allergy_same_ingredient` | documented allergy to the ingredient | LBL01, LBL02, LBL03 contraindications |
| `allergy_class` and `cross_sensitivity` | aspirin or NSAID allergy for ibuprofen; phenothiazine allergy for promethazine | N18 p. 6-9; LBL02; LBL03 |
| `condition_caution` | epistaxis (ICL38, ICL93) for ibuprofen | N18 p. 4-40 |
| `organ_caution` | hepatic impairment for acetaminophen | LBL01 "Ask a doctor before use if the user has liver disease" |
| `interaction` | promethazine with zolpidem, diphenhydramine, loratadine, meperidine, fentanyl, morphine, temazepam, diazepam, hydrocodone (the N18 p. 6-2 list by ingredient); acetaminophen with warfarin | N18 p. 6-2; LBL01 |
| `duplicate_ingredient` and `outside_medicine_overlap` | another medicine with the same ingredient in the context | LBL01 "Do not use with any other drug containing acetaminophen" |
| `consult_ground_first` | asterisked product | N18 p. 6-2 |
| `spaceflight_caution` | "w/food and drink" for ibuprofen | N18 p. 5-3 |

The level of each alert (`override_required` for allergy, contraindicated interaction, organ and condition caution,
outside overlap and clock alerts, with `override_roles` `[crew_medical_officer]`) is the design's team default
(E32 3.7) with `to_be_set_by` "clinical review"; no source above sets an alert level.

## 1.4 Refusal codes

The closed vocabulary of E32 3.5 and 3.6. Each refusal is reachable from its minimal input in E32-T-53:
`no_working_diagnosis`, `rule_not_found`, `rule_not_reviewed_live`, `formulary_mismatch`, `allergy_list_unknown`,
`patient_context_missing`, `weight_missing`, `weight_stale`, `weight_implausible`, `weight_change_unconfirmed`,
`unit_incompatible`, `clock_discontinuity`, `min_interval_not_passed`, `course_ended`, `no_usable_lot`,
`potency_adjustment_disabled`, `adjustment_not_permitted`, `no_deliverable_amount`,
`per_administration_max_exceeded`, `period_max_exceeded`, `cumulative_max_exceeded`, `catastrophic_limit`,
`insufficient_stock`, `history_too_large`.

## 1.5 Why the medication context is structured

N18 names every medicine twice and needs an index to join the names: the brand entry "MOTRIN (ibuprofen)" on p. 6-9
and the generic line "ibuprofen: See MOTRIN" on p. 6-7, the same for Tylenol and acetaminophen (pp. 6-2 and 6-12).
An allergy written as "Motrin" therefore never matches a product whose ingredient is `ibuprofen`, and an aspirin
allergy has to reach ibuprofen through a class (N18 p. 6-9, LBL02). So allergies, other medicines, organ function and
conditions are recorded as ingredient, class and condition IDs (R-E32-14), and a part that was never recorded is
`unknown`, which refuses (`allergy_list_unknown`, `patient_context_missing`) instead of counting as "none".

## 1.6 Citations in docs/tdd/E32.md

The design cites numbered items of the lost original. They map to this note as follows: "dosing-safety 1.1" (UCUM
and ISMP display) to section 1.1 and E32 3.5 step 8; "1.1.5 to 1.1.10" (context parts, weight, overlap) to sections
1.3 and 1.5; "1.1.7" (name matching misses allergies) to section 1.5; "1.4" (refusal codes) to section 1.4.
