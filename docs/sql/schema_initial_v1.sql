-- Начальная схема v1 по docs/data_model.md (пустая БД: Neon и т.п.).
-- Идемпотентно: CREATE TABLE IF NOT EXISTS. Порядок учитывает внешние ключи.
-- После этого применяйте schema_migrations_v1.sql и payment_reminder_jobs_trigger_v1.sql
-- (см. scripts/apply_schema_migrations.py).

create table if not exists public.therapists (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  email text,
  timezone text,
  telegram_private_chat_id bigint,
  payment_reminder_timing text,
  payment_reminder_offset_minutes integer,
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists public.clients (
  id uuid primary key default gen_random_uuid(),
  therapist_id uuid not null references public.therapists (id) on delete restrict,
  name text not null,
  telegram_user_id bigint,
  send_payment_reminders boolean not null default true,
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

create index if not exists clients_therapist_id_idx on public.clients (therapist_id);

create table if not exists public.client_chat_links (
  id uuid primary key default gen_random_uuid(),
  group_chat_id bigint not null,
  selected_therapist_id uuid references public.therapists (id) on delete set null,
  client_id uuid references public.clients (id) on delete set null,
  created_at timestamptz not null default now()
);

create index if not exists client_chat_links_client_id_idx on public.client_chat_links (client_id);
create index if not exists client_chat_links_selected_therapist_id_idx
  on public.client_chat_links (selected_therapist_id);

create table if not exists public.calendar_connections (
  id uuid primary key default gen_random_uuid(),
  therapist_id uuid not null references public.therapists (id) on delete restrict,
  calendar_id text not null,
  google_oauth_credentials_encrypted bytea,
  oauth_credentials_version smallint,
  sync_token text,
  last_full_sync_at timestamptz,
  push_channel_id text,
  push_resource_id text,
  push_channel_expires_at timestamptz,
  push_channel_token text,
  created_at timestamptz not null default now()
);

create index if not exists calendar_connections_therapist_id_idx
  on public.calendar_connections (therapist_id);

create table if not exists public.calendar_events (
  id uuid primary key default gen_random_uuid(),
  calendar_connection_id uuid not null references public.calendar_connections (id) on delete cascade,
  google_event_id text not null,
  summary text,
  client_id uuid references public.clients (id) on delete set null,
  status text not null default 'confirmed',
  is_recurring boolean not null default false,
  updated_at_google timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists calendar_events_calendar_connection_id_idx
  on public.calendar_events (calendar_connection_id);
create index if not exists calendar_events_client_id_idx on public.calendar_events (client_id);

create table if not exists public.calendar_event_instances (
  id uuid primary key default gen_random_uuid(),
  calendar_event_id uuid not null references public.calendar_events (id) on delete cascade,
  google_instance_event_id text not null,
  original_start_at timestamptz,
  actual_start_at timestamptz,
  actual_end_at timestamptz,
  status text not null default 'confirmed',
  is_paid boolean not null default false,
  payment_confirmed_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists calendar_event_instances_calendar_event_id_idx
  on public.calendar_event_instances (calendar_event_id);

comment on table public.therapists is 'Терапевты; см. docs/data_model.md §1';
comment on table public.clients is 'Клиенты; см. docs/data_model.md §2';
comment on table public.client_chat_links is 'Связь Telegram group ↔ клиент; см. docs/data_model.md §3';
comment on table public.calendar_connections is 'Подключения Google Calendar; см. docs/data_model.md §4';
comment on table public.calendar_events is 'Родительские события календаря; см. docs/data_model.md §5';
comment on table public.calendar_event_instances is 'Инстансы встреч; см. docs/data_model.md §6';
