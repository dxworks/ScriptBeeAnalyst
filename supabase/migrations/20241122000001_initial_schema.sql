-- Enable UUID extension
create extension if not exists "uuid-ossp";

-- Projects table
create table public.projects (
  id uuid primary key default uuid_generate_v4(),
  name text not null,
  description text,
  user_id uuid not null references auth.users(id) on delete cascade,
  status text not null default 'draft' check (status in ('draft', 'processing', 'ready', 'idle', 'resuming', 'error')),
  created_at timestamp with time zone default now() not null,
  updated_at timestamp with time zone default now() not null
);

-- Serialized files table
-- file_type derived from filename: git.iglog -> git, github.json -> github, jira.json -> jira
create table public.serialized_files (
  id uuid primary key default uuid_generate_v4(),
  name text not null,
  file_type text not null check (file_type in ('git', 'github', 'jira')),
  storage_path text not null,
  size_bytes bigint not null,
  project_id uuid not null references public.projects(id) on delete cascade,
  created_at timestamp with time zone default now() not null,
  updated_at timestamp with time zone default now() not null,
  -- One file per type per project
  unique (project_id, file_type)
);

-- Enable RLS
alter table public.projects enable row level security;
alter table public.serialized_files enable row level security;

-- Projects policies: users can only CRUD their own projects
create policy "Users can view own projects"
  on public.projects for select
  using ((select auth.uid()) = user_id);

create policy "Users can create own projects"
  on public.projects for insert
  with check ((select auth.uid()) = user_id);

create policy "Users can update own projects"
  on public.projects for update
  using ((select auth.uid()) = user_id);

create policy "Users can delete own projects"
  on public.projects for delete
  using ((select auth.uid()) = user_id);

-- Serialized files policies: users can only CRUD files in their projects
create policy "Users can view own files"
  on public.serialized_files for select
  using (
    exists (
      select 1 from public.projects
      where projects.id = serialized_files.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can create files in own projects"
  on public.serialized_files for insert
  with check (
    exists (
      select 1 from public.projects
      where projects.id = serialized_files.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can update own files"
  on public.serialized_files for update
  using (
    exists (
      select 1 from public.projects
      where projects.id = serialized_files.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can delete own files"
  on public.serialized_files for delete
  using (
    exists (
      select 1 from public.projects
      where projects.id = serialized_files.project_id
      and projects.user_id = (select auth.uid())
    )
  );

-- Indexes for better query performance
create index projects_user_id_idx on public.projects(user_id);
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

-- Apply updated_at triggers
create trigger projects_updated_at
  before update on public.projects
  for each row execute function public.handle_updated_at();

create trigger serialized_files_updated_at
  before update on public.serialized_files
  for each row execute function public.handle_updated_at();

-- Create storage bucket for serialized files
insert into storage.buckets (id, name)
values ('serialized-files', 'serialized-files');

-- Storage policies: users can only access files in their projects
create policy "Users can upload to own projects"
  on storage.objects for insert
  with check (
    bucket_id = 'serialized-files'
    and auth.uid() is not null
  );

create policy "Users can view own project files"
  on storage.objects for select
  using (
    bucket_id = 'serialized-files'
    and auth.uid() is not null
  );

create policy "Users can delete own project files"
  on storage.objects for delete
  using (
    bucket_id = 'serialized-files'
    and auth.uid() is not null
  );

-- Create storage bucket for project graphs (pickles)
insert into storage.buckets (id, name)
values ('project-graphs', 'project-graphs');

-- Storage policies for project-graphs: users can access graphs for their projects
create policy "Users can upload graphs to own projects"
  on storage.objects for insert
  with check (
    bucket_id = 'project-graphs'
    and auth.uid() is not null
  );

create policy "Users can view own project graphs"
  on storage.objects for select
  using (
    bucket_id = 'project-graphs'
    and auth.uid() is not null
  );

create policy "Users can delete own project graphs"
  on storage.objects for delete
  using (
    bucket_id = 'project-graphs'
    and auth.uid() is not null
  );
