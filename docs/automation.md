# Automation

## Роль автоматизаций

Автоматизации реализуются через Make и выполняют orchestration процессов между:
- Google Calendar
- Supabase
- Telegram Bot API

Make является оркестратором, но не источником истины.

---

## Простое и надёжное решение для напоминаний (v1)

Идея: **храним время отправки** в `notification_jobs`, а **саму отправку** делаем не в Make, а в Supabase-воркере/Edge Function по расписанию.
В этом разделе фиксируем только вариант **B** (отправка из очереди).

| Сценарий | Расписание (рекомендация для теста) | Что делает |
|----------|-------------------------------------|------------|
| **B. Отправка** | каждые **2–5 минут** | Supabase-воркер/Edge Function делает `SELECT` где `status = pending` и `scheduled_for <= now()` → Telegram → обновляет `status = sent`, `sent_at = now()` (или `failed` + `last_error`). |

Почему так проще всего:
- не нужно создавать таймеры “на каждую запись”; воркер регулярно проверяет просроченные `pending`;
- воркер можно часто крутить — задержка отправки не больше интервала воркера;
- идемпотентность достигается через статусы задач (`pending` → `sent/failed`) и уникальность записи для `payment_reminder` на один `calendar_event_instance_id`.

**Время:** в `scheduled_for` хранить **timestamptz в UTC**; момент встречи (`actual_start_at` / `actual_end_at`) при расчёте переводить из контекста терапевта или считать, что в БД уже UTC.

**Минимальный тест:**
1. Выполнить `docs/sql/notification_jobs_v1.sql` в Supabase.
2. Вручную вставить строку в `notification_jobs`: `scheduled_for` = через 2 минуты, `target_chat_id` = твой тестовый чат, `pending`.
3. Воркера/Edge Function B по расписанию должны отправить сообщение и проставить `sent`.
4. Затем выполнить подключение механизма пополнения/обновления `notification_jobs` через SQL-триггер `docs/sql/payment_reminder_jobs_trigger_v1.sql` (он пересчитывает записи при INSERT/UPDATE `calendar_event_instances`).

---

## Ключевые автоматизации (v1)

## 1. Синхронизация календаря

Цель:
- поддерживать актуальные встречи в Supabase на основе Google Calendar.

Условия:
- `therapists.is_active = true` для владельца подключения; для неактивных терапевтов сценарий sync не запускается (или завершается без изменений).

Вход:
- `calendar_connections` (`calendar_id`, `sync_token`)

Шаги:
1. Запуск sync-сценария в Make.
2. Получение изменений из Google Calendar (incremental по `sync_token`, если валиден).
3. При недействительном/отсутствующем `sync_token` — **полная синхронизация** за нужный горизонт, затем сохранение нового `sync_token` и обновление `last_full_sync_at`.
4. Обновление `calendar_events`.
5. Создание/обновление `calendar_event_instances`.
6. Сохранение нового `sync_token` после успешного incremental-запроса.

Результат:
- Supabase содержит актуальные инстансы встреч.

---

## 2. Создание payment reminder jobs

Цель:
- планировать отправку reminder-уведомлений клиентам.

Условия:
- `therapists.is_active = true`
- `clients.is_active = true`
- у клиента `send_payment_reminders = true`
- глобальные настройки терапевта:
  - `payment_reminder_timing` (`before`/`after`)
  - `payment_reminder_offset_minutes`

Шаги:
1. При INSERT/UPDATE `calendar_event_instances` Supabase-триггер `payment_reminder_jobs_trigger_v1.sql` вычисляет необходимость reminder по:
   - `therapists.is_active`
   - `clients.is_active`
   - `clients.send_payment_reminders`
   - `therapists.payment_reminder_timing` + `payment_reminder_offset_minutes`
   - `calendar_event_instances.is_paid` и `calendar_event_instances.status`
   Триггер пересчитывает `notification_jobs` при изменении `actual_start_at`, `actual_end_at`, `status`, `is_paid` (то есть перенос/отмена/оплата конкретного инстанса).
   Также при установке `calendar_events.client_id` Supabase пересчитывает связанные `calendar_event_instances` (через дополнительный триггер), чтобы `notification_jobs` корректно отражали reminder-логику на основе клиента.
2. Триггер создаёт или обновляет запись в `notification_jobs` для этого же `calendar_event_instance_id` (и обновляет `scheduled_for` при переносе/изменении).

Результат:
- уведомления (pending) поставлены в очередь в `notification_jobs`.

---

## 3. Отправка уведомлений из очереди

Цель:
- отправлять запланированные сообщения в Telegram.

Вход:
- `notification_jobs` со статусом `pending` и наступившим `scheduled_for`.

Шаги:
1. Supabase-воркер/Edge Function получает pending-задачи.
2. Отправить сообщение в `target_chat_id`.
3. Обновить задачу:
   - при успехе: `status = sent`, заполнить `sent_at`
   - при ошибке: `status = failed`

Результат:
- уведомления отправлены и зафиксирован их статус.

---

## 4. Обработка подтверждения оплаты

Цель:
- обновлять статус оплаты встречи по действию клиента.

Триггер:
- webhook от Telegram после нажатия кнопки.

Шаги:
1. Make получает callback.
2. Находит нужный `calendar_event_instance`.
3. Обновляет:
   - `is_paid = true`
   - `payment_confirmed_at = now()`

Результат:
- оплата встречи подтверждена в данных системы.

---

## 5. Привязка клиента к встрече (calendar_events.client_id)

Цель:
- связать конкретную будущую встречу с выбранным клиентом, чтобы `calendar_events.client_id` был корректным источником для reminder’ов и digest.

Триггер:
- callback/сообщение из меню бота терапевта (раздел **клиенты и напоминания**), когда терапевт выбирает опцию “Связать со встречей” и затем конкретную встречу.

Условия:
- только `therapists.is_active = true`
- опция привязки доступна только если `calendar_events.client_id IS NULL` (перепривязка терапевту недоступна)

Шаги:
1. Make получает `therapist_id` и выбранный `client_id`.
2. Make запрашивает из Supabase список **встреч (calendar_events)** без повторов recurring, где:
   - `calendar_events.client_id IS NULL`
   - существует связанный `calendar_event_instances` с `actual_start_at > now()` (только будущие встречи)
   - события относятся к этому терапевту через `calendar_connections`
3. Терапевт выбирает `calendar_event_id`.
4. Make выполняет update связки:
   - `UPDATE calendar_events SET client_id = :client_id WHERE id = :calendar_event_id AND client_id IS NULL`
5. Если обновлено 0 строк, Make возвращает ошибку “эта встреча уже привязана”.

Результат:
- `calendar_events.client_id` заполнен для выбранной будущей встречи;
- связанные reminder’ы для этой встречи пересчитываются в Supabase.

---
## 6. Формирование и отправка daily digest

Цель:
- ежедневно отправлять терапевту список встреч и статусы оплат.

Триггер:
- ежедневный запуск по `digest_time_local` в таймзоне терапевта (по умолчанию 09:00, если не задано иное).

Условия:
- только `therapists.is_active = true`.

Шаги:
1. Собрать встречи за день по терапевту.
2. Для каждой встречи определить текущий статус оплаты.
3. Сформировать текст digest.
4. Отправить сообщение в `therapists.telegram_private_chat_id`.

Результат:
- терапевт получает ежедневный сводный отчет.

---

## Принципы надежности автоматизаций

- Идемпотентность: повторный запуск sync/notification не должен создавать неконсистентные дубликаты.
- Явные статусы задач: `pending`, `sent`, `failed`.
- Наблюдаемость: фиксировать время отправки (`sent_at`) и ключевые метки выполнения.
- Изоляция ответственности: Make оркестрирует, Supabase хранит состояние, Google Calendar хранит расписание.
