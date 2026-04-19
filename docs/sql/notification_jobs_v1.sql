-- Очередь напоминаний (v1): payment_reminder.
-- Создание/обновление записей — backend и/или триггер на calendar_event_instances (см. payment_reminder_jobs_trigger_v1.sql).
-- Текст и отправка в Telegram — конструктор ботов; backend фиксирует sent/failed через HTTP API (docs/automation.md §7.6, §9.10–9.12).
-- Выполнить в PostgreSQL (миграция, консоль или psql).

-- FK на calendar_event_instances(id) опускаем, если в БД нет UNIQUE/PK на id (миграции со старых схем).
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

-- Одно напоминание об оплате на один инстанс встречи (без дублей при повторных запусках планировщика).
create unique index if not exists notification_jobs_instance_reminder_unique
  on public.notification_jobs (calendar_event_instance_id)
  where job_type = 'payment_reminder';

comment on table public.notification_jobs is 'Очередь payment reminder: планирование на backend, доставка текста через конструктор, статусы по mark-sent/mark-failed.';
