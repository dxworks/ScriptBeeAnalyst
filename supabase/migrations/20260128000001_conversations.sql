-- Conversations table
create table public.conversations (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid not null references auth.users(id) on delete cascade,
  project_id uuid references public.projects(id) on delete cascade,
  title text,
  parent_conversation_id uuid references public.conversations(id) on delete cascade,
  created_at timestamp with time zone default now() not null,
  updated_at timestamp with time zone default now() not null
);

-- Messages table
create table public.messages (
  id uuid primary key default uuid_generate_v4(),
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  role text not null check (role in ('user', 'assistant', 'system')),
  content text not null,
  parent_message_id uuid references public.messages(id) on delete cascade,
  branch_index integer default 0,
  created_at timestamp with time zone default now() not null
);

-- Enable RLS
alter table public.conversations enable row level security;
alter table public.messages enable row level security;

-- Conversations policies: users can only access their own conversations
create policy "Users can view own conversations"
  on public.conversations for select
  using ((select auth.uid()) = user_id);

create policy "Users can create own conversations"
  on public.conversations for insert
  with check ((select auth.uid()) = user_id);

create policy "Users can update own conversations"
  on public.conversations for update
  using ((select auth.uid()) = user_id);

create policy "Users can delete own conversations"
  on public.conversations for delete
  using ((select auth.uid()) = user_id);

-- Messages policies: users can only access messages in their conversations
create policy "Users can view own messages"
  on public.messages for select
  using (
    exists (
      select 1 from public.conversations
      where conversations.id = messages.conversation_id
      and conversations.user_id = (select auth.uid())
    )
  );

create policy "Users can create messages in own conversations"
  on public.messages for insert
  with check (
    exists (
      select 1 from public.conversations
      where conversations.id = messages.conversation_id
      and conversations.user_id = (select auth.uid())
    )
  );

create policy "Users can update own messages"
  on public.messages for update
  using (
    exists (
      select 1 from public.conversations
      where conversations.id = messages.conversation_id
      and conversations.user_id = (select auth.uid())
    )
  );

create policy "Users can delete own messages"
  on public.messages for delete
  using (
    exists (
      select 1 from public.conversations
      where conversations.id = messages.conversation_id
      and conversations.user_id = (select auth.uid())
    )
  );

-- Indexes for better query performance
create index conversations_user_id_idx on public.conversations(user_id);
create index conversations_project_id_idx on public.conversations(project_id);
create index conversations_parent_conversation_id_idx on public.conversations(parent_conversation_id);
create index messages_conversation_id_idx on public.messages(conversation_id);
create index messages_parent_message_id_idx on public.messages(parent_message_id);

-- Apply updated_at trigger to conversations
create trigger conversations_updated_at
  before update on public.conversations
  for each row execute function public.handle_updated_at();
