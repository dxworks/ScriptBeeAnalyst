-- Enrichment cache: one JSONB blob per project. Single-tenant, no RLS.

create table public.enrichments (
    project_id uuid primary key references public.projects(id) on delete cascade,
    payload jsonb not null,
    generated_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index enrichments_generated_at_idx on public.enrichments (generated_at);
