# Data Model

Актуальная модель данных основана на `project_context.md`.

---

## 1. therapists

Терапевты системы.

| Поле | Тип данных | Назначение |
|---|---|---|
| id | uuid | уникальный идентификатор терапевта |
| name | text | имя терапевта |
| email | text | рабочий email |
| timezone | text | таймзона терапевта в формате IANA |
| telegram_private_chat_id | int8 | id личного чата терапевта с ботом |
| digest_time_local | time | локальное время ежедневного digest в таймзоне `timezone`; по умолчанию 09:00; меняется через меню бота |
| payment_reminder_timing | text | схема reminder: `before` или `after`; задаётся при стартовой настройке, перенастраивается через меню бота |
| payment_reminder_offset_minutes | int | смещение reminder в минутах; то же правило сбора и смены |
| is_active | boolean | `false`, если терапевт удалил чат с ботом или прекратил подписку: по нему и связанным клиентам автоматизации не выполняются |
| created_at | timestamptz | дата создания записи |

---

## 2. clients

Клиенты терапевтов.

| Поле | Тип данных | Назначение |
|---|---|---|
| id | uuid | идентификатор клиента |
| therapist_id | uuid | ссылка на терапевта |
| name | text | имя клиента |
| telegram_user_id | int8 | Telegram user id клиента (если известен) |
| send_payment_reminders | boolean | напоминания об оплате для этого клиента: терапевт явно включает/выключает в боте (список клиентов с метками) |
| is_active | boolean | активен ли клиент в системе; см. комментарий ниже |
| created_at | timestamptz | дата создания |

Комментарии:
- у клиента нет индивидуальной схемы reminder (только on/off), схема задаётся на уровне терапевта.
- **`is_active`**: если терапевт прекращает работу с клиентом, клиента можно деактивировать — тогда напоминания и учёт в digest для него не ведутся даже при оставшихся в календаре будущих событиях. Если встреч с клиентом больше не планируется, технически напоминаний не будет и без флага; флаг даёт явный контроль и защиту от «хвостов» в календаре.

---

## 3. client_chat_links

Связь Telegram group chat и клиента.

| Поле | Тип данных | Назначение |
|---|---|---|
| id | uuid | идентификатор записи |
| group_chat_id | int8 | id Telegram group/supergroup |
| selected_therapist_id | uuid | терапевт, выбранный клиентом до подтверждения |
| client_id | uuid | привязанный клиент после подтверждения |
| created_at | timestamptz | время первого обнаружения чата |

Логика состояния:
- `selected_therapist_id is not null` и `client_id is null` -> клиент выбрал терапевта, но терапевт еще не подтвердил
- `client_id is not null` -> чат привязан к подтвержденному клиенту

---

## 4. calendar_connections

Подключения Google Calendar.

| Поле | Тип данных | Назначение |
|---|---|---|
| id | uuid | идентификатор подключения |
| therapist_id | uuid | владелец календаря |
| calendar_id | text | Google Calendar ID |
| sync_token | text | токен incremental sync Calendar API; доступ к API после OAuth терапевта (см. `docs/integrations.md`) |
| last_full_sync_at | timestamptz | время последней полной синхронизации; полезно при инвалидации `sync_token`, диагностике и принудительном полном пересборе инстансов |
| created_at | timestamptz | дата создания |

---

## 5. calendar_events

Родительские события Google Calendar:
- одиночное событие
- или родитель recurring-серии

| Поле | Тип данных | Назначение |
|---|---|---|
| id | uuid | внутренний id события |
| calendar_connection_id | uuid | ссылка на подключение календаря |
| google_event_id | text | id события в Google |
| client_id | uuid | клиент события/серии |
| status | text | статус родительского события в терминах Google (`confirmed`, `cancelled` и т.д.); отмена всего события/серии |
| is_recurring | boolean | признак recurring-серии |
| updated_at_google | timestamptz | время последнего обновления события в Google (`updated` из API); помогает при sync решать, нужно ли пересобирать дочерние инстансы |
| created_at | timestamptz | дата создания записи |

Комментарий:
- перенос или отмена **отдельного вхождения** recurring-серии отражаются на уровне `calendar_event_instances`, а не только здесь.
- `client_id` связывает родительское событие (встречу/серия) с конкретным клиентом системы: одна встреча — один клиент.
  - `NULL` означает, что встреча еще не привязана.
  - терапевт устанавливает `client_id` только при `NULL` (перепривязка доступна только в админ-режиме).

---

## 6. calendar_event_instances

Главная операционная таблица реальных встреч:
- одиночные встречи
- все инстансы recurring-серий

| Поле | Тип данных | Назначение |
|---|---|---|
| id | uuid | внутренний id инстанса |
| calendar_event_id | uuid | ссылка на `calendar_events.id` |
| google_instance_event_id | text | id конкретного инстанса из Google |
| original_start_at | timestamptz | для recurring: исходное время слота серии; при переносе одного вхождения не меняется. Для одиночных событий — `null` |
| actual_start_at | timestamptz | фактическое время начала встречи (после переноса — новое время) |
| actual_end_at | timestamptz | фактическое время окончания |
| status | text | операционный статус встречи: состоялась / отменена / перенесена и т.д.; напоминания и digest опираются на инстанс |
| is_paid | boolean | оплачена ли встреча |
| payment_confirmed_at | timestamptz | время подтверждения оплаты |
| created_at | timestamptz | дата создания |

Комментарий:
- через эту таблицу строятся reminders, digest и статусы оплат.
- **Время и переносы**: для одиночного события ориентир — `actual_start_at` / `actual_end_at`. Для серии сравнение `original_start_at` и `actual_start_at` позволяет увидеть сдвиг конкретного инстанса.

---

## 7. notification_jobs

Очередь исходящих уведомлений (v1: Supabase-триггер при изменениях `calendar_event_instances` создаёт/обновляет `pending` задачи; Supabase-воркер/Edge Function по расписанию отправляет payment-reminder уведомления и обновляет `status`/`sent_at`).

| Поле | Тип данных | Назначение |
|---|---|---|
| id | uuid | id задачи |
| calendar_event_instance_id | uuid | встреча, к которой относится напоминание |
| job_type | text | например `payment_reminder` |
| scheduled_for | timestamptz | когда отправить (хранить в UTC) |
| target_chat_id | int8 | куда писать в Telegram (часто `client_chat_links.group_chat_id`) |
| status | text | `pending` → `sent` или `failed` |
| sent_at | timestamptz | факт отправки |
| last_error | text | краткий текст ошибки при `failed` (опционально) |
| created_at | timestamptz | создание записи |

Правило уникальности (см. `docs/sql/notification_jobs_v1.sql`): не более **одной** задачи `payment_reminder` на один `calendar_event_instance_id`.

---

## Связи высокого уровня

- `therapists 1 -> N clients`
- `therapists 1 -> N calendar_connections`
- `calendar_connections 1 -> N calendar_events`
- `calendar_events 1 -> N calendar_event_instances`
- `calendar_event_instances 1 -> 0..1 notification_jobs` (для `payment_reminder` в v1)
- `clients 1 -> N client_chat_links` (через подтвержденную привязку)
