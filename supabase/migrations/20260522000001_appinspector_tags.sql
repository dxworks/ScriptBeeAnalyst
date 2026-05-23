-- AppInspector tags cache: one JSONB blob per project. Single-tenant, no RLS.

create table public.app_inspector_tags (
    project_id uuid primary key references public.projects(id) on delete cascade,
    payload jsonb not null,
    source text not null default 'appinspector',
    generated_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index app_inspector_tags_generated_at_idx on public.app_inspector_tags (generated_at);

-- Allow AppInspector tags JSON to upload as a serialized file alongside
-- git/github/jira/lizard/codeframe/dude_external/dude_internal/quality_issues.
alter table public.serialized_files
  drop constraint if exists serialized_files_file_type_check;

alter table public.serialized_files
  add constraint serialized_files_file_type_check
  check (file_type in ('git', 'github', 'jira', 'lizard', 'codeframe', 'dude_external', 'dude_internal', 'quality_issues', 'app_inspector'));
