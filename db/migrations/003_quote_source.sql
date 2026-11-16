-- Quote source on entry and close. Existing rows are tagged from the book
-- name when that name is a known sportsbook, Kalshi, or Polymarket.
-- Everything else stays 'unknown'. Tags are not pooled across classes.

ALTER TABLE intended_quotes
  ADD COLUMN IF NOT EXISTS quote_source TEXT NOT NULL DEFAULT 'unknown';

ALTER TABLE placements
  ADD COLUMN IF NOT EXISTS quote_source TEXT NOT NULL DEFAULT 'unknown';

ALTER TABLE closing_references
  ADD COLUMN IF NOT EXISTS quote_source TEXT NOT NULL DEFAULT 'unknown';

UPDATE intended_quotes
SET quote_source = CASE
  WHEN lower(coalesce(book, '')) LIKE '%kalshi%' THEN 'kalshi'
  WHEN lower(coalesce(book, '')) LIKE '%polymarket%' THEN 'polymarket'
  WHEN lower(coalesce(book, '')) LIKE '%fanduel%' THEN 'odds_api:fanduel'
  WHEN lower(coalesce(book, '')) LIKE '%draftkings%' THEN 'odds_api:draftkings'
  WHEN lower(coalesce(book, '')) LIKE '%betmgm%' OR lower(coalesce(book, '')) LIKE '%mgm%' THEN 'odds_api:betmgm'
  WHEN lower(coalesce(book, '')) LIKE '%caesars%' THEN 'odds_api:caesars'
  WHEN lower(coalesce(book, '')) LIKE '%pinnacle%' THEN 'odds_api:pinnacle'
  WHEN lower(coalesce(book, '')) LIKE '%bovada%' THEN 'odds_api:bovada'
  ELSE 'unknown'
END
WHERE quote_source = 'unknown' AND book IS NOT NULL AND btrim(book) <> '';

UPDATE placements
SET quote_source = CASE
  WHEN lower(coalesce(book, '')) LIKE '%kalshi%' THEN 'kalshi'
  WHEN lower(coalesce(book, '')) LIKE '%polymarket%' THEN 'polymarket'
  WHEN lower(coalesce(book, '')) LIKE '%fanduel%' THEN 'odds_api:fanduel'
  WHEN lower(coalesce(book, '')) LIKE '%draftkings%' THEN 'odds_api:draftkings'
  WHEN lower(coalesce(book, '')) LIKE '%betmgm%' OR lower(coalesce(book, '')) LIKE '%mgm%' THEN 'odds_api:betmgm'
  WHEN lower(coalesce(book, '')) LIKE '%caesars%' THEN 'odds_api:caesars'
  WHEN lower(coalesce(book, '')) LIKE '%pinnacle%' THEN 'odds_api:pinnacle'
  WHEN lower(coalesce(book, '')) LIKE '%bovada%' THEN 'odds_api:bovada'
  ELSE 'unknown'
END
WHERE quote_source = 'unknown' AND book IS NOT NULL AND btrim(book) <> '';

CREATE OR REPLACE FUNCTION closing_match_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  quote_row intended_quotes%ROWTYPE;
  placement_row placements%ROWTYPE;
BEGIN
  IF NEW.match_status = 'UNMATCHED' THEN
    RETURN NEW;
  END IF;
  IF NEW.signal_id IS NOT NULL THEN
    SELECT * INTO quote_row FROM intended_quotes WHERE signal_id = NEW.signal_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'matched close requires the intended quote committed with the forecast';
    END IF;
    IF quote_row.event_id IS DISTINCT FROM NEW.event_id
       OR quote_row.market IS DISTINCT FROM NEW.market
       OR quote_row.selection IS DISTINCT FROM NEW.selection
       OR quote_row.line IS DISTINCT FROM NEW.line
       OR quote_row.settlement_rules IS DISTINCT FROM NEW.settlement_rules
       OR lower(btrim(quote_row.quote_source)) IS DISTINCT FROM lower(btrim(NEW.quote_source)) THEN
      RAISE EXCEPTION 'matched close must share event, market, selection, line, settlement rules, and quote source';
    END IF;
  END IF;
  IF NEW.placement_id IS NOT NULL THEN
    SELECT * INTO placement_row FROM placements WHERE id = NEW.placement_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'placement close is missing its placement';
    END IF;
    IF placement_row.event_id IS DISTINCT FROM NEW.event_id
       OR placement_row.market IS DISTINCT FROM NEW.market
       OR placement_row.selection IS DISTINCT FROM NEW.selection
       OR placement_row.achieved_line IS DISTINCT FROM NEW.line
       OR placement_row.settlement_rules IS DISTINCT FROM NEW.settlement_rules
       OR lower(btrim(placement_row.quote_source)) IS DISTINCT FROM lower(btrim(NEW.quote_source)) THEN
      RAISE EXCEPTION 'matched placement close must share event, market, selection, achieved line, settlement rules, and quote source';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;
