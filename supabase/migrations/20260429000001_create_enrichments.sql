-- Enrichment cache: one JSONB blob per project.
-- The data-server (re)computes the dx-style enrichment layer at project load
-- and via POST /projects/{id}/reenrich; this table avoids paying that cost
-- on every load. Keyed by project_id so re-runs upsert in place.

create table public.enrichments (
    project_id uuid primary key references public.projects(id) on delete cascade,
    payload jsonb not null,
    generated_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index enrichments_generated_at_idx on public.enrichments (generated_at);

alter table public.enrichments enable row level security;

create policy "Users can view own enrichments"
  on public.enrichments for select
  using (
    exists (
      select 1 from public.projects
      where projects.id = enrichments.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can upsert own enrichments"
  on public.enrichments for insert
  with check (
    exists (
      select 1 from public.projects
      where projects.id = enrichments.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can update own enrichments"
  on public.enrichments for update
  using (
    exists (
      select 1 from public.projects
      where projects.id = enrichments.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can delete own enrichments"
  on public.enrichments for delete
  using (
    exists (
      select 1 from public.projects
      where projects.id = enrichments.project_id
      and projects.user_id = (select auth.uid())
    )
  );
