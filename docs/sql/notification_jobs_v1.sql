-- Очередь напоминаний (v1): очередь для payment-reminder.
-- Напоминания создаются/обновляются триггером на `calendar_event_instances`, а отправляются воркером/Edge Function по расписанию.
-- Выполнить в Supabase SQL Editor (или через миграцию).

create table if not exists public.notification_jobs (
  id uuid primary key default gen_random_uuid(),
  calendar_event_instance_id uuid not null references public.calendar_event_instances (id) on delete cascade,
  job_type text not null default 'payment_reminder',
  scheduled_for timestamptz not null,
  target_chat_id bigint not null,
  status text not null default 'pending',
  sent_at timestamptz,
  last_error text,
  created_at timestamptz not null default now()
);

-- Одно напоминание об оплате на один инстанс встречи (без дублей при повторных запусках планировщика).
create unique index if not exists notification_jobs_instance_reminder_unique
  on public.notification_jobs (calendar_event_instance_id)
  where job_type = 'payment_reminder';

comment on table public.notification_jobs is 'Очередь исходящих уведомлений; обрабатывается Supabase-воркером/Edge Function по расписанию.';
