-- Single-tenant local deployment: no auth, no RLS, no user attribution.
-- Tables are open; the only writer is the local data-server / web-ui pair.

create extension if not exists "uuid-ossp";

-- Projects table
--
-- UnifiedUsers redesign (see ../../unified_users_change.md §M):
--   * merge_state — lifecycle phase. 'PRE_MERGE' = setup (today's
--     default; refs target per-source accounts); 'FINALIZED' = post
--     rebind pass (refs target UNIFIED_USER). Flipped once by the
--     /projects/{id}/finalize endpoint.
--   * enrichment_config_frozen — JSON snapshot of the EnrichmentConfig
--     captured at finalize time. Used by post-finalize enrichment
--     re-runs (e.g. after a reload) so behaviour is reproducible
--     regardless of global config drift. NULL until finalize.
create table public.projects (
  id uuid primary key default uuid_generate_v4(),
  name text not null,
  description text,
  status text not null default 'draft' check (status in ('draft', 'processing', 'ready', 'idle', 'resuming', 'error')),
  merge_state text not null default 'PRE_MERGE' check (merge_state in ('PRE_MERGE', 'FINALIZED')),
  enrichment_config_frozen jsonb,
  created_at timestamp with time zone default now() not null,
  updated_at timestamp with time zone default now() not null
);

-- Serialized files table
-- file_type derived from filename: *.iglog -> git, github.json -> github, jira.json -> jira
-- repo_name: for git files, derived from filename stem (e.g., backend.iglog -> backend). NULL for github/jira.
create table public.serialized_files (
  id uuid primary key default uuid_generate_v4(),
  name text not null,
  file_type text not null check (file_type in ('git', 'github', 'jira')),
  repo_name text,
  storage_path text not null,
  size_bytes bigint not null,
  project_id uuid not null references public.projects(id) on delete cascade,
  created_at timestamp with time zone default now() not null,
  updated_at timestamp with time zone default now() not null
);

-- One iglog per repo name per project (multiple iglog files allowed with different repo names)
create unique index uq_serialized_files_with_repo
  on public.serialized_files (project_id, file_type, repo_name)
  where repo_name is not null;

-- One github.json / jira.json per project (repo_name is null for non-git types)
create unique index uq_serialized_files_without_repo
  on public.serialized_files (project_id, file_type)
  where repo_name is null;

create index serialized_files_project_id_idx on public.serialized_files(project_id);
create index serialized_files_file_type_idx on public.serialized_files(file_type);

-- Updated_at trigger function
create or replace function public.handle_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger projects_updated_at
  before update on public.projects
  for each row execute function public.handle_updated_at();

create trigger serialized_files_updated_at
  before update on public.serialized_files
  for each row execute function public.handle_updated_at();

-- Storage buckets — anonymous-friendly in single-tenant mode.
insert into storage.buckets (id, name, public)
values ('serialized-files', 'serialized-files', true)
on conflict (id) do update set public = excluded.public;

insert into storage.buckets (id, name, public)
values ('project-graphs', 'project-graphs', true)
on conflict (id) do update set public = excluded.public;

create policy "Anyone can read serialized files"
  on storage.objects for select
  using (bucket_id = 'serialized-files');

create policy "Anyone can upload serialized files"
  on storage.objects for insert
  with check (bucket_id = 'serialized-files');

create policy "Anyone can update serialized files"
  on storage.objects for update
  using (bucket_id = 'serialized-files');

create policy "Anyone can delete serialized files"
  on storage.objects for delete
  using (bucket_id = 'serialized-files');

create policy "Anyone can read project graphs"
  on storage.objects for select
  using (bucket_id = 'project-graphs');

create policy "Anyone can upload project graphs"
  on storage.objects for insert
  with check (bucket_id = 'project-graphs');

create policy "Anyone can update project graphs"
  on storage.objects for update
  using (bucket_id = 'project-graphs');

create policy "Anyone can delete project graphs"
  on storage.objects for delete
  using (bucket_id = 'project-graphs');
