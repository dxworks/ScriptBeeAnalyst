-- Lizard / Metrix++ file metrics cache: one JSONB blob per project.
-- Mirrors the enrichment cache pattern (20260429000001_create_enrichments.sql):
-- the data-server (re)computes per-file LOC + complexity rollups from Lizard
-- CSV at project load and stores the full set as a single payload row keyed
-- by project_id so subsequent loads can read without re-parsing the CSV.

create table public.file_metrics (
    project_id uuid primary key references public.projects(id) on delete cascade,
    payload jsonb not null,
    source text not null default 'lizard',
    generated_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index file_metrics_generated_at_idx on public.file_metrics (generated_at);

alter table public.file_metrics enable row level security;

create policy "Users can view own file_metrics"
  on public.file_metrics for select
  using (
    exists (
      select 1 from public.projects
      where projects.id = file_metrics.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can upsert own file_metrics"
  on public.file_metrics for insert
  with check (
    exists (
      select 1 from public.projects
      where projects.id = file_metrics.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can update own file_metrics"
  on public.file_metrics for update
  using (
    exists (
      select 1 from public.projects
      where projects.id = file_metrics.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can delete own file_metrics"
  on public.file_metrics for delete
  using (
    exists (
      select 1 from public.projects
      where projects.id = file_metrics.project_id
      and projects.user_id = (select auth.uid())
    )
  );

-- Allow Lizard / Metrix++ CSVs to upload as serialized files alongside git/github/jira.
alter table public.serialized_files
  drop constraint if exists serialized_files_file_type_check;

alter table public.serialized_files
  add constraint serialized_files_file_type_check
  check (file_type in ('git', 'github', 'jira', 'lizard', 'metrixpp'));
