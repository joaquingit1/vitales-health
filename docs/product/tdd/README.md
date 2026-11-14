# Technical design document backlog

These 33 briefs define the scope and decisions for later TDDs. They are not completed TDDs. All epics are in the hackathon release. E23 to E26 build the all-in-one dashboard (environment and habitat sensors, warnings, routine, Someone needs help) against [CONTRACTS.md](../CONTRACTS.md) sections 11 to 20 and `api/openapi.yaml`; E27, the kit map, against section 21.

- [E01: Platform, identities and durable records](E01.md): stream A, S0; dependencies none.
- [E02: Consent, authentication and privacy](E02.md): stream A, S1; dependencies E01.
- [E03: Crew experience and daily check](E03.md): stream D, S1; dependencies E01, E02.
- [E04: Guided complaint intake](E04.md): stream B, S2; dependencies E01, E02, E03.
- [E05: Capabilities and measurement workflow](E05.md): stream A, S2; dependencies E01, E02.
- [E06: Personal and station cameras](E06.md): stream C, S2; dependencies E01, E02, E05.
- [E07: Oximeter and clinical device adapters](E07.md): stream C, S2; dependencies E05.
- [E08: Wrist activity band and sleep pipeline](E08.md): stream C, S2; dependencies E01, E05.
- [E09: Reports and specialist findings](E09.md): stream A, S2; dependencies E01, E02, E05.
- [E10: NASA registry and evidence database](E10.md): stream B, S0; dependencies E01.
- [E11: Urgent handling and pathway engine](E11.md): stream B, S1; dependencies E01, E10.
- [E12: Retrieval and typed tool gateway](E12.md): stream C, S3; dependencies E02, E05, E09, E10.
- [E13: Local reasoning and assessment](E13.md): stream C, S3; dependencies E04, E11, E12.
- [E14: Trends, environment and Chronos-2](E14.md): stream C, S3; dependencies E01, E05, E08.
- [E15: Episodes, follow-up and clinical review](E15.md): stream B, S3; dependencies E04, E05, E11.
- [E16: Ground console and delayed handoff](E16.md): stream A, S3; dependencies E02, E09, E15.
- [E17: Model integration and tool-use optimization](E17.md): stream C, S4; dependencies E10, E12, E13.
- [E18: HealthBench and clinical evaluation](E18.md): stream D, S0; dependencies E01.
- [E19: Research and content workbench](E19.md): stream B, S4; dependencies E10, E18.
- [E20: Accessibility, resilience and packaging](E20.md): stream D, S4; dependencies E03, E06, E08, E13, E14, E16.
- [E21: Integration, evidence and demonstration](E21.md): stream D, S5; dependencies E07, E09, E15, E17, E18, E19, E20, E22, E23, E24, E25, E26.
- [E22: Participation streak, patches and crew goal](E22.md): stream D, S2; dependencies E01, E03.
- [E23: Environment profile and habitat sensors](E23.md): stream C (Habitat screens by D), S2; dependencies E01, E05, E10.
- [E24: Warnings engine](E24.md): stream B (warning screens by D), S2; dependencies E01, E11, E23.
- [E25: Routine scheduler and task guidance](E25.md): streams D and B, S2; dependencies E01, E03, E23, E24.
- [E26: Someone needs help](E26.md): streams B and D, S3; dependencies E03, E11, E13, E16, E24.
- [E27: Kit map: what this ship can test](E27.md): streams B, A, C and D, S2; dependencies E01, E05, E10, E23.
- [E28: Onboard plans: no dead ends](E28.md): streams B, A, C and D, S2; dependencies E10, E26, E27.
- [E29: The ground as a reader](E29.md): streams A, B and D, S2; dependencies E16, E23.
- [E30: Teach and check](E30.md): streams C, A, B and D, S3; dependencies E06, E07, E28.
- [E31: Who helps](E31.md): streams A, B and D, S3; dependencies E01, E22, E25, E26.
- [E32: Treat here: supplies and planning](E32.md): streams A, B and D, S3; dependencies E27, E28.
- [E33: Stay or return](E33.md): streams B, A and D, S4; dependencies E28.
