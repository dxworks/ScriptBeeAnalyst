-- Author smart-merge persistence — single-tenant, no RLS.

create table public.unified_users (
  id uuid primary key default uuid_generate_v4(),
  project_id uuid not null references public.projects(id) on delete cascade,
  display_name text not null,
  primary_email text,
  created_at timestamp with time zone default now() not null,
  updated_at timestamp with time zone default now() not null
);

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

create table public.rejected_similarities (
  id uuid primary key default uuid_generate_v4(),
  project_id uuid not null references public.projects(id) on delete cascade,
  first_source text not null,
  first_source_key text not null,
  second_source text not null,
  second_source_key text not null,
  created_at timestamp with time zone default now() not null
);

create unique index uq_identity_mapping
  on public.user_identity_mappings (project_id, source, source_key);

create unique index uq_rejected_similarity
  on public.rejected_similarities (
    project_id,
    least(first_source || ':' || first_source_key, second_source || ':' || second_source_key),
    greatest(first_source || ':' || first_source_key, second_source || ':' || second_source_key)
  );

create index unified_users_project_id_idx on public.unified_users(project_id);
create index user_identity_mappings_project_id_idx on public.user_identity_mappings(project_id);
create index user_identity_mappings_unified_user_id_idx on public.user_identity_mappings(unified_user_id);
create index rejected_similarities_project_id_idx on public.rejected_similarities(project_id);

create trigger unified_users_updated_at
  before update on public.unified_users
  for each row execute function public.handle_updated_at();
