# Системная спецификация backend (автоматизации и интеграции)

Документ описывает поведение backend-слоя, достаточное для генерации кода: контракты HTTP API, схемы данных на входе и выходе, алгоритмы синхронизации Google Calendar, расчёта и пересчёта `notification_jobs`, фоновые процессы и инварианты.

Связанные документы:

- доменные сценарии: `docs/business_logic.md`
- таблицы PostgreSQL: `docs/data_model.md`
- обзор интеграций: `docs/integrations.md`
- матрица сценариев BotHelp → API (для конструктора): `docs/constructor_scenarios_api.md`
- SQL-референс очереди (опционально для v1): `docs/sql/notification_jobs_v1.sql`, `docs/sql/payment_reminder_jobs_trigger_v1.sql`

---

## 1. Назначение и границы

### 1.1. Назначение

Backend:

- принимает запросы от **конструктора ботов** (HTTP JSON);
- хранит состояние в **PostgreSQL**;
- синхронизирует расписание из **Google Calendar** (источник истины по времени встреч);
- планирует **payment reminder** через очередь `notification_jobs`; отправку сообщения в Telegram по тексту сценария выполняет **конструктор ботов**, backend фиксирует факт отправки через API (см. §7.6 и §9.10–9.12).

### 1.2. Вне области документа (v1)

- ежедневный digest для терапевта;
- изменение событий в Google Calendar со стороны продукта;
- собственная платёжная интеграция.

### 1.3. Архитектурный контур

`Конструктор ботов → Backend HTTP API → PostgreSQL`

`Backend → Google Calendar API` (OAuth, sync, watch)

`Конструктор ботов → Telegram` (текст и отправка payment reminder); `Backend` — очередь и статусы `notification_jobs`

Конструктор **не** обращается к БД напрямую.

---

## 2. Термины и идентификаторы

| Термин | Смысл |
|--------|--------|
| `therapist_id` | UUID терапевта в PostgreSQL |
| `client_id` | UUID клиента в PostgreSQL |
| `calendar_connection_id` | UUID подключения Google Calendar |
| `calendar_event_id` | UUID родительского события в продукте |
| `calendar_event_instance_id` | UUID конкретного вхождения встречи |
| `google_event_id` | строковый id события в Google |
| `google_instance_event_id` | строковый id инстанса в Google (для recurring) |
| `sync_token` | opaque token incremental sync Google (`events.list`) |

Все моменты времени в API — **RFC 3339** в **UTC**, если не указано иное (`timestamptz`).

---

## 3. Общие правила HTTP API

### 3.1. Базовый префикс

- публичный API для конструктора: `/api/v1/bot/...`
- внутренние endpoint'ы: `/api/v1/google/...`, `/api/v1/internal/...`

### 3.2. Заголовки

| Заголовок | Обязательность | Назначение |
|-----------|----------------|------------|
| `Content-Type: application/json` | для тел с JSON | тело запроса |
| `Authorization: Bearer <token>` **или** `X-Bot-Api-Token: <token>` | для `/api/v1/bot/*` | секрет **конструктора ботов** (отдельно от internal) |
| `Authorization: Bearer <token>` **или** `X-Internal-Api-Token: <token>` | для `/api/v1/internal/*` | секрет **внутренних** вызовов (cron, worker sync и т.п.); **не** использовать тот же ключ, что для `/api/v1/bot/*` |

Рекомендация по реализации: для bot- и internal-префиксов проверять разные значения из конфигурации (`BOT_CONSTRUCTOR_SECRET` и `INTERNAL_API_SECRET` или аналогичные имена).

### 3.3. Успешный ответ

HTTP `2xx`, тело — JSON-объект (см. разделы по endpoint'ам).

### 3.4. Ошибка

Единый формат:

```json
{
  "error": {
    "code": "SNAKE_CASE_CODE",
    "message": "Человекочитаемое описание",
    "details": {}
  }
}
```

Рекомендуемые HTTP-коды:

| HTTP | Когда |
|------|--------|
| 400 | невалидное тело, нарушение бизнес-правила; также типичный код для `TIMEZONE_NOT_FOUND` / `TIMEZONE_AMBIGUOUS` при `upsert-profile` |
| 401 | неверный/отсутствующий токен |
| 404 | сущность не найдена или нет доступа (не раскрывать факт существования без необходимости) |
| 409 | конфликт состояния (например, гонка при привязке) |
| 500 | внутренняя ошибка |

### 3.5. Идемпотентность

Для действий из Telegram (повтор webhook, двойной клик) endpoint'ы должны быть **идемпотентны по смыслу**:

- `confirm-payment`: повторный вызов не меняет состояние и возвращает тот же итог;
- `bind-client`: повтор с теми же параметрами — no-op;
- `register-chat`: upsert по `(group_chat_id, therapist_id)` или эквивалент;
- `mark-sent` / `mark-failed` для `notification_jobs`: повтор для уже терминального статуса — no-op или стабильный ответ без деградации данных.

---

## 4. Модель данных (сводка для реализации)

Полная схема полей — в `docs/data_model.md`. Здесь — инварианты, влияющие на код.

### 4.1. Ключевые таблицы

- `therapists` — настройки reminder (`payment_reminder_timing`, `payment_reminder_offset_minutes`), `timezone`, `is_active`
- `clients` — `therapist_id`, `send_payment_reminders`, `is_active`
- `client_chat_links` — связь `group_chat_id` ↔ клиент/терапевт
- `calendar_connections` — `calendar_id`, `sync_token`, поля push-watch, **зашифрованный blob** `google_oauth_credentials_encrypted` (см. `docs/data_model.md`)
- `calendar_events` — родитель Google-события, `client_id` (привязка клиента к серии/событию)
- `calendar_event_instances` — конкретные встречи, `actual_start_at`, `actual_end_at`, `status`, `is_paid`
- `notification_jobs` — очередь reminder, `scheduled_for`, `status`

### 4.2. Инварианты

1. Google Calendar **не** изменяется продуктом.
2. Для одного `calendar_event_instance_id` и `job_type = payment_reminder` в очереди не более **одной** актуальной записи (уникальность — см. SQL-референс).
3. Привязка клиента к встрече: выбирается только `calendar_events`, у которых `client_id IS NULL`; при перепривязке на новую встречу backend **снимает** `client_id` с прежней встречи этого клиента в одной транзакции.

---

## 5. Google Calendar API — контракт для реализации

Официальная документация: [Calendar API](https://developers.google.com/workspace/calendar/api/guides/overview).

### 5.1. OAuth 2.0 (авторизация терапевта)

Используется поток **authorization code** для установленного backend-клиента Google.

Типичные query-параметры при редиректе пользователя на Google:

| Параметр | Назначение |
|----------|------------|
| `client_id` | OAuth client id приложения |
| `redirect_uri` | URL callback backend (`GET /api/v1/google/oauth/callback`) |
| `response_type` | `code` |
| `scope` | минимум доступа к календарю, например `https://www.googleapis.com/auth/calendar` (или более узкий, если выбран) |
| `access_type` | `offline` (нужен `refresh_token`) |
| `prompt` | `consent` при первом подключении, если требуется гарантированный refresh token |
| `state` | непрозрачная строка: **обязательно** связать с `therapist_id` и защитить от CSRF |

Обмен `code` на токены: `POST https://oauth2.googleapis.com/token` с полями `code`, `client_id`, `client_secret`, `redirect_uri`, `grant_type=authorization_code`.

Дальнейшее обновление access token: `grant_type=refresh_token`.

### 5.2. Базовый URL Calendar API

`https://www.googleapis.com/calendar/v3/`

### 5.3. `events.list` — параметры, используемые в продукте

Метод: `GET /calendars/{calendarId}/events`

| Параметр | Когда задаётся | Смысл |
|----------|----------------|--------|
| `calendarId` | всегда | id календаря из `calendar_connections.calendar_id` |
| `syncToken` | incremental sync | если задан и валиден — вернуть только изменения с прошлого sync |
| `timeMin`, `timeMax` | full sync / первичная загрузка | окно по времени для полной выборки (UTC) |
| `singleEvents` | full и при развёртке | `true` — развернуть recurring в отдельные вхождения (если выбрана эта стратегия) |
| `showDeleted` | full/incremental | учитывать удалённые/отменённые для согласования локального состояния |
| `pageToken` | всегда при пагинации | обход страниц ответа |
| `maxResults` | опционально | размер страницы (например 250) |

Поведение при ошибке `410 Gone` на incremental запросе с `syncToken`: токен инвалидирован — выполнить **full sync** за требуемый горизонт, затем получить новый `sync_token`.

### 5.4. `events.watch` — push-уведомления

Метод: `POST https://www.googleapis.com/calendar/v3/calendars/{calendarId}/events/watch`

Тело (сущность `Channel`):

| Поле | Смысл |
|------|--------|
| `id` | уникальный id канала (UUID, до 64 символов); при продлении — **новый** id |
| `type` | `web_hook` |
| `address` | публичный HTTPS URL backend: `POST /api/v1/google/calendar/webhook` |
| `token` | опционально: секрет для проверки заголовка `X-Goog-Channel-Token` |
| `params` | опционально: `ttl` не используется в v1; срок жизни задаётся ответом |

Ответ содержит `resourceId`, `expiration` (ms с эпохи) — сохранить в `calendar_connections.push_resource_id`, `push_channel_expires_at`.

### 5.5. Webhook от Google (уведомление о изменениях)

Google шлёт уведомление на `address`. Backend не должен полагаться на тело: после валидации заголовков запускается **incremental sync** (или внутренний `POST /api/v1/internal/calendar-connections/{id}/sync`).

Заголовки (типичные): `X-Goog-Channel-Id`, `X-Goog-Resource-Id`, `X-Goog-Resource-State`, `X-Goog-Channel-Token`.

---

## 6. Алгоритм синхронизации календаря

### 6.1. Входные данные

- запись `calendar_connections` с `calendar_id`, токенами, `sync_token`, `last_full_sync_at`
- флаг активности терапевта `therapists.is_active = true` (иначе sync не выполнять)

### 6.2. Full sync

**Цель:** заполнить/обновить `calendar_events` и `calendar_event_instances` за горизонт `[timeMin, timeMax]`.

Рекомендуемые шаги:

1. Определить `timeMin`/`timeMax` (например, `now - 7d` … `now + 365d` — фиксируется в конфиге продукта).
2. Вызвать `events.list` с `singleEvents=true`, `showDeleted=true`, пагинацией.
3. Для каждого item из ответа:
   - upsert в `calendar_events` по `(calendar_connection_id, google_event_id)`;
   - upsert инстансов в `calendar_event_instances` по правилам маппинга (см. 6.4).
4. Сохранить новый `sync_token` из последнего успешного **incremental-capable** ответа (если API отдал `nextSyncToken` на полном проходе — зависит от параметров; иначе после первого incremental).
5. Обновить `last_full_sync_at = now()`.
6. Запустить пересчёт reminder: `recompute_notifications_for_connection(connection_id)` (см. раздел 7).

### 6.3. Incremental sync

1. Если `sync_token` отсутствует — выполнить full sync.
2. Иначе вызвать `events.list` с `syncToken=<sync_token>`, `showDeleted=true`, пагинация.
3. Применить дельты к `calendar_events` / `calendar_event_instances`.
4. Обновить `sync_token` из ответа.
5. Пересчёт reminder для затронутых сущностей (минимум: по затронутым `calendar_event_id`, практично — по всему connection при небольшом объёме).

### 6.4. Маппинг полей Google → доменная модель (требования к коду)

На уровне родителя `calendar_events`:

- `google_event_id` ← `event.id`
- `summary` ← `event.summary` (если в Google пусто — допустимо `NULL` или запасной title на стороне backend)
- `status` ← `event.status`
- `is_recurring` ← наличие `event.recurrence` или признак серии в API
- `updated_at_google` ← `event.updated` (RFC3339)

На уровне инстанса `calendar_event_instances`:

- `google_instance_event_id` ← для развёрнутых событий: стабильный id вхождения из API (или составной ключ, если API отдаёт иначе — зафиксировать одну стратегию в коде и не смешивать)
- `actual_start_at` / `actual_end_at` ← `start.dateTime`/`end.dateTime` или all-day правила
- `original_start_at` — для recurring-серий при отличии от слота
- `status` — продуктовый статус встречи; отмена: если `event.status=cancelled` или эквивалент в instance

**Важно:** точные правила развёртки recurring и обработки исключений серии должны быть единообразны между full и incremental; при изменении правил — миграция данных вне scope v1.

---

## 7. Алгоритм расчёта и пересчёта payment reminder (`notification_jobs`)

### 7.1. Входные параметры (из БД)

Для инстанса встречи `cei` и связанного родителя `ce` (через `calendar_event_id`):

| Поле | Источник |
|------|----------|
| `therapist` | по цепочке `calendar_events` → `calendar_connections` → `therapists` |
| `client_id` | `calendar_events.client_id` |
| `client.send_payment_reminders` | `clients` |
| `client.is_active` | `clients` |
| `therapist.is_active` | `therapists` |
| `payment_reminder_timing` | `before` или `after` |
| `payment_reminder_offset_minutes` | целое ≥ 0 |
| `actual_start_at`, `actual_end_at` | `calendar_event_instances` |
| `instance.status` | текст; отмена если в нижнем регистре одно из: `cancelled`, `canceled`, `cancel` |
| `is_paid` | `calendar_event_instances` |

### 7.2. Условие «напоминание нужно»

Напоминание создаётся только если одновременно:

1. `therapist.is_active = true`
2. `client_id` не `NULL` и `clients.is_active = true`
3. `send_payment_reminders = true`
4. инстанс не отменён (по правилу статуса выше)
5. `is_paid = false`
6. существует момент `scheduled_for` в будущем относительно «логического сейчас» (UTC) — см. 7.3

Если условие не выполнено — задача `payment_reminder` для этого инстанса должна стать `cancelled` (или удаляться — политика v1: **предпочтительно** `cancelled` для аудита).

### 7.3. Расчёт `scheduled_for` (UTC)

Пусть `offset = payment_reminder_offset_minutes` (целое).

Формула опирается **только** на моменты начала/конца встречи в календаре (`actual_start_at` / `actual_end_at` как `timestamptz` в UTC). **Таймзона терапевта в профиле и таймзона клиента на расчёт `scheduled_for` не влияют**; напоминание адресовано клиенту (чат из `target_chat_id`), момент отправки — относительно границы встречи, а не «локальных часов» абонента.

Если `payment_reminder_timing = before`:

```
scheduled_for = actual_start_at - (offset minutes)
```

Если `payment_reminder_timing = after`:

```
scheduled_for = actual_end_at + (offset minutes)
```

Если `scheduled_for` в прошлом на момент расчёта — напоминание не планируется (задача `cancelled`).

### 7.4. Поле `target_chat_id`

Для отправки в общий чат с клиентом: `client_chat_links.group_chat_id` для данного `client_id` (если несколько — выбрать каноническое правило: последний активный / единственный; зафиксировать в коде).

### 7.5. Пересчёт при событиях

Функция `recompute_reminder_for_instance(calendar_event_instance_id)` должна вызываться после:

- insert/update `calendar_event_instances` (время, статус, оплата)
- insert/update `calendar_events` (смена `client_id`)
- insert/update `clients` (`send_payment_reminders`, `is_active`)
- insert/update `therapists` (настройки reminder, `is_active`)

Реализация: доменный сервис в коде **или** триггеры PostgreSQL (как в SQL-референсе) — на выбор команды, но поведение должно совпасть с формулами выше.

### 7.6. Доставка payment reminder (конструктор + backend)

В v1 **текст сообщения и вызов Telegram API** выполняет конструктор ботов (сценарий, BotHelp и т.п.). Backend отвечает за **планирование** (`scheduled_for`, `pending`/`cancelled`) и за **фиксацию факта отправки или сбоя** по вызовам из конструктора (см. §9.10–9.12).

Рекомендуемый цикл конструктора (например каждые 1–5 минут на терапевта или глобально по политике продукта):

1. Вызвать `GET /api/v1/bot/therapists/{therapist_id}/notification-jobs/due` и получить список задач с `target_chat_id`, контекстом встречи (для текста в сценарии).
2. Для каждой задачи отправить сообщение клиенту в Telegram средствами конструктора.
3. Успех: вызвать `POST .../notification-jobs/{job_id}/mark-sent` (идемпотентно).
4. Ошибка доставки: вызвать `POST .../notification-jobs/{job_id}/mark-failed` с кратким `last_error` (политика ретраев — в конфиге конструктора/backend).

---

## 8. Алгоритмы доменных операций (кроме календаря)

### 8.1. `register-chat` (бот добавлен в групповой чат)

**Цель:** создать/найти `clients`, upsert `client_chat_links`.

Шаги:

1. Валидировать токен и права.
2. Найти или создать `clients` для пары `(therapist_id, опционально telegram user)` — правило имени: из `chat_title` или заглушка, если политика продукта позволяет.
3. Upsert `client_chat_links` по `(group_chat_id, selected_therapist_id)`.
4. Вернуть `client_id` и `link_id`.

### 8.2. `bind-client` (привязка / перепривязка встречи)

Вход: `calendar_event_id`, `client_id`.

В транзакции:

1. Проверить, что клиент принадлежит тому же терапевту, что и `calendar_event`.
2. Найти текущую встречу клиента: `select id from calendar_events where client_id = :client_id` (ожидается 0 или 1 строка; если больше — ошибка данных/миграция).
3. Если найдена и `id != calendar_event_id`: обнулить `client_id` у старой строки.
4. Проверить, что целевая встреча имеет `client_id IS NULL`.
5. Установить `client_id` у целевой встречи.
6. Вызвать пересчёт reminder для затронутых `calendar_event_id` (старый и новый родитель).

### 8.3. `therapists/upsert-profile` (профиль и таймзона)

**Цель:** создать или обновить запись `therapists`, в том числе поле `timezone` (IANA).

**Источник таймзоны** (взаимоисключающий приоритет):

1. Если в теле запроса передано непустое поле `timezone` и значение — **валидный** идентификатор IANA (например `Europe/Moscow`) — сохранить его как `therapists.timezone` и **не** вызывать геокодинг по городу (явное значение от приоритетных интеграций или ручной ввод в конструкторе).
2. Иначе, если передано непустое поле `city` (строка с названием города, как пришло из бота) — вычислить IANA-таймзону по алгоритму разрешения города (п. «Разрешение города» ниже) и сохранить результат в `therapists.timezone`.
3. Если оба поля пустые или отсутствуют — вернуть `VALIDATION_ERROR` (для записи профиля терапевта таймзона обязательна).

**Разрешение города в IANA-таймзону** (рекомендуемая реализация):

1. Нормализовать строку `city` (trim, схлопывание пробелов; при необходимости — единый регистр только для сравнения с кэшем, исходную строку в БД не хранить, если нет отдельного поля `city`).
2. Геокодинг: по строке города получить координаты (широта/долгота). Подходящие внешние источники: **Nominatim (OpenStreetMap)** с соблюдением [политики использования](https://operations.osmfoundation.org/policies/nominatim/), либо **Google Geocoding API** (если в продукте уже есть ключ Google для Calendar — унификация биллинга/квот).
3. По координатам определить IANA-зону: библиотека уровня приложения (например `timezonefinder` в Python, аналог в другом стеке) **или** **Google Time Zone API** по `lat,lng` и опорному UTC-времени (учёт DST).
4. Если геокодинг не дал результата или таймзону определить нельзя — ошибка `TIMEZONE_NOT_FOUND` (в `details` — кратко причина).
5. Если геокодинг даёт несколько равноправных кандидатов в разных странах/континентах и политика продукта не выбирает один автоматически — ошибка `TIMEZONE_AMBIGUOUS` (в `details` — список вариантов `{ "city_label", "timezone" }` для возможного уточнения в боте в будущем; в v1 допустимо брать первый результат при явной фиксации политики в коде и логировании).

**Идемпотентность:** повторный запрос с теми же `telegram_user_id` / идентификатором терапевта и теми же полями профиля не должен создавать дубликатов; повторное разрешение того же `city` обновляет `timezone` тем же значением (если внешний сервис вернул тот же итог).

**Пересчёт `notification_jobs` и смена `therapists.timezone`:** при одной только смене поля `timezone` **пересчёт очереди не требуется**, потому что `scheduled_for` в §7.3 считается от `actual_start_at` / `actual_end_at` в UTC и **не использует** `therapists.timezone`. Пересчёт выполнять при изменении **`payment_reminder_timing`**, **`payment_reminder_offset_minutes`** или **`is_active`** терапевта (как и перечислено в §7.5).

---

## 9. HTTP API — спецификация endpoint'ов (конструктор)

Ниже — контракты для генерации кода. Тела — JSON.

### 9.1. `POST /api/v1/bot/therapists/upsert-profile`

**Вход:**

```json
{
  "telegram_user_id": 123456789,
  "telegram_private_chat_id": 123456789,
  "name": "string",
  "timezone": "Europe/Moscow",
  "city": "Казань",
  "payment_reminder_timing": "before",
  "payment_reminder_offset_minutes": 60
}
```

Поля `timezone` и `city` — **опциональны по отдельности**, но для установки/обновления таймзоны терапевта на момент запроса должно выполняться правило из раздела **8.3** (либо валидный IANA в `timezone`, либо непустой `city` для разрешения). Конструктор бота может передавать только `city` (ответ пользователя на вопрос «ваш город»), без поля `timezone`.

**Выход:**

```json
{
  "therapist_id": "uuid",
  "created": true
}
```

Опционально (если удобно для отладки и сценариев бота) в ответ можно добавить `resolved_timezone` — фактически сохранённая IANA-строка (особенно когда на входе был только `city`).

**Ошибки:** `VALIDATION_ERROR`, `CONFLICT`, `TIMEZONE_NOT_FOUND`, `TIMEZONE_AMBIGUOUS` (см. раздел 8.3).

---

### 9.2. `POST /api/v1/bot/therapists/{therapist_id}/google/oauth-url`

**Вход:** `{}` (опционально `{ "redirect_return": "..." }` — если нужно продукту)

**Выход:**

```json
{
  "auth_url": "https://accounts.google.com/...",
  "state": "opaque"
}
```

---

### 9.3. `POST /api/v1/bot/client-links/register-chat`

**Вход:**

```json
{
  "group_chat_id": -1001234567890,
  "therapist_id": "uuid",
  "client_telegram_user_id": 55555555,
  "chat_title": "string"
}
```

**Выход:**

```json
{
  "client_id": "uuid",
  "client_chat_link_id": "uuid"
}
```

---

### 9.4. `GET /api/v1/bot/therapists/{therapist_id}/clients`

**Выход:**

```json
{
  "items": [
    {
      "client_id": "uuid",
      "name": "string",
      "send_payment_reminders": true,
      "is_active": true
    }
  ]
}
```

---

### 9.5. `PATCH /api/v1/bot/clients/{client_id}/reminders`

**Вход:**

```json
{
  "send_payment_reminders": false
}
```

**Выход:** `{ "ok": true }` + последующий пересчёт reminder (синхронно или через очередь — допускается async, но тогда документировать job id в v2).

---

### 9.5a. `PATCH /api/v1/bot/clients/{client_id}`

**Вход (частичное обновление):**

```json
{
  "is_active": false
}
```

**Назначение:** выставить `clients.is_active` (деактивация/реактивация клиента из меню терапевта). После успеха — пересчёт reminder по затронутым инстансам (как при изменении `clients` в §7.5).

**Выход:** `{ "ok": true }`

---

### 9.5b. `PATCH /api/v1/bot/therapists/{therapist_id}`

**Вход:**

```json
{
  "is_active": false
}
```

**Назначение:** выставить `therapists.is_active` по событию из конструктора (например, терапевт удалил личный чат с ботом / отмена подписки — конкретный триггер задаётся сценарием BotHelp). После `is_active = false` не выполнять sync и не планировать новые pending reminder для этого терапевта.

**Выход:** `{ "ok": true }`

---

### 9.6. `GET /api/v1/bot/clients/{client_id}/available-calendar-events`

**Выход:**

```json
{
  "items": [
    {
      "calendar_event_id": "uuid",
      "title": "string",
      "next_instance_start_at": "2026-04-17T10:00:00Z"
    }
  ]
}
```

Поле `title` в ответе соответствует `calendar_events.summary` в БД (после sync из Google).

Фильтры — см. `docs/integrations.md` и инварианты раздела 4.

---

### 9.7. `POST /api/v1/bot/calendar-events/{calendar_event_id}/bind-client`

**Вход:**

```json
{
  "client_id": "uuid"
}
```

**Выход:**

```json
{
  "bound_calendar_event_id": "uuid",
  "unbound_calendar_event_id": "uuid | null"
}
```

---

### 9.8. `POST /api/v1/bot/calendar-event-instances/{instance_id}/confirm-payment`

**Вход:**

```json
{
  "confirmed_by": "client"
}
```

**Выход:** `{ "ok": true }`

Побочный эффект: `is_paid=true`, `payment_confirmed_at=now()`, пересчёт reminder.

---

### 9.9. `GET /api/v1/bot/therapists/{therapist_id}/dashboard`

**Выход (пример):**

```json
{
  "google_connected": true,
  "active_clients_count": 3,
  "payment_reminder_timing": "after",
  "payment_reminder_offset_minutes": 0
}
```

---

### 9.10. `GET /api/v1/bot/therapists/{therapist_id}/notification-jobs/due`

**Назначение:** выдать список задач `payment_reminder` в статусе `pending`, у которых `scheduled_for <= now()`, только для данных терапевта и его клиентов (через цепочку `calendar_event_instances` → `calendar_events` → `calendar_connections`).

**Query (опционально):** `limit` (например по умолчанию 50, максимум — лимит в конфиге backend).

**Выход (пример):**

```json
{
  "items": [
    {
      "notification_job_id": "uuid",
      "calendar_event_instance_id": "uuid",
      "calendar_event_id": "uuid",
      "job_type": "payment_reminder",
      "scheduled_for": "2026-04-17T10:00:00Z",
      "target_chat_id": -1001234567890,
      "event_title": "Сессия",
      "actual_start_at": "2026-04-17T12:00:00Z",
      "actual_end_at": "2026-04-17T13:00:00Z"
    }
  ]
}
```

Поле `event_title` — из `calendar_events.summary` (для подстановок в тексте сценария конструктора).

---

### 9.11. `POST /api/v1/bot/notification-jobs/{job_id}/mark-sent`

**Вход:** `{}`

**Назначение:** зафиксировать успешную отправку reminder конструктором: `status = sent`, `sent_at = now()`, `last_error = null`. Идемпотентно, если задача уже в `sent`.

**Выход:** `{ "ok": true }`

---

### 9.12. `POST /api/v1/bot/notification-jobs/{job_id}/mark-failed`

**Вход:**

```json
{
  "last_error": "Telegram: chat not found"
}
```

**Назначение:** зафиксировать ошибку доставки: `status = failed`, заполнить `last_error`. Повторный вызов не должен портить данные (идемпотентность по смыслу).

**Выход:** `{ "ok": true }`

---

## 10. Внутренние HTTP endpoint'ы

### 10.1. `GET /api/v1/google/oauth/callback`

Query: `code`, `state`, опционально `error`.

Поведение: обмен кода на токены, сериализация в JSON, **шифрование** и запись в `calendar_connections.google_oauth_credentials_encrypted` (см. `docs/data_model.md`), запуск initial sync.

### 10.2. `POST /api/v1/google/calendar/webhook`

Поведение: валидация канала, постановка sync в очередь/немедленный incremental sync.

### 10.3. `POST /api/v1/internal/calendar-connections/{id}/sync`

**Вход:**

```json
{
  "mode": "incremental | full"
}
```

**Выход:** `{ "ok": true, "sync_token_updated": true }`

Доступ: заголовок **`X-Internal-Api-Token`** или **`Authorization: Bearer`** со значением **`INTERNAL_API_SECRET`** (не секрет конструктора). Дополнительно рекомендуется ограничить endpoint приватной сетью или allowlist IP.

---

## 11. Фоновые процессы (обязательные)

| Процесс | Период | Действие |
|---------|--------|----------|
| доставка `payment_reminder` | 1–5 мин (политика конструктора) | конструктор опрашивает `GET .../notification-jobs/due`, шлёт сообщение в Telegram, вызывает `mark-sent` / `mark-failed` |
| `calendar_sync_cron` | 5–15 мин | incremental sync для активных подключений без push |
| `watch_renewal` | ежедневно / по `push_channel_expires_at` | продление `events.watch` до истечения |
| `token_refresh` | по необходимости | обновление access token перед API вызовами (чтение/запись зашифрованного blob в `calendar_connections`) |

---

## 12. Наблюдаемость и логирование

Минимум:

- correlation id на HTTP запрос;
- логировать `calendar_connection_id`, тип sync, длительность, результат;
- логировать результат `mark-sent` / `mark-failed`: `notification_job_id`, `target_chat_id`, итог вызова.

---

## 13. Чеклист согласованности реализации

- [ ] Поведение reminder совпадает с формулами раздела 7.3
- [ ] Смена `therapists.timezone` **не** требует пересчёта `notification_jobs` при неизменных `actual_start_at` / `actual_end_at`
- [ ] Incremental sync обрабатывает `410` и делает full sync
- [ ] `bind-client` перепривязка атомарна и снимает старую привязку
- [ ] Идемпотентность confirm-payment, register-chat, `mark-sent` / `mark-failed`
- [ ] Неактивные терапевт/клиент не получают новые pending reminder
- [ ] OAuth-токены Google только в `google_oauth_credentials_encrypted`; `/api/v1/internal/*` только с `INTERNAL_API_SECRET`
- [ ] При sync заполняется `calendar_events.summary` из Google; в списке доступных встреч `title` = `summary`
