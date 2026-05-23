-- Lizard file metrics cache: one JSONB blob per project. Single-tenant, no RLS.

create table public.file_metrics (
    project_id uuid primary key references public.projects(id) on delete cascade,
    payload jsonb not null,
    source text not null default 'lizard',
    generated_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index file_metrics_generated_at_idx on public.file_metrics (generated_at);

-- Allow Lizard CSV to upload as a serialized file alongside git/github/jira.
alter table public.serialized_files
  drop constraint if exists serialized_files_file_type_check;

alter table public.serialized_files
  add constraint serialized_files_file_type_check
  check (file_type in ('git', 'github', 'jira', 'lizard'));
