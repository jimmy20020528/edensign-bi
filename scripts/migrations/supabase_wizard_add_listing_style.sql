-- Supabase migration (run in the Supabase SQL editor).
--
-- The wizard tables (wizard_submissions, staging_runs) live in Supabase, not in
-- this repo's schema.sql / BI Postgres. The on-demand listing feature writes the
-- generated listing back to the submission row via:
--   _sb.from('wizard_submissions').update({listing_text, listing_style}).eq('id', id)
-- `listing_text` already exists; this adds `listing_style` so we record which
-- recommended style the stored listing was generated for.

ALTER TABLE wizard_submissions ADD COLUMN IF NOT EXISTS listing_style text;
