ALTER TABLE repl_log ADD COLUMN hlc_physical_ms INTEGER CHECK (hlc_physical_ms IS NULL OR hlc_physical_ms >= 0);
ALTER TABLE repl_log ADD COLUMN hlc_logical INTEGER CHECK (hlc_logical IS NULL OR hlc_logical >= 0);
