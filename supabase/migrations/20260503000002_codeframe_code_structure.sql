-- CodeFrame code-structure cache: one JSONB blob per project.
-- Mirrors the lizard_file_metrics pattern (20260503000001_lizard_file_metrics.sql):
-- the data-server (re)computes the type/method/field/reference registries from
-- the CodeFrame JSONL at project load and stores the full set as a single
-- payload row keyed by project_id so subsequent loads can read without re-parsing.

create table public.code_structure (
    project_id uuid primary key references public.projects(id) on delete cascade,
    payload jsonb not null,
    source text not null default 'codeframe',
    generated_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index code_structure_generated_at_idx on public.code_structure (generated_at);

alter table public.code_structure enable row level security;

create policy "Users can view own code_structure"
  on public.code_structure for select
  using (
    exists (
      select 1 from public.projects
      where projects.id = code_structure.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can upsert own code_structure"
  on public.code_structure for insert
  with check (
    exists (
      select 1 from public.projects
      where projects.id = code_structure.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can update own code_structure"
  on public.code_structure for update
  using (
    exists (
      select 1 from public.projects
      where projects.id = code_structure.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can delete own code_structure"
  on public.code_structure for delete
  using (
    exists (
      select 1 from public.projects
      where projects.id = code_structure.project_id
      and projects.user_id = (select auth.uid())
    )
  );

-- Allow CodeFrame layout JSONL files to upload as serialized files.
update public.serialized_files set file_type = 'codeframe' where file_type = 'jafax';

alter table public.serialized_files
  drop constraint if exists serialized_files_file_type_check;

alter table public.serialized_files
  add constraint serialized_files_file_type_check
  check (file_type in ('git', 'github', 'jira', 'lizard', 'codeframe'));
