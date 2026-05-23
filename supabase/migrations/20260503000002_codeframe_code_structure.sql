-- CodeFrame code-structure cache: one JSONB blob per project. Single-tenant, no RLS.

create table public.code_structure (
    project_id uuid primary key references public.projects(id) on delete cascade,
    payload jsonb not null,
    source text not null default 'codeframe',
    generated_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index code_structure_generated_at_idx on public.code_structure (generated_at);

-- Allow CodeFrame layout JSONL files to upload as serialized files.
update public.serialized_files set file_type = 'codeframe' where file_type = 'jafax';

alter table public.serialized_files
  drop constraint if exists serialized_files_file_type_check;

alter table public.serialized_files
  add constraint serialized_files_file_type_check
  check (file_type in ('git', 'github', 'jira', 'lizard', 'codeframe'));
