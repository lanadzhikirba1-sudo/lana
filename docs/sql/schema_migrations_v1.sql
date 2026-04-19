-- Миграция к схеме docs/data_model.md (v1), идемпотентные шаги.
-- Порядок: сначала колонки в существующих таблицах, затем notification_jobs, затем отдельно
-- примените docs/sql/payment_reminder_jobs_trigger_v1.sql (или scripts/apply_schema_migrations.py).

alter table public.calendar_connections
  add column if not exists google_oauth_credentials_encrypted bytea,
  add column if not exists oauth_credentials_version smallint,
  add column if not exists push_channel_id text,
  add column if not exists push_resource_id text,
  add column if not exists push_channel_expires_at timestamptz,
  add column if not exists push_channel_token text;

alter table public.calendar_events
  add column if not exists summary text;

-- Колонка не входит в актуальную модель (digest удалён из product_overview / v1).
alter table public.therapists
  drop column if exists digest_time_local;

-- FK на calendar_event_instances(id) не задаём: в существующих БД id может быть без UNIQUE/PK.
-- Целостность обеспечивает приложение; триггер sync_payment_reminder_job_from_instance опирается на id инстанса.
create table if not exists public.notification_jobs (
  id uuid primary key default gen_random_uuid(),
  calendar_event_instance_id uuid not null,
  job_type text not null default 'payment_reminder',
  scheduled_for timestamptz not null,
  target_chat_id bigint not null,
  status text not null default 'pending',
  sent_at timestamptz,
  last_error text,
  created_at timestamptz not null default now()
);

create unique index if not exists notification_jobs_instance_reminder_unique
  on public.notification_jobs (calendar_event_instance_id)
  where job_type = 'payment_reminder';

comment on table public.notification_jobs is 'Очередь payment reminder: планирование на backend, доставка текста через конструктор, статусы по mark-sent/mark-failed.';
