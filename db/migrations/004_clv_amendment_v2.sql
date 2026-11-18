-- AMEND-003: CLV estimand v2 close states, shadow ledger, and legacy LINE_CHANGED guard.

ALTER TABLE closing_references
  ADD COLUMN IF NOT EXISTS close_state TEXT,
  ADD COLUMN IF NOT EXISTS closing_snapshot_v2_sha256 TEXT,
  ADD COLUMN IF NOT EXISTS clv_line_component NUMERIC,
  ADD COLUMN IF NOT EXISTS clv_juice_component NUMERIC,
  ADD COLUMN IF NOT EXISTS clv_book_execution_component NUMERIC;

ALTER TABLE closing_references
  DROP CONSTRAINT IF EXISTS closing_references_close_state_chk;

ALTER TABLE closing_references
  ADD CONSTRAINT closing_references_close_state_chk
  CHECK (
    close_state IS NULL
    OR close_state IN ('CLOSE_EXACT', 'CLOSE_TRANSLATED', 'CLOSE_UNEVALUABLE')
  );

-- Guard: historical LINE_CHANGED rows and close_state values cannot be mutated.
CREATE OR REPLACE FUNCTION closing_references_protect_legacy_line_changed() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.reason_code = 'LINE_CHANGED' THEN
    RAISE EXCEPTION 'amendment guard: historical LINE_CHANGED row % cannot be mutated', OLD.id;
  END IF;
  IF OLD.close_state IS DISTINCT FROM NEW.close_state
     OR OLD.reason_code IS DISTINCT FROM NEW.reason_code THEN
    RAISE EXCEPTION 'amendment guard: close_state/reason_code on closing_references row % is immutable', OLD.id;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS closing_references_protect_legacy_update ON closing_references;
CREATE TRIGGER closing_references_protect_legacy_update
  BEFORE UPDATE ON closing_references
  FOR EACH ROW
  EXECUTE FUNCTION closing_references_protect_legacy_line_changed();

-- Shadow ledger for non-confirmatory licensed books (cannot unfreeze paid access).
CREATE TABLE IF NOT EXISTS shadow_ledger (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  signal_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  market TEXT NOT NULL,
  selection TEXT NOT NULL,
  quote_source TEXT NOT NULL,
  book TEXT NOT NULL,
  source_ts TIMESTAMPTZ NOT NULL,
  entry_line NUMERIC NOT NULL,
  entry_american_odds INTEGER NOT NULL,
  entry_devig_p NUMERIC NOT NULL,
  best_tape_devig_p NUMERIC,
  close_state TEXT NOT NULL CHECK (close_state IN ('CLOSE_EXACT', 'CLOSE_TRANSLATED', 'CLOSE_UNEVALUABLE')),
  line_component NUMERIC,
  juice_component NUMERIC,
  book_execution_component NUMERIC,
  closing_snapshot_v2_sha256 TEXT,
  can_unfreeze_paid_access BOOLEAN NOT NULL DEFAULT FALSE CHECK (can_unfreeze_paid_access = FALSE),
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION shadow_ledger_reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'append-only shadow_ledger: % is rejected', TG_OP;
END;
$$;

DROP TRIGGER IF EXISTS shadow_ledger_reject_update ON shadow_ledger;
CREATE TRIGGER shadow_ledger_reject_update
  BEFORE UPDATE ON shadow_ledger FOR EACH ROW EXECUTE FUNCTION shadow_ledger_reject_mutation();

DROP TRIGGER IF EXISTS shadow_ledger_reject_delete ON shadow_ledger;
CREATE TRIGGER shadow_ledger_reject_delete
  BEFORE DELETE ON shadow_ledger FOR EACH ROW EXECUTE FUNCTION shadow_ledger_reject_mutation();

DROP TRIGGER IF EXISTS shadow_ledger_reject_truncate ON shadow_ledger;
CREATE TRIGGER shadow_ledger_reject_truncate
  BEFORE TRUNCATE ON shadow_ledger FOR EACH STATEMENT EXECUTE FUNCTION shadow_ledger_reject_mutation();
