"""Medicine potency (chemical degradation) engine for the Crew Health Check hub.

The engine estimates remaining active ingredient per lot from the storage history the
station records, with uncertainty, and returns a decision per lot. The contract lives in
docs/ENGINE-SPEC.md; configuration lives in config/pharmacy and validates against the JSON
Schemas in contracts/pharmacy.
"""

__version__ = "0.1.0"
