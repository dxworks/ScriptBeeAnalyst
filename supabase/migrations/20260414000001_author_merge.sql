-- Unified users: one row per merged identity group
create table public.unified_users (
  id uuid primary key default uuid_generate_v4(),
  project_id uuid not null references public.projects(id) on delete cascade,
  display_name text not null,
  primary_email text,
  created_at timestamp with time zone default now() not null,
  updated_at timestamp with time zone default now() not null
);

-- Source identity mappings: which source accounts map to which unified user
create table public.user_identity_mappings (
  id uuid primary key default uuid_generate_v4(),
  unified_user_id uuid not null references public.unified_users(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  source text not null check (source in ('git', 'github', 'jira')),
  source_key text not null,
  source_name text,
  source_email text,
  source_login text,
  created_at timestamp with time zone default now() not null
);

-- Rejected similarity pairs: persisted so they don't reappear in suggestions
create table public.rejected_similarities (
  id uuid primary key default uuid_generate_v4(),
  project_id uuid not null references public.projects(id) on delete cascade,
  first_source text not null,
  first_source_key text not null,
  second_source text not null,
  second_source_key text not null,
  created_at timestamp with time zone default now() not null
);

-- Unique constraints
-- Each source identity can only be mapped once per project
create unique index uq_identity_mapping
  on public.user_identity_mappings (project_id, source, source_key);

-- Rejected pairs stored in canonical order to avoid duplicates
create unique index uq_rejected_similarity
  on public.rejected_similarities (
    project_id,
    least(first_source || ':' || first_source_key, second_source || ':' || second_source_key),
    greatest(first_source || ':' || first_source_key, second_source || ':' || second_source_key)
  );

-- Performance indexes
create index unified_users_project_id_idx on public.unified_users(project_id);
create index user_identity_mappings_project_id_idx on public.user_identity_mappings(project_id);
create index user_identity_mappings_unified_user_id_idx on public.user_identity_mappings(unified_user_id);
create index rejected_similarities_project_id_idx on public.rejected_similarities(project_id);

-- Enable RLS
alter table public.unified_users enable row level security;
alter table public.user_identity_mappings enable row level security;
alter table public.rejected_similarities enable row level security;

-- Unified users policies
create policy "Users can view own unified users"
  on public.unified_users for select
  using (
    exists (
      select 1 from public.projects
      where projects.id = unified_users.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can create unified users in own projects"
  on public.unified_users for insert
  with check (
    exists (
      select 1 from public.projects
      where projects.id = unified_users.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can update own unified users"
  on public.unified_users for update
  using (
    exists (
      select 1 from public.projects
      where projects.id = unified_users.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can delete own unified users"
  on public.unified_users for delete
  using (
    exists (
      select 1 from public.projects
      where projects.id = unified_users.project_id
      and projects.user_id = (select auth.uid())
    )
  );

-- User identity mappings policies
create policy "Users can view own identity mappings"
  on public.user_identity_mappings for select
  using (
    exists (
      select 1 from public.projects
      where projects.id = user_identity_mappings.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can create identity mappings in own projects"
  on public.user_identity_mappings for insert
  with check (
    exists (
      select 1 from public.projects
      where projects.id = user_identity_mappings.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can update own identity mappings"
  on public.user_identity_mappings for update
  using (
    exists (
      select 1 from public.projects
      where projects.id = user_identity_mappings.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can delete own identity mappings"
  on public.user_identity_mappings for delete
  using (
    exists (
      select 1 from public.projects
      where projects.id = user_identity_mappings.project_id
      and projects.user_id = (select auth.uid())
    )
  );

-- Rejected similarities policies
create policy "Users can view own rejected similarities"
  on public.rejected_similarities for select
  using (
    exists (
      select 1 from public.projects
      where projects.id = rejected_similarities.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can create rejected similarities in own projects"
  on public.rejected_similarities for insert
  with check (
    exists (
      select 1 from public.projects
      where projects.id = rejected_similarities.project_id
      and projects.user_id = (select auth.uid())
    )
  );

create policy "Users can delete own rejected similarities"
  on public.rejected_similarities for delete
  using (
    exists (
      select 1 from public.projects
      where projects.id = rejected_similarities.project_id
      and projects.user_id = (select auth.uid())
    )
  );

-- Updated_at trigger for unified_users
create trigger unified_users_updated_at
  before update on public.unified_users
  for each row execute function public.handle_updated_at();
