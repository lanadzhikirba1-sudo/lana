-- Триггер синхронизации payment-reminder jobs (v1)
-- Идея: когда конкретный `calendar_event_instances` меняется (перенос/отмена/оплата),
-- обновляем соответствующую запись в `notification_jobs` по тому же `calendar_event_instance_id`.
--
-- Требуется:
-- - таблица `public.notification_jobs` (см. docs/sql/notification_jobs_v1.sql)
-- - в notification_jobs job_type = 'payment_reminder' предусмотрена уникальность по calendar_event_instance_id (частичный уникальный индекс)

create or replace function public.sync_payment_reminder_job_from_instance()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_therapist_id uuid;
  v_client_id uuid;

  v_therapist_active boolean;
  v_payment_reminder_timing text;
  v_payment_reminder_offset_minutes int;

  v_client_active boolean;
  v_send_payment_reminders boolean;

  v_is_cancelled boolean;
  v_scheduled_for timestamptz;
  v_target_chat_id bigint;

  v_existing_status text;
begin
  -- Находим терапевта и клиента через parent-событие календаря.
  -- calendar_event_instances -> calendar_events -> clients -> therapists
  select
    ce.client_id,
    c.therapist_id
  into
    v_client_id,
    v_therapist_id
  from public.calendar_event_instances cei
  join public.calendar_events ce on ce.id = cei.calendar_event_id
  join public.clients c on c.id = ce.client_id
  where cei.id = new.id;

  -- Если цепочка связей не найдена — просто ничего не планируем.
  if v_client_id is null or v_therapist_id is null then
    return new;
  end if;

  select
    t.is_active,
    t.payment_reminder_timing,
    t.payment_reminder_offset_minutes
  into
    v_therapist_active,
    v_payment_reminder_timing,
    v_payment_reminder_offset_minutes
  from public.therapists t
  where t.id = v_therapist_id;

  select
    c.is_active,
    c.send_payment_reminders
  into
    v_client_active,
    v_send_payment_reminders
  from public.clients c
  where c.id = v_client_id;

  v_is_cancelled :=
    lower(coalesce(new.status, '')) in ('cancelled', 'canceled', 'cancel');

  -- Разруливаем, должно ли существовать напоминание
  -- (и если да — то на какую дату).
  if v_therapist_active
     and v_client_active
     and v_send_payment_reminders
     and coalesce(new.is_paid, false) = false
     and not v_is_cancelled
     and v_payment_reminder_timing in ('before', 'after')
  then
    if v_payment_reminder_timing = 'before' then
      if new.actual_start_at is null then
        v_scheduled_for := null;
      else
        v_scheduled_for := new.actual_start_at - make_interval(mins => coalesce(v_payment_reminder_offset_minutes, 0));
      end if;
    else
      if new.actual_end_at is null then
        v_scheduled_for := null;
      else
        v_scheduled_for := new.actual_end_at + make_interval(mins => coalesce(v_payment_reminder_offset_minutes, 0));
      end if;
    end if;

    -- target_chat_id берём из связки client_chat_links для этого клиента и терапевта.
    -- Если связей несколько, выбираем самую свежую.
    select l.group_chat_id::bigint
    into v_target_chat_id
    from public.client_chat_links l
    where l.client_id = v_client_id
      and l.selected_therapist_id = v_therapist_id
    order by l.created_at desc
    limit 1;

    if v_scheduled_for is null or v_target_chat_id is null then
      -- Нельзя запланировать без времени и чата.
      v_is_cancelled := true;
    end if;
  else
    -- Любое из условий не выполнено => напоминание не нужно.
    v_is_cancelled := true;
  end if;

  -- Проверяем, есть ли уже job.
  select status
  into v_existing_status
  from public.notification_jobs nj
  where nj.calendar_event_instance_id = new.id
    and nj.job_type = 'payment_reminder';

  -- Если job уже отправлен — не трогаем.
  if v_existing_status = 'sent' then
    return new;
  end if;

  if v_is_cancelled then
    -- Отменяем job (если он ещё не отправлен).
    if v_existing_status is not null then
      update public.notification_jobs
      set status = 'cancelled',
          sent_at = null,
          last_error = null
      where calendar_event_instance_id = new.id
        and job_type = 'payment_reminder'
        and status <> 'sent';
    end if;
    return new;
  end if;

  -- Нужно активное напоминание (pending).
  -- Используем INSERT ... ON CONFLICT, чтобы избежать гонок при параллельных UPDATE.
  insert into public.notification_jobs (
    calendar_event_instance_id,
    job_type,
    scheduled_for,
    target_chat_id,
    status,
    sent_at,
    last_error
  ) values (
    new.id,
    'payment_reminder',
    v_scheduled_for,
    v_target_chat_id,
    'pending',
    null,
    null
  )
  on conflict (calendar_event_instance_id)
  where job_type = 'payment_reminder'
  do update
  set scheduled_for = case when public.notification_jobs.status = 'sent'
                            then public.notification_jobs.scheduled_for
                            else excluded.scheduled_for
                       end,
      target_chat_id = case when public.notification_jobs.status = 'sent'
                            then public.notification_jobs.target_chat_id
                            else excluded.target_chat_id
                       end,
      status = case when public.notification_jobs.status = 'sent'
                     then public.notification_jobs.status
                     else 'pending'
                end,
      sent_at = case when public.notification_jobs.status = 'sent'
                      then public.notification_jobs.sent_at
                      else null
                 end,
      last_error = null;

  return new;
end;
$$;

drop trigger if exists tr_sync_payment_reminder_jobs_from_instances on public.calendar_event_instances;

create trigger tr_sync_payment_reminder_jobs_from_instances
after insert or update of
  actual_start_at,
  actual_end_at,
  status,
  is_paid,
  payment_confirmed_at
on public.calendar_event_instances
for each row
execute function public.sync_payment_reminder_job_from_instance();

-- Триггер пересчёта reminder’ов при привязке клиента к встрече.
-- Текущий триггер payment-reminder работает только на INSERT/UPDATE конкретного
-- `calendar_event_instances`, но привязка происходит на parent уровне `calendar_events.client_id`.
-- Поэтому, когда `calendar_events.client_id` меняется (обычно NULL -> not NULL, либо админом),
-- "тронем" связанные инстансы, чтобы сработал `tr_sync_payment_reminder_jobs_from_instances`.
create or replace function public.sync_payment_reminder_jobs_from_event_client_id()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.client_id is distinct from old.client_id then
    update public.calendar_event_instances
    set status = status
    where calendar_event_id = new.id;
  end if;

  return new;
end;
$$;

drop trigger if exists tr_sync_payment_reminder_jobs_from_event_client_id on public.calendar_events;

create trigger tr_sync_payment_reminder_jobs_from_event_client_id
after update of client_id
on public.calendar_events
for each row
execute function public.sync_payment_reminder_jobs_from_event_client_id();

