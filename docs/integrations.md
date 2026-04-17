# Integrations

## Обзор

Система использует 4 ключевые интеграции:

- Google Calendar
- Telegram Bot API
- Backend API
- PostgreSQL

Распределение ролей:

- расписание: Google Calendar
- данные системы: PostgreSQL
- пользовательский интерфейс: Telegram через конструктор ботов
- бизнес-логика и интеграции: backend

Целевая схема взаимодействия:

`Конструктор ботов -> Backend API -> PostgreSQL / Google Calendar / Telegram API`

---

## 1. Google Calendar

### Назначение

- получение событий календаря
- обработка recurring-серий
- обновление `calendar_events` и `calendar_event_instances`
- получение push-уведомлений об изменениях календаря

### Принцип

- Google Calendar является источником истины для расписания.
- Система не создает и не изменяет события в Google Calendar.
- Все обращения к Google Calendar выполняет backend.

### Доступ к календарю терапевта (OAuth)

1. Терапевт инициирует в боте действие **«Подключить Google Calendar»**.
2. Конструктор получает от backend ссылку авторизации и показывает её пользователю.
3. Терапевт проходит авторизацию в браузере.
4. Backend принимает OAuth callback, обменивает `code` на `refresh_token` и `access_token`.
5. Backend определяет нужный `calendar_id`, создаёт или обновляет запись в `calendar_connections`.
6. Backend запускает первичный full sync.

### Что должен делать backend

- генерировать OAuth URL с корректным `state`
- принимать callback и валидировать `state`
- хранить токены и метаданные подключения
- выполнять full sync и incremental sync
- подписываться на `events.watch`
- продлевать истекающие push-каналы
- обрабатывать инвалидированный `sync_token`

### Основные данные

- `google_event_id` в `calendar_events`
- `google_instance_event_id` в `calendar_event_instances`
- `sync_token` в `calendar_connections`
- `last_full_sync_at` в `calendar_connections`
- `push_channel_id`, `push_resource_id`, `push_channel_expires_at`

---

## 2. Telegram Bot API и конструктор ботов

### Назначение

- onboarding терапевта
- клиентские и терапевтические меню
- привязка клиента к встрече
- подтверждение оплаты
- отправка reminder

### Принцип

- Telegram остаётся основным интерфейсом для пользователей.
- Конструктор управляет сценарием диалога, но не хранит продуктовую логику.
- Любое значимое действие пользователя должно приводить к вызову backend API.

### Что остаётся в конструкторе

- тексты сообщений
- кнопки и ветвления
- отображение списков и результатов
- сбор пользовательского ввода
- вызов backend endpoint и обработка ответа

### Что уходит в backend

- проверка прав доступа
- работа с PostgreSQL
- получение списка встреч для привязки
- подтверждение оплаты
- сохранение настроек терапевта
- создание и пересчёт reminder
- работа с Google Calendar

### Основные данные Telegram-контура

- `therapists.telegram_private_chat_id`
- `clients.telegram_user_id`
- `client_chat_links.group_chat_id`
- `notification_jobs.target_chat_id`

---

## 3. PostgreSQL

### Назначение

- основная база данных системы
- хранение состояния всех бизнес-сущностей
- хранение очереди `notification_jobs`
- поддержка ограничений целостности и производительных выборок

### Хранит

- терапевтов и их настройки
- клиентов и связи клиент -> терапевт
- связи Telegram-чатов
- подключения Google Calendar
- родительские события и инстансы встреч
- статусы оплат
- очередь уведомлений

### Принцип

- PostgreSQL является источником истины по данным системы.
- Backend работает с БД через явную доменную логику.
- Прямой доступ конструктора к БД не используется.

---

## 4. Backend API

## Задача backend

Backend заменяет `Make` и становится единой точкой входа для интеграций и бизнес-логики.

Он должен:

1. Принимать запросы от конструктора ботов.
2. Выполнять операции с PostgreSQL.
3. Работать с Google Calendar API.
4. Запускать фоновые процессы sync и ведение очереди reminder; доставку текста reminder в Telegram в v1 выполняет конструктор (см. раздел про поток reminder ниже).
5. Отдавать конструктору стабильный и предсказуемый HTTP API.

## Общие требования к API

- Формат: JSON over HTTP.
- Версионирование: `/api/v1/...`.
- Аутентификация запросов **конструктора** к `/api/v1/bot/*`: секрет конструктора, например `Authorization: Bearer <BOT_CONSTRUCTOR_SECRET>` или `X-Bot-Api-Token: <BOT_CONSTRUCTOR_SECRET>`.
- Аутентификация **внутренних** вызовов к `/api/v1/internal/*`: **отдельный** секрет, например `Authorization: Bearer <INTERNAL_API_SECRET>` или `X-Internal-Api-Token: <INTERNAL_API_SECRET>` (не смешивать с секретом конструктора).
- Для повторяемых действий обязательна идемпотентность.
- Для ошибок используется единый формат ответа.

Пример структуры ошибки:

```json
{
  "error": {
    "code": "CLIENT_ALREADY_BOUND",
    "message": "Клиент уже привязан к другой встрече"
  }
}
```

## Endpoint'ы для конструктора ботов

Ниже минимальный API, который нужен для интеграции с бот-конструктором. Для **матрицы сценариев BotHelp → вызовы API → переменные** см. `docs/constructor_scenarios_api.md`.

### 4.1. Терапевт и настройки

`POST /api/v1/bot/therapists/upsert-profile`

Назначение:
- создать терапевта при первом входе;
- обновить `name`, `telegram_private_chat_id`;
- сохранить `therapists.timezone`: либо из явного поля `timezone` (IANA), либо вычислив его по полю `city` (строка города из бота: геокодинг → координаты → IANA), см. `docs/automation.md`, разделы 8.3 и 9.1;
- сохранить настройки `payment_reminder_timing`, `payment_reminder_offset_minutes`.

Поле `therapists.email` в БД в v1 **не обязательно**: в теле `upsert-profile` можно не передавать email — тогда в PostgreSQL остаётся `NULL`, пока не появится отдельное требование продукта.

Минимальное тело (таймзона явно):

```json
{
  "telegram_user_id": 123456789,
  "telegram_private_chat_id": 123456789,
  "name": "Имя терапевта",
  "timezone": "Europe/Moscow",
  "payment_reminder_timing": "after",
  "payment_reminder_offset_minutes": 0
}
```

Вариант из бота по городу (без `timezone` в теле):

```json
{
  "telegram_user_id": 123456789,
  "telegram_private_chat_id": 123456789,
  "name": "Имя терапевта",
  "city": "Новосибирск",
  "payment_reminder_timing": "after",
  "payment_reminder_offset_minutes": 0
}
```

Если переданы и `timezone`, и `city`, приоритет у валидного `timezone` (город не используется для перезаписи). Ошибки разрешения города: `TIMEZONE_NOT_FOUND`, `TIMEZONE_AMBIGUOUS` (детали — в `docs/automation.md`).

### 4.2. Получение ссылки на Google OAuth

`POST /api/v1/bot/therapists/{therapist_id}/google/oauth-url`

Назначение:
- вернуть `auth_url`, по которому терапевт перейдёт для подключения Google Calendar.

Ответ:

```json
{
  "auth_url": "https://accounts.google.com/...",
  "state": "opaque-state"
}
```

### 4.3. Регистрация чата терапевта с клиентом

`POST /api/v1/bot/client-links/register-chat`

Назначение:
- создать или обновить `client_chat_links` после того, как терапевт добавил бота в общий чат с клиентом;
- автоматически создать или найти `clients` и сразу записать `client_id` в связку чата.

Минимальное тело:

```json
{
  "group_chat_id": -1001234567890,
  "therapist_id": "uuid",
  "client_telegram_user_id": 55555555,
  "chat_title": "Имя чата"
}
```

### 4.4. Список клиентов терапевта

`GET /api/v1/bot/therapists/{therapist_id}/clients`

Назначение:
- вернуть список клиентов для меню **клиенты и напоминания**.

### 4.5. Изменение флага напоминаний у клиента

`PATCH /api/v1/bot/clients/{client_id}/reminders`

Назначение:
- включить или выключить `send_payment_reminders`.

Минимальное тело:

```json
{
  "send_payment_reminders": false
}
```

### 4.6. Активность клиента

`PATCH /api/v1/bot/clients/{client_id}`

Назначение:
- выставить `clients.is_active` (деактивация при прекращении работы с клиентом или обратная активация).

Минимальное тело:

```json
{
  "is_active": false
}
```

Детали — `docs/automation.md`, §9.5a.

### 4.7. Активность терапевта

`PATCH /api/v1/bot/therapists/{therapist_id}`

Назначение:
- выставить `therapists.is_active` по событию из конструктора (удаление чата с ботом, отмена подписки и т.д. — конкретный триггер в сценарии BotHelp).

Минимальное тело:

```json
{
  "is_active": false
}
```

Детали — `docs/automation.md`, §9.5b.

### 4.8. Список встреч, доступных для привязки

`GET /api/v1/bot/clients/{client_id}/available-calendar-events`

Назначение:
- вернуть встречи, которые можно показать терапевту в списке выбора.

Backend обязан фильтровать:

- только встречи терапевта этого клиента;
- только `calendar_events.client_id IS NULL`;
- только встречи, у которых есть будущий `calendar_event_instance`;
- без дублей recurring-серий.

Пример ответа:

```json
{
  "items": [
    {
      "calendar_event_id": "uuid",
      "title": "Сессия 17 апреля",
      "next_instance_start_at": "2026-04-17T10:00:00Z"
    }
  ]
}
```

Поле `title` соответствует `calendar_events.summary` (заполняется при sync из Google Calendar).

### 4.9. Привязка клиента к встрече

`POST /api/v1/bot/calendar-events/{calendar_event_id}/bind-client`

Назначение:
- привязать клиента к `calendar_event`;
- при необходимости перепривязать клиента на новую встречу через тот же endpoint;
- пересчитать reminder для связанных инстансов.

Правило:
- целевая встреча должна быть без текущей привязки (`calendar_events.client_id IS NULL`);
- если у клиента уже была ранее привязанная встреча, backend снимает привязку с неё и переносит её на новую.

Минимальное тело:

```json
{
  "client_id": "uuid"
}
```

### 4.10. Подтверждение оплаты

`POST /api/v1/bot/calendar-event-instances/{instance_id}/confirm-payment`

Назначение:
- установить `is_paid = true`;
- записать `payment_confirmed_at`;
- отменить неотправленный reminder.

Минимальное тело:

```json
{
  "confirmed_by": "client"
}
```

### 4.11. Получение состояния для меню терапевта

`GET /api/v1/bot/therapists/{therapist_id}/dashboard`

Назначение:
- вернуть компактные данные для главного меню или summary-экрана:
  - подключён ли Google Calendar;
  - сколько активных клиентов;
  - текущие настройки reminder.

### 4.12. Список due-задач payment reminder

`GET /api/v1/bot/therapists/{therapist_id}/notification-jobs/due`

Назначение:
- отдать конструктору задачи `payment_reminder` со статусом `pending`, у которых наступил срок `scheduled_for`, чтобы сценарий бота отправил сообщение в `target_chat_id` и зафиксировал результат через §4.13.

Детали ответа — `docs/automation.md`, §9.10.

### 4.13. Фиксация отправки reminder

`POST /api/v1/bot/notification-jobs/{job_id}/mark-sent` — успешная отправка (идемпотентно).

`POST /api/v1/bot/notification-jobs/{job_id}/mark-failed` — ошибка доставки, тело с полем `last_error`.

Спецификация — `docs/automation.md`, §9.11–9.12.

## Внутренние endpoint'ы backend

Эти endpoint'ы не вызывает конструктор напрямую, но они нужны backend-слою.

### Google OAuth callback

`GET /api/v1/google/oauth/callback`

Назначение:
- принять `code`, обменять его на токены, сохранить их в **зашифрованном** виде в `calendar_connections.google_oauth_credentials_encrypted`, завершить подключение календаря.

### Google push webhook

`POST /api/v1/google/calendar/webhook`

Назначение:
- принимать push-уведомления от Google о необходимости sync.

### Внутренний trigger sync

`POST /api/v1/internal/calendar-connections/{id}/sync`

Назначение:
- запускать full или incremental sync по cron, webhook или вручную.

Аутентификация: только **`INTERNAL_API_SECRET`** (заголовки как в `docs/automation.md`, §3.2 и §10.3), не секрет конструктора.

---

## Потоки интеграций

## Sync поток

`Google Calendar -> Backend -> PostgreSQL`

1. Backend получает изменения календаря.
2. Backend обновляет `calendar_events` и `calendar_event_instances`.
3. Backend пересчитывает `notification_jobs`.

## Reminder поток

`PostgreSQL -> Backend API -> Конструктор ботов -> Telegram`

1. Backend (триггеры/доменная логика) создаёт и обновляет `notification_jobs` (`scheduled_for`, `pending` / `cancelled` и т.д.).
2. Конструктор периодически вызывает `GET .../notification-jobs/due`, формирует **текст** сообщения в сценарии и отправляет его в чат клиента через Telegram.
3. Конструктор вызывает `mark-sent` или `mark-failed`; backend обновляет `status`, `sent_at`, `last_error`.

## Payment confirmation поток

`Telegram / конструктор -> Backend -> PostgreSQL`

1. Клиент нажимает кнопку в Telegram.
2. Конструктор вызывает backend endpoint подтверждения оплаты.
3. Backend обновляет `calendar_event_instances` и отменяет reminder при необходимости.

