-- Insider quality-issues cache: one JSONB blob per project.
-- Mirrors the lizard_file_metrics / code_structure / duplication pattern: the
-- data-server (re)computes the per-file Insider code-smell payload at project
-- load and stores the full set as a single payload row keyed by project_id so
-- subsequent loads can read without re-parsing the inputs.

create table public.quality_issues (
    project_id uuid primary key references public.projects(id) on delete cascade,
    payload jsonb not null,
    source text not null default 'insider',
    generated_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index quality_issues_generated_at_idx on public.quality_issues (generated_at);

alter table public.quality_issues enable row level security;

create policy "Users can view own quality_issues"
  on public.quality_issues for select
  using (
    exists (
      select 1 from public.projects
      where projects.id = quality_issues.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can upsert own quality_issues"
  on public.quality_issues for insert
  with check (
    exists (
      select 1 from public.projects
      where projects.id = quality_issues.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can update own quality_issues"
  on public.quality_issues for update
  using (
    exists (
      select 1 from public.projects
      where projects.id = quality_issues.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can delete own quality_issues"
  on public.quality_issues for delete
  using (
    exists (
      select 1 from public.projects
      where projects.id = quality_issues.project_id
      and projects.user_id = (select auth.uid())
    )
  );

-- Allow Insider quality-issues JSON to upload as a serialized file alongside
-- git/github/jira/lizard/codeframe/dude_external/dude_internal.
alter table public.serialized_files
  drop constraint if exists serialized_files_file_type_check;

alter table public.serialized_files
  add constraint serialized_files_file_type_check
  check (file_type in ('git', 'github', 'jira', 'lizard', 'codeframe', 'dude_external', 'dude_internal', 'quality_issues'));
