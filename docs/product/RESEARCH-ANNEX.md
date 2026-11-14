# Research and documentation annex

## 1. What this annex preserves

This project has a substantial research history. The archive is not being replaced with a short bibliography. The existing [searchable research library](sources/recovered/research-library.html), [combined source index](sources/recovered/all-sources.csv), original research notes and raw Exa outputs remain the provenance record.

The recovered archive contains **3,432 normalized unique URLs**, including **2,859 recovered previous-source URLs**. The recovery includes the original 202 Exa source URLs, archived search/fetch outputs and eight research notes. The newer NASA collection contains 580 URLs with overlap against the old set; 502 were catalog PDF links, not 502 independently read papers. Counts are catalog counts, not a claim that every page was read, verified, licensed for reuse or clinically relevant. The recovery cannot establish that every source from every unknown agent conversation was found.

The previous research recovery recorded 251 web-search and 131 web-fetch responses from ten sessions, plus the original Exa batches. Keep the [archive manifest](sources/recovered/manifest.json) as the authority for recovery details. The research notes distinguish primary verification, secondary/search information, background knowledge and inference. Those labels must survive ingestion.

New searches for the LLM architecture used **Exa first**, with eight batches saved as `sources/P01…P08`. Primary pages were then inspected for the model, HealthBench, tool evaluation and runtime capabilities. Search snippets are discovery evidence; they are not a substitute for reading the primary document. The new source registry records the actual access level.

**Latest model decision:** HuatuoGPT-3-9B is the model, with its existing medically adapted weights. Additional fine-tuning was considered in an early draft of this package, then removed after Joaquin clarified the intent. All implementation work now focuses on tools, retrieval, context, verification and evaluation.

## 2. The source of truth

This package is the working plan for the event. It covers the expanded assessment system, research tooling and evaluation: the pitch and the loop, gamification (streak, patches, crew goal), the brief's five questions on every measurement card, the reaction test, the cabin CO2 replay, HuatuoGPT-3-9B as the model and the M3/16 GB laptop target.

***REMOVED*** [PITCH.md](PITCH.md) wins on the story, the demo and what we show; the [PRD](PRD.md) owns product behavior.

| Source | Role in this package |
|---|---|
| This package | The working plan for the event and the single source of truth |
| User instructions | Full hackathon scope; four agentic developers; no time-based cuts; broad assessment, LLM architecture and tool-use optimization plan; then the pitch, gamification and the visual direction restored |

The shared document has no literal HealthBench reference in its exported text. The separate copy cites the publisher's HealthBench score and explicitly says our own system has not been evaluated. The new PRD makes HealthBench a named evaluation requirement rather than treating a publisher score as our result.

New product decisions are recorded explicitly in the PRD and architecture decision list.

## 3. NASA condition and risk sources

The complete list of NASA documents and data, with what each does in the product and the full catalog of 580 documents the research found, is in [NASA-SOURCES.md](NASA-SOURCES.md). This section explains how far each kind of NASA source can be trusted.

The anchor is [HRP-48036 Rev B: ExMC Evidence Library Methods](https://ntrs.nasa.gov/api/citations/20240004774/downloads/HRP-48036%20RevB_ExMC%20Evidence%20Library%20Methods.pdf). Its 119 active condition entries and the broader inventories were extracted, preserving PDF-page references. Its lists support condition membership and modeling scope. They do not establish clinical prevalence, a validated diagnostic algorithm or that the current laptop can detect each condition.

[Appendix E of NASA's health and medical care requirements](https://www.nasa.gov/reference/appendix-e-health-and-medical-care-technical-requirements/) provides an additional capability-planning view. [Integrated Medical Model documentation](https://www.nasa.gov/integrated-medical-model-documentation/) links condition-level CLiFFs and related documentation. [Human Research Roadmap evidence](https://humanresearchroadmap.nasa.gov/Evidence/) and [human-system risks](https://www.nasa.gov/hhp/human-system-risks/) provide risk context, including hazards that extend beyond a list of clinical diagnoses.

The [medical operations and clinical care references](https://www.nasa.gov/reference/medical-operations-and-clinical-care/) and [fundamentals of human health](https://www.nasa.gov/reference/fundamentals-of-human-health/) link technical briefs. Their status, audience and reference-only limitations matter. Do not turn a statement about a spacecraft's medical capability into personal medical advice without an appropriate reviewed pathway.

| Source family | Best use | What it does not establish |
|---|---|---|
| Condition inventories | Taxonomy and coverage audit | Disease frequency or diagnostic confirmation |
| CLiFFs and model documentation | Modeled resources, outcomes and assumptions | A directly deployable clinical protocol |
| NASA technical briefs | Spaceflight context and relevant examinations | Validation of our implementation |
| NASA standards | Applicable system and capability requirements | Blanket compliance of a prototype |
| HRR evidence reports | Risks, mechanisms, research gaps | Individual causal diagnosis |
| Primary clinical guidance | Indicative workup and clinical principles | Direct transfer of Earth thresholds to every mission |
| Device documentation | Intended measurement, protocol and limitations | Accuracy of our adapted acquisition pipeline |

## 4. What the assessment matrices mean

[condition-assessments-119.csv](condition-assessments-119.csv) contains one row per active ICL entry. Each row provides optional context, indicative clinical assessment/tests, equipment requirements, current-stack support and limitations. These are planning syntheses, not approved medical instructions. General clinical references support selected workup principles; the NASA page link establishes list membership, not every test listed.

[expanded-inventory-assessments-792.csv](expanded-inventory-assessments-792.csv) retains all 450 master, 92 proposed, 184 remaining and 66 removed/consolidated rows. Exact normalized-title matches reuse active-condition assessment text. Other rows are provisionally routed to a family; this is a product-planning classification and requires condition-level clinical review. Unresolved entries remain explicitly assigned to specialist review rather than receiving an invented test panel.

The 911 total rows overlap. Pregnancy, risk states, exposure events and supportive-care concepts are not all pathologies. Removed entries can have valid clinical meaning while no longer being separate active modeling entries. Preserve these distinctions in the database and in any coverage claim.

The mapping is broad enough to plan capture and review for the whole expanded inventory. It is **not exhaustive clinical protocol authoring for all medicine**. The content workbench and backlog include the review needed to promote individual pathways. No clinical review is claimed to have occurred during this planning work.

## 5. Primary sources checked for selected workup principles

| Topic | Source | Product implication |
|---|---|---|
| Arrhythmia | [NHLBI diagnosis](https://www.nhlbi.nih.gov/health/arrhythmias/diagnosis) | ECG/monitoring evidence differs from a pulse measurement |
| Acute coronary syndrome context | [NHLBI heart attack diagnosis](https://www.nhlbi.nih.gov/health/heart-attack/diagnosis) | Urgent evaluation and appropriate objective tests; do not wait for a camera retry |
| Stones | [NIDDK diagnosis](https://www.niddk.nih.gov/health-information/urologic-diseases/kidney-stones/diagnosis) | Capture urine/blood/imaging results rather than claiming symptom-only confirmation |
| Urinary retention | [NIDDK diagnosis](https://www.niddk.nih.gov/health-information/urologic-diseases/urinary-retention/diagnosis) | Record postvoid-residual assessment and unavailable equipment |
| Glaucoma | [NEI detection statement](https://www.nei.nih.gov/about/education-and-outreach/glaucoma-resources/national-eye-institute-statement-detection-glaucoma-and-adult-vision-screening) | A comprehensive eye assessment matters; pressure alone is insufficient |
| Radiation | [HHS REMM](https://remm.hhs.gov/ars.htm) | Dosimetry and clinical/laboratory assessment are distinct from wearable vitals; captured page was partial |
| Appendicitis | [NIDDK diagnosis](https://www.niddk.nih.gov/health-information/digestive-diseases/appendicitis/diagnosis) | A complaint may need examination, laboratory tests and imaging |
| Depression | [NIMH overview](https://www.nimh.nih.gov/health/publications/depression) | Clinical assessment goes beyond a mood slider |
| Bone health | [NIAMS diagnosis](https://www.niams.nih.gov/health-topics/osteoporosis/diagnosis-treatment-and-steps-to-take) | Separate bone-density findings from exercise adherence |
| SANS | [NASA technical brief](https://www.nasa.gov/wp-content/uploads/2024/02/ochmo-mtb-001-sans.pdf) | Specialist ocular findings and baseline comparison need their own capture path |

These references do not comprehensively source every rare-condition workup. The source registry and matrices mark the remaining content-review work. Equipment, operator training and mission applicability must be specified before a procedure is offered as actionable clinical guidance.

### Sources behind the plan aboard and teach and check

Non-NASA sources for [PRD](PRD.md) section 4.13, read on 14 November 2026. Their text is not yet saved in the package; the NASA sources for the same work are in [NASA-SOURCES.md](NASA-SOURCES.md), section 1.

| Topic | Source | Product implication |
|---|---|---|
| Checking a pulse | [British Heart Foundation, Checking your pulse](https://www.bhf.org.uk/informationsupport/tests/checking-your-pulse) (published 18 October 2024): finger placement, pressure, counting for 30 or 60 seconds, and "If your pulse feels irregular, you should check for a full 60 seconds." | The teach steps of the wrist pulse check and its 60-second count ([DEMO-CONDITIONS.md](DEMO-CONDITIONS.md), C29) |
| What a pulse check can tell | [British Heart Foundation, Your heart rate](https://www.bhf.org.uk/informationsupport/how-a-healthy-heart-works/your-heart-rate): "You may be able to tell if you have a normal or irregular heart rate by checking your pulse" | The check changes the watch stage of a plan, never the diagnosis, and it cannot replace an ECG (C30) |
| Compression rate | [American Heart Association 2025, Adult Basic Life Support](https://cpr.heart.org/en/resuscitation-science/cpr-and-ecc-guidelines/adult-basic-life-support): rate 100 to 120 a minute, depth at least 5 cm, feedback devices "reasonable" | The band for the camera's compression rate line; depth is never measured. The page blocks automated access and was read in a browser; [RESPONDER-FLOWS.md](RESPONDER-FLOWS.md) quotes the Canadian edition of the AHA highlights (RS03) |
| A camera that counts compressions | [Engan and others, 2016](https://pubmed.ncbi.nlm.nih.gov/27516194/) and [Meinich-Bache and others, 2018](https://pubmed.ncbi.nlm.nih.gov/30581549/): a smartphone camera detected bystander compression rate on a manikin, with an average error of "2.7 compressions per minute" in 2018 | Precedent only: rate, on a manikin, in Earth gravity. The product's CPR rate stays Experimental until the venue check against a metronome |
| CPR in microgravity and reduced gravity | [Hinkelbein and others, 2020](https://pubmed.ncbi.nlm.nih.gov/33138865/) (RS04) and [Overbeek and others, 2022](https://pubmed.ncbi.nlm.nih.gov/36556323/): in reduced gravity "the depth of compression quickly falls below the guideline level" | Chest compression technique in partial gravity is left to clinical review ([PRD](PRD.md) section 13) |
| Just-in-time training in flight | [Foale and others, 2005](https://pubmed.ncbi.nlm.nih.gov/15945407/): non-physician crew "performed with high accuracy following limited preflight training and multimedia based in-flight review" while "following commands from the ground-based expert" | Teaching a non-physician works with an expert watching; teach and check puts the camera check where the remote expert was, and only for checks a camera can verify |
| Evacuation from the Moon | [Levin and others, 2023](https://pmc.ncbi.nlm.nih.gov/articles/PMC10810302/): lunar "evacuation back to Earth may take as long as 2 weeks" | A return from the Moon takes days to weeks, so every plan runs aboard first and the return stage is a decision for the commander and the medical officer |
| NASA's medical AI, as industry describes it | [Google Cloud, 7 August 2025](https://cloud.google.com/blog/topics/public-sector/how-google-and-nasa-are-testing-ai-for-medical-care-in-space) ("proof-of-concept") and [Red Hat, 24 June 2026](https://www.redhat.com/en/blog/moon-and-beyond-ramalama-being-tested-nasa-potentially-support-medical-ai-assistant-future-deep-space-missions) ("to a fully disconnected, edge deployment") on the Crew Medical Officer Digital Assistant | The pitch positions the product beside CMO-DA and never competes on its diagnostic accuracy |

## 6. LLM evidence and limits

[HuatuoGPT-3-9B's current model card](https://huggingface.co/FreedomIntelligence/HuatuoGPT-3-9B) supports its identity, backbone and thinking-mode requirement. It does not prove that our local quantization, tool template or orchestration will perform equally well. [llama.cpp function-calling documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md) establishes tool-format support in the runtime, with template-specific considerations. Actual candidate compatibility must still be tested.

[HealthBench](https://openai.com/index/healthbench/) and its [official harness](https://github.com/openai/simple-evals/blob/main/healthbench_eval.py) support the conversation/rubric evaluation track. [MedAgentBench](https://github.com/stanfordmlgroup/MedAgentBench) and [AgentClinic](https://github.com/samuelschmidgall/agentclinic) address different aspects of simulated clinical tool use and interactive assessment. Their task definitions and constraints must be preserved when reporting their scores.

[Medical RAG research](https://arxiv.org/abs/2402.13178) motivates measuring corpus and retrieval choices rather than assuming retrieval always helps. [MLX LM](https://github.com/ml-explore/mlx-lm) documents local inference and fine-tuning capabilities in general. The architecture's exact chunk sizes, tool grouping, development-case mixture, loop budget and model selection are **our proposed experiments**, not findings established by those sources.

## 7. Research questions

### Open, to close during implementation

1. Which condition-specific CLiFFs and technical briefs have actually been read, and which are still catalog links?
2. Which clinical pathways and urgent wording have an appropriate reviewer and mission applicability?
3. Which source versions are current and licensed for redistribution in an offline bundle?
4. Which actual devices are available, with documented interfaces and operator requirements?
5. Does the 9B candidate reliably combine thinking, structured output and tools in the pinned runtime?
6. Does retrieval improve the target cases, or introduce irrelevant/conflicting guidance?
7. Do improved tool interfaces, context and prompts improve information gathering without degrading medical reasoning or uncertainty handling?
8. How much performance changes after quantization and constrained local inference?
9. Do the station cameras and band add useful accepted information beyond the personal check?
10. Does Chronos improve a defined forecasting metric beyond simple baselines, without implying diagnostic value?

### Closed: the official 2026 rules

Read on 14 November 2026 from the [Project Submission Guide](https://www.spaceappschallenge.org/resources/project-submission-guide/) and the [Judging and Awards Guide](https://www.spaceappschallenge.org/resources/judging-awards-guide/), with the [Participant FAQ](https://www.spaceappschallenge.org/resources/participant-faqs/) cited alongside them in [PITCH.md](PITCH.md).

- **Submission window:** opens Saturday 14 November at 09:00 and closes Sunday 15 November at 23:59, local event time.
- **Demo format:** a video of 30 seconds or less, or at most seven slides including the title and introduction. Over the limit means not eligible for Global Judging.
- **Judging:** Impact, Creativity, Validity, Relevance and Presentation, each scored 1 to 5.
- **Required fields:** project name, high-level summary, demo link, final project link, detailed description, use of SI (AI), NASA data, Space Agency partner data and references.
- **NASA data:** at least one NASA data or resource is required for a Global Award.
- **Language and likeness:** English; no likeness of anyone under 18.
- **Generated content:** no NASA branding; AI-generated images and video need a visible watermark; AI-generated code and data are acknowledged in text and metadata.
- **AI and the idea:** "SI should not be used to come up with your idea of how to solve the challenge."
- **Timing of work:** no work on the challenge before the hackathon starts.

## 8. Documentation package and maintenance

[PITCH.md](PITCH.md) owns the story, the demo and what we show. The [PRD](PRD.md) owns product behavior. [UX design](UX-DESIGN.md) owns interaction semantics. [Contracts](CONTRACTS.md) own shared state and data. [LLM architecture](LLM-ARCHITECTURE.md) owns the reasoning/retrieval/tool design. [Validation](VALIDATION.md) owns evaluation definitions. [Backlog](backlog.csv) and [TDD briefs](tdd/README.md) translate those commitments into implementation work.

A documentation release must include: the decision log; architecture diagrams; device/adapter manuals; schema and migration reference; clinical-content review status; source and license manifest; model/data/evaluation cards; deployment and recovery runbook; privacy/consent behavior; known limitations; and demonstration capability manifest. Each item has a backlog owner. The research archive remains separate from the approved runtime corpus, and both are versioned.
