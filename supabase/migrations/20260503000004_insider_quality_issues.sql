-- Insider quality-issues cache: one JSONB blob per project. Single-tenant, no RLS.

create table public.quality_issues (
    project_id uuid primary key references public.projects(id) on delete cascade,
    payload jsonb not null,
    source text not null default 'insider',
    generated_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index quality_issues_generated_at_idx on public.quality_issues (generated_at);

-- Allow Insider quality-issues JSON to upload as a serialized file alongside
-- git/github/jira/lizard/codeframe/dude_external/dude_internal.
alter table public.serialized_files
  drop constraint if exists serialized_files_file_type_check;

alter table public.serialized_files
  add constraint serialized_files_file_type_check
  check (file_type in ('git', 'github', 'jira', 'lizard', 'codeframe', 'dude_external', 'dude_internal', 'quality_issues'));
