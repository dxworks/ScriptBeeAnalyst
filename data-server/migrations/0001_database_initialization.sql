-- ============================================================================
-- database_initialization — the full ScriptBee schema in one migration.
--
-- Squashed from the 14 historical supabase/migrations/*.sql files (initial
-- schema .. finalizing_merge_state), with the Supabase-platform-only
-- statements (storage.buckets/storage.objects, `alter publication
-- supabase_realtime`) removed permanently: this is plain-Postgres canonical.
-- Applied by yoyo-migrations (see src/bootstrap.py).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- from 20241122000001_initial_schema.sql
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- from 20260128000001_conversations.sql
-- ---------------------------------------------------------------------------
-- Conversations + messages — single-tenant, no RLS, no user attribution.

create table public.conversations (
  id uuid primary key default uuid_generate_v4(),
  project_id uuid references public.projects(id) on delete cascade,
  title text,
  parent_conversation_id uuid references public.conversations(id) on delete cascade,
  created_at timestamp with time zone default now() not null,
  updated_at timestamp with time zone default now() not null
);
create table public.messages (
  id uuid primary key default uuid_generate_v4(),
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  role text not null check (role in ('user', 'assistant', 'system')),
  content text not null,
  parent_message_id uuid references public.messages(id) on delete cascade,
  branch_index integer default 0,
  created_at timestamp with time zone default now() not null
);
create index conversations_project_id_idx on public.conversations(project_id);
create index conversations_parent_conversation_id_idx on public.conversations(parent_conversation_id);
create index messages_conversation_id_idx on public.messages(conversation_id);
create index messages_parent_message_id_idx on public.messages(parent_message_id);
create trigger conversations_updated_at
  before update on public.conversations
  for each row execute function public.handle_updated_at();

-- ---------------------------------------------------------------------------
-- from 20260414000001_author_merge.sql
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- from 20260429000001_create_enrichments.sql
-- ---------------------------------------------------------------------------
-- Enrichment cache: one JSONB blob per project. Single-tenant, no RLS.

create table public.enrichments (
    project_id uuid primary key references public.projects(id) on delete cascade,
    payload jsonb not null,
    generated_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index enrichments_generated_at_idx on public.enrichments (generated_at);

-- ---------------------------------------------------------------------------
-- from 20260503000001_lizard_file_metrics.sql
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- from 20260503000002_codeframe_code_structure.sql
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- from 20260503000003_dude_duplication.sql
-- ---------------------------------------------------------------------------
-- DuDe duplication cache: one JSONB blob per project. Single-tenant, no RLS.

create table public.duplication (
    project_id uuid primary key references public.projects(id) on delete cascade,
    payload jsonb not null,
    source text not null default 'dude',
    generated_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index duplication_generated_at_idx on public.duplication (generated_at);
-- Allow DuDe external CSV / internal JSON to upload as serialized files
-- alongside git/github/jira/lizard/codeframe.
alter table public.serialized_files
  drop constraint if exists serialized_files_file_type_check;
alter table public.serialized_files
  add constraint serialized_files_file_type_check
  check (file_type in ('git', 'github', 'jira', 'lizard', 'codeframe', 'dude_external', 'dude_internal'));

-- ---------------------------------------------------------------------------
-- from 20260503000004_insider_quality_issues.sql
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- from 20260522000001_appinspector_tags.sql
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- from 20260523000001_project_component_mapping.sql
-- ---------------------------------------------------------------------------
-- Per-project component mapping: a JSONB blob on the projects row that
-- replaces the operator-level EnrichmentConfig.components_mapping_path knob.
-- The data-server reads this column at build time, hands the dict to
-- EnrichmentConfig.components_mapping_data, and the path field stays as a
-- dev/test fallback when this column is null.
--
-- Payload shape (mirrors src/common/domains/components/resolver.py):
--   {
--     "<component_name>": {
--       "path_prefix": "src/foo/",
--       "extra_paths": ["lib/foo-helpers/"]
--     },
--     ...
--   }
-- Null means "no curated mapping yet" — the resolver falls back to the
-- top-folder heuristic (and to components_mapping_path when set).
-- An empty object ``{}`` is treated identically to null: both mean
-- "no curated mapping". The PUT endpoint normalises empty payloads
-- to SQL NULL so the heuristic fallback re-engages cleanly.
--
-- A column on projects (not a side table) is enough for v1: no history,
-- one mapping per project, atomic with the rest of the project row.

alter table public.projects
  add column if not exists component_mapping jsonb;

-- ---------------------------------------------------------------------------
-- from 20260523120000_project_filter_rules.sql
-- ---------------------------------------------------------------------------
-- Per-project filter rules: agent-authored exclusion rules that the
-- data-server applies at view time via FilteredSandboxView. Each row is a
-- single-entity-kind predicate (see data-server/src/filter_rules/models.py)
-- the user described in natural language and the OpenCode assistant
-- translated into a structured DSL. Single-tenant local mode — no RLS, no
-- user attribution; realtime stays on so the web-ui's "Exclusion Rules"
-- tab refreshes without polling.

create table public.project_filter_rules (
  id uuid primary key default uuid_generate_v4(),
  project_id uuid not null references public.projects(id) on delete cascade,
  entity_kind text not null,
  name text not null,
  nl_description text not null,
  dsl jsonb not null,
  created_at timestamp with time zone default now() not null
);
create index project_filter_rules_project_id_idx
  on public.project_filter_rules(project_id);

-- ---------------------------------------------------------------------------
-- from 20260524000001_project_config_overrides.sql
-- ---------------------------------------------------------------------------
-- Per-project enrichment-config overrides. Each row carries a JSON dict
-- {field_name: value} that overlays the global EnrichmentConfig defaults
-- at build time. Mirrors the projects.component_mapping pattern but lives
-- in its own table because the payload is open-ended (~80 fields) and
-- benefits from its own updated_at for "last edited" UX.
--
-- Shape:
--   overrides = {
--     "bugmagnet_min_bugfix_commits": 8,
--     "hibernator_min_lifetime_commits": 10,
--     "polarised_top_share": 0.75,
--     "resolved_status_categories": ["done", "closed", "wontfix"],
--     ...
--   }
-- Only fields the user EXPLICITLY edited appear. Missing fields fall
-- through to EnrichmentConfig's class-level default. An empty {} is
-- semantically identical to "no row" — the merge step is a no-op.
--
-- Single row per project; the project_id is both PK and FK so the row
-- cascades on project delete and there is no insert-vs-update branch
-- (the repository upserts).
--
-- No RLS in local single-tenant mode (matches project_filter_rules).
-- Realtime is disabled — saves are explicit and the UI re-reads on demand.

create table public.project_config_overrides (
  project_id uuid primary key references public.projects(id) on delete cascade,
  overrides jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
-- Reuse the schema-hardened helper from the initial migration; it sets
-- new.updated_at = now() and pins search_path = ''.
create trigger project_config_overrides_updated_at
before update on public.project_config_overrides
for each row execute procedure public.handle_updated_at();

-- ---------------------------------------------------------------------------
-- from 20260602000001_project_progress.sql
-- ---------------------------------------------------------------------------
-- Live pipeline progress for the dashboard loading bar.
--
-- The graph build/finalize work runs in the `processor` container (and the
-- HTTP /build path in `app`), while the dashboard reads `GET /projects` from
-- the `app` container. Those are separate processes, so progress can't live in
-- process memory — it goes on the projects row instead, written at hardcoded
-- checkpoints by the worker (see data-server/src/progress.py) and read back by
-- the API serializer (projects/router.py::_project_to_dict).
--
--   progress        smallint 0..100, NULL = no pipeline running (bar hidden)
--   progress_stage  human-readable checkpoint label for `progress`
--
-- The row's `projects_updated_at` trigger bumps `updated_at` on every progress
-- write, which the web-ui's 4s poll already reconciles on — so the card's
-- top-edge bar advances without any extra transport.
alter table public.projects
  add column if not exists progress smallint,
  add column if not exists progress_stage text;

-- ---------------------------------------------------------------------------
-- from 20260602000002_finalizing_merge_state.sql
-- ---------------------------------------------------------------------------
-- Add the transient FINALIZING merge_state.
--
-- Finalize (PRE_MERGE -> FINALIZED) runs Phase B in the background for a couple
-- of minutes. Previously the projects row only flipped to FINALIZED at the very
-- end, so a browser refresh mid-finalize saw PRE_MERGE and re-opened the
-- editable setup (author matching / configs) instead of the Analysis loading
-- bar. The finalize endpoint now writes FINALIZING onto the row immediately
-- (data-server/src/server.py) and `/projects/current` surfaces it, so the
-- web-ui keeps the setup locked and the loading bar up across a refresh.
--
-- The graph's in-memory merge_state still only ever holds PRE_MERGE / FINALIZED
-- (see common/kernel/merge_state.py) — FINALIZING is a row-only state.
--
-- The original CHECK constraint is the table-anonymous `projects_merge_state_check`
-- created inline in 20241122000001_initial_schema.sql.
alter table public.projects
  drop constraint if exists projects_merge_state_check;
alter table public.projects
  add constraint projects_merge_state_check
  check (merge_state in ('PRE_MERGE', 'FINALIZING', 'FINALIZED'));
