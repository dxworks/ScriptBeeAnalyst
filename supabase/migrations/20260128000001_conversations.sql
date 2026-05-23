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
