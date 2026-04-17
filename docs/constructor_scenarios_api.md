# Конструктор ботов (BotHelp): сценарии и HTTP API

Документ для **сборки сценариев** в BotHelp: какой внешний запрос к backend вызывать и какие идентификаторы хранить в переменных подписчика/сценария.

**Каноничные контракты** (тела JSON, коды ошибок, идемпотентность): [`docs/automation.md`](automation.md) §9–10 и краткий обзор в [`docs/integrations.md`](integrations.md) §4. Здесь — **матрица и порядок**, без дублирования полных тел запросов.

**Аутентификация** всех вызовов к `/api/v1/bot/*`: секрет конструктора (`Authorization: Bearer` или `X-Bot-Api-Token`, см. [`docs/automation.md`](automation.md) §3.2). Секрет **internal** для cron/sync в сценарии не используется.

---

## Итерация v1: таблица «сценарий → API»

| Сценарий / шаг в BotHelp | Метод и path | Что сохранить в переменных (пример имён) | Зависимости |
|--------------------------|--------------|------------------------------------------|-------------|
| Старт / онбординг терапевта: имя, чат, таймзона или город, настройки reminder | `POST /api/v1/bot/therapists/upsert-profile` | `therapist_id` (из ответа), при необходимости `resolved_timezone` | — |
| Кнопка «Подключить Google Calendar» | `POST /api/v1/bot/therapists/{therapist_id}/google/oauth-url` | `oauth_state` (если нужен в сценарии; `state` в ответе), открыть `auth_url` в браузере | `therapist_id` |
| Меню / сводка после настройки | `GET /api/v1/bot/therapists/{therapist_id}/dashboard` | опционально кэш для UI (`google_connected`, счётчики) | `therapist_id` |
| Событие: бот добавлен в **общий** чат с клиентом | `POST /api/v1/bot/client-links/register-chat` | `client_id`, `client_chat_link_id` | `therapist_id`, `group_chat_id` из события Telegram/BotHelp |
| Меню «клиенты и напоминания»: список | `GET /api/v1/bot/therapists/{therapist_id}/clients` | список для отображения (можно не сохранять) | `therapist_id` |
| Вкл/выкл напоминания об оплате для клиента | `PATCH /api/v1/bot/clients/{client_id}/reminders` | — | `client_id` из шага списка |
| Деактивация / активация клиента | `PATCH /api/v1/bot/clients/{client_id}` | — | `client_id` |
| Деактивация терапевта (удалил личный чат, отписка и т.д.) | `PATCH /api/v1/bot/therapists/{therapist_id}` | — | событие из BotHelp + `therapist_id` |
| Выбор клиента → список встреч для привязки | `GET /api/v1/bot/clients/{client_id}/available-calendar-events` | для следующего шага: выбранный `calendar_event_id` | `client_id` |
| Пользователь выбрал встречу → привязка | `POST /api/v1/bot/calendar-events/{calendar_event_id}/bind-client` | обновить при необходимости текущую привязку в UI | `client_id`, `calendar_event_id` |
| Клиент нажал «оплатил» / кнопка подтверждения | `POST /api/v1/bot/calendar-event-instances/{instance_id}/confirm-payment` | — | `instance_id` из контекста кнопки (данные из сценария или из предыдущего списка) |

---

## Цикл payment reminder (v1)

Планирование (`notification_jobs`, `scheduled_for`, статусы) делает **backend**; **текст** и **отправка** в Telegram — **сценарий BotHelp** (шаги с отправкой сообщения в нужный чат).

Рекомендуемый цикл (периодический запуск по таймеру или по расписанию в конструкторе, например каждые 1–5 минут **на каждого активного терапевта** для которого есть `therapist_id`):

1. `GET /api/v1/bot/therapists/{therapist_id}/notification-jobs/due` — получить `items[]` с полями `notification_job_id`, `target_chat_id`, `event_title`, `actual_start_at`, … (см. [`docs/automation.md`](automation.md) §9.10).
2. Для каждого элемента: сформировать текст в сценарии → отправить сообщение в Telegram в чат `target_chat_id`.
3. Успех: `POST /api/v1/bot/notification-jobs/{job_id}/mark-sent`.
4. Ошибка отправки: `POST /api/v1/bot/notification-jobs/{job_id}/mark-failed` с телом `{ "last_error": "..." }`.

Идемпотентность: повторные `mark-sent` / `mark-failed` не должны ломать данные (см. [`docs/automation.md`](automation.md) §3.5).

---

## Не вызывается из сценариев BotHelp (backend / Google)

Эти вызовы **не** строятся как «внешний запрос» шага BotHelp к вашему API в v1:

| Назначение | Endpoint / механизм | Кто вызывает |
|------------|---------------------|--------------|
| OAuth callback Google | `GET /api/v1/google/oauth/callback` | Браузер / Google redirect |
| Push календаря | `POST /api/v1/google/calendar/webhook` | Google |
| Full / incremental sync | `POST /api/v1/internal/calendar-connections/{id}/sync` | Cron, worker, инфраструктура с **`INTERNAL_API_SECRET`** |
| Обновление access token, продление `events.watch` | Внутренняя логика backend | Процессы из [`docs/automation.md`](automation.md) §11 |

---

## Итерации после v1 (черновик бэклога)

Ниже — **продуктовый задел**, без описанных HTTP-контрактов (кроме уже существующих общих правил). Перед реализацией нужны отдельные спецификации и согласование.

| Тема | Источник в документации | API в репозитории |
|------|-------------------------|-------------------|
| Ежедневный digest для терапевта | [`docs/automation.md`](automation.md) §1.2 | не описан |
| Бронирование / перенос встреч клиентом | [`docs/business_logic.md`](business_logic.md) «Не реализуются» | не описан |
| Собственная платёжная система | [`docs/business_logic.md`](business_logic.md), [`docs/automation.md`](automation.md) §1.2 | не описан |
| CRM, сложная аналитика, multi-therapist | [`docs/business_logic.md`](business_logic.md) | не описан |
| Изменение событий в Google Calendar из продукта | [`docs/automation.md`](automation.md) §1.2 | не описан |

---

## Связанные документы

- [`docs/business_logic.md`](business_logic.md) — пользовательские сценарии и границы v1  
- [`docs/integrations.md`](integrations.md) — роли интеграций и список endpoint для конструктора  
- [`docs/data_model.md`](data_model.md) — поля БД, в т.ч. `notification_jobs`, `calendar_events.summary`  
- [`docs/sql/notification_jobs_v1.sql`](sql/notification_jobs_v1.sql), [`docs/sql/payment_reminder_jobs_trigger_v1.sql`](sql/payment_reminder_jobs_trigger_v1.sql) — референс очереди и триггера  
