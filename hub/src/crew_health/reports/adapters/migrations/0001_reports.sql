CREATE TABLE reports_report (
 report_id TEXT PRIMARY KEY,
 owner_person_id TEXT NOT NULL,
 current_entry_id TEXT NOT NULL,
 source_entry_id TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>=1),
 deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1))
) STRICT;
CREATE INDEX reports_report_owner ON reports_report(owner_person_id,report_id);
CREATE TABLE reports_version (
 entry_id TEXT PRIMARY KEY,
 report_id TEXT NOT NULL REFERENCES reports_report,
 revision INTEGER NOT NULL CHECK(revision>=1),
 version_id TEXT NOT NULL,
 owner_person_id TEXT NOT NULL,
 command_key TEXT NOT NULL,
 fingerprint TEXT NOT NULL,
 key_id TEXT NOT NULL,
 ciphertext_sha256 TEXT NOT NULL,
 deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)),
 UNIQUE(report_id,revision),
 UNIQUE(owner_person_id,command_key)
) STRICT;
CREATE TABLE reports_confirmation (
 owner_person_id TEXT NOT NULL,
 command_key TEXT NOT NULL,
 report_id TEXT,
 request_digest TEXT NOT NULL,
 applied INTEGER NOT NULL DEFAULT 0 CHECK(applied IN (0,1)),
 PRIMARY KEY(owner_person_id,command_key)
) STRICT;
CREATE TABLE reports_finding (
 finding_id TEXT PRIMARY KEY,
 owner_person_id TEXT NOT NULL,
 command_key TEXT NOT NULL,
 seq INTEGER NOT NULL CHECK(seq>=0),
 report_id TEXT,
 draft_id TEXT,
 patient_id TEXT NOT NULL,
 code TEXT NOT NULL,
 value_kind TEXT NOT NULL CHECK(value_kind IN ('number','text')),
 value TEXT NOT NULL,
 unit TEXT,
 observed_on TEXT,
 origin TEXT NOT NULL CHECK(origin IN ('clinician','device','extraction')),
 role TEXT NOT NULL CHECK(role IN ('result','interpretation')),
 origin_mode TEXT NOT NULL CHECK(origin_mode IN ('LIVE','REPLAY','SIM')),
 confirmer_id TEXT NOT NULL,
 confirmed_at TEXT NOT NULL,
 report_revision INTEGER,
 source_version_id TEXT,
 attributes TEXT NOT NULL,
 reference_low TEXT,
 reference_high TEXT,
 record_ref TEXT,
 UNIQUE(owner_person_id,command_key,seq),
 CHECK((value_kind='number')=(unit IS NOT NULL))
) STRICT;
CREATE INDEX reports_finding_patient ON reports_finding(patient_id,code,finding_id);
