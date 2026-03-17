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
| digest_time_local | time | локальное время отправки утреннего digest |
| payment_reminder_timing | text | схема reminder: `before` или `after` |
| payment_reminder_offset_minutes | int | смещение reminder в минутах |
| is_active | boolean | активен ли терапевт |
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
| send_payment_reminders | boolean | включены ли reminder-уведомления |
| is_active | boolean | активен ли клиент |
| created_at | timestamptz | дата создания |

Комментарий:
- у клиента нет индивидуальной схемы reminder, только включение/выключение.

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
| sync_token | text | токен incremental sync |
| last_full_sync_at | timestamptz | время последней полной синхронизации |
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
| summary | text | заголовок события |
| status | text | статус родительского события |
| is_recurring | boolean | признак recurring-серии |
| updated_at_google | timestamptz | время обновления в Google |
| created_at | timestamptz | дата создания записи |

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
| original_start_at | timestamptz | исходное время recurring-инстанса; для одиночных `null` |
| actual_start_at | timestamptz | фактическое время начала |
| actual_end_at | timestamptz | фактическое время окончания |
| status | text | статус встречи |
| is_paid | boolean | оплачена ли встреча |
| payment_confirmed_at | timestamptz | время подтверждения оплаты |
| created_at | timestamptz | дата создания |

Комментарий:
- через эту таблицу строятся reminders, digest и статусы оплат.

---

## Связи высокого уровня

- `therapists 1 -> N clients`
- `therapists 1 -> N calendar_connections`
- `calendar_connections 1 -> N calendar_events`
- `calendar_events 1 -> N calendar_event_instances`
- `clients 1 -> N client_chat_links` (через подтвержденную привязку)
