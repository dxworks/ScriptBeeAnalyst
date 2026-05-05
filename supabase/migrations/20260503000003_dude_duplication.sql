-- DuDe duplication cache: one JSONB blob per project.
-- Mirrors the lizard_file_metrics / code_structure pattern: the data-server
-- (re)computes the per-pair external + per-file internal duplication payload
-- from DuDe's CSV/JSON at project load and stores the full set as a single
-- payload row keyed by project_id so subsequent loads can read without
-- re-parsing the inputs.

create table public.duplication (
    project_id uuid primary key references public.projects(id) on delete cascade,
    payload jsonb not null,
    source text not null default 'dude',
    generated_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index duplication_generated_at_idx on public.duplication (generated_at);

alter table public.duplication enable row level security;

create policy "Users can view own duplication"
  on public.duplication for select
  using (
    exists (
      select 1 from public.projects
      where projects.id = duplication.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can upsert own duplication"
  on public.duplication for insert
  with check (
    exists (
      select 1 from public.projects
      where projects.id = duplication.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can update own duplication"
  on public.duplication for update
  using (
    exists (
      select 1 from public.projects
      where projects.id = duplication.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can delete own duplication"
  on public.duplication for delete
  using (
    exists (
      select 1 from public.projects
      where projects.id = duplication.project_id
      and projects.user_id = (select auth.uid())
    )
  );

-- Allow DuDe external CSV / internal JSON to upload as serialized files
-- alongside git/github/jira/lizard/jafax/codeframe.
alter table public.serialized_files
  drop constraint if exists serialized_files_file_type_check;

alter table public.serialized_files
  add constraint serialized_files_file_type_check
  check (file_type in ('git', 'github', 'jira', 'lizard', 'jafax', 'codeframe', 'dude_external', 'dude_internal'));
