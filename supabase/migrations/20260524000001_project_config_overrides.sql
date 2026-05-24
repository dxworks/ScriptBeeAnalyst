-- Per-project enrichment-config overrides. Each row carries a JSON dict
-- {field_name: value} that overlays the global EnrichmentConfig defaults
-- at build time. Mirrors the projects.component_mapping pattern but lives
-- in its own table because the payload is open-ended (~80 fields) and
-- benefits from its own updated_at for "last edited" UX.
--
-- Shape:
--   overrides = {
--     "bugmagnet_min_bugfix_commits": 8,
--     "hibernator_min_lifetime_commits": 10,
--     "polarised_top_share": 0.75,
--     "resolved_status_categories": ["done", "closed", "wontfix"],
--     ...
--   }
-- Only fields the user EXPLICITLY edited appear. Missing fields fall
-- through to EnrichmentConfig's class-level default. An empty {} is
-- semantically identical to "no row" — the merge step is a no-op.
--
-- Single row per project; the project_id is both PK and FK so the row
-- cascades on project delete and there is no insert-vs-update branch
-- (the repository upserts).
--
-- No RLS in local single-tenant mode (matches project_filter_rules).
-- Realtime is disabled — saves are explicit and the UI re-reads on demand.

create table public.project_config_overrides (
  project_id uuid primary key references public.projects(id) on delete cascade,
  overrides jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create or replace function public.touch_project_config_overrides()
returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

create trigger project_config_overrides_touch
before update on public.project_config_overrides
for each row execute function public.touch_project_config_overrides();
