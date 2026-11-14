# CliFF confirming tests: method

**Sources.** NASA's Integrated Medical Model documentation page (S13, https://www.nasa.gov/integrated-medical-model-documentation/), fetched 2026-11-14T18:56:22Z (SHA-256 d708dfbf266db484add44e7ca311d83127d49dca5023444eceabb93da134434a). Its links match the 356 catalogued in NASA-SOURCES.md. 120 PDFs were downloaded one at a time, at least 12 seconds apart. Each URL, UTC time and SHA-256 is in manifest.jsonl, and every hash was recomputed from disk. `forms/ICL<n>.json` holds each form's extracted page text with its URL, SHA-256 and fetch time; the PDFs themselves are not committed.

**Matching rule.** Each condition maps to the form in the page's "CliFF 119" column, the set written for this 119 condition list. Every form names itself in its PDF title as "ICL<n>_<name>". All 118 titles match their condition_id.

**Extraction.** Text was extracted with pypdf. The tests come from "Diagnostic Scope and Resourcing" and the Clinical Phase 1 rationale, plus tests named on page 1 that the scope does not exclude. History, treatment steps and items the form calls unavailable go in notes. Quotes are verbatim after whitespace normalization, and each page is computed from where the quote occurs. No quote spans two pages.

**Counts.** 118 found, 0 not found, 1 ambiguous (ICL88). There are 439 confirming tests, and 105 conditions state a body region.

**Anomalies.** ICL88 has no CliFF 119 form, so the older 2020 Skin Infection CliFF is cited. The ICL1 link is labelled "Abdominal Injury" but points to the hernia form. ICL8 says there is "no availability of confirmatory testing". In some forms, tables interleave the text. Some forms reuse wording copied from other conditions (ICL97, ICL99, ICL104). Quotes from ICL3, ICL35 and ICL104 keep the form's own dashes.

**Independent check.** A second script, written separately, recomputed every SHA-256 from the PDFs and found all 575 quotes (439 tests, 136 body region quotes) on their cited pages.

**Use.** `confirming_tests` here are candidates: every test the diagnostic text names, flagged in `notes` when optional, differential only, worst case or set aside. COORDINATOR-DECISIONS Q26 says which of them become a kit map row's confirming tests.
