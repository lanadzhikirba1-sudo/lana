# Integrations

## Обзор

Система использует 4 ключевые интеграции:
- Google Calendar
- Telegram Bot API
- Supabase
- Make

Распределение роли источников истины:
- расписание: Google Calendar
- данные системы: Supabase
- оркестрация процессов: Make
- интерфейс пользователя: Telegram

---

## 1. Google Calendar

Назначение:
- получение событий календаря
- обработка recurring-серий
- обновление инстансов встреч в данных системы

Принцип:
- Google Calendar является источником истины для расписания.
- Система не создает и не изменяет события в Google Calendar.

### Доступ к календарю терапевта (OAuth)

1. Терапевт в Telegram-боте инициирует действие **«Подключить Google Calendar»** (стартовая настройка или настройки).
2. Бот (через Make) выдаёт терапевту **ссылку на OAuth 2.0 Google** (запрос доступа к Calendar API для приложения проекта).
3. Терапевт в браузере входит в Google и подтверждает доступ.
4. После успешной авторизации **Make** (callback redirect) получает код обмена, обменивает его на **refresh/access token**, определяет **calendar_id** (основной или выбранный календарь) и записывает в Supabase строку в `calendar_connections` для этого терапевта.
5. Все последующие запросы к Google Calendar API выполняются **оркестратором Make** от имени терапевта, используя сохранённые учётные данные; ответы API обновляют `calendar_events`, `calendar_event_instances` и поле `sync_token` (incremental sync).

Важно:
- пользовательский переход на авторизацию — **из браузера по ссылке**, которую выдаёт бот; сам Telegram OAuth не выполняет.

Основные данные:
- `google_event_id` в `calendar_events`
- `google_instance_event_id` в `calendar_event_instances`
- `sync_token` в `calendar_connections` для incremental sync
- `last_full_sync_at` — отметка полного sync при сбросе incremental

---

## 2. Telegram Bot API

Назначение:
- отправка reminder-уведомлений клиентам
- подтверждение оплаты через кнопку
- отправка daily digest терапевту
- меню настроек (digest, payment reminder, список клиентов и флаги напоминаний)

Принцип:
- Telegram является основным интерфейсом взаимодействия пользователей с системой.
- Бот реализуется через Telegram Bot API + Make без отдельного конструктора в первой версии.

Основные данные:
- `telegram_private_chat_id` в `therapists`
- `telegram_user_id` в `clients`
- `group_chat_id` в `client_chat_links`
- `target_chat_id` в `notification_jobs`

---

## 3. Supabase

Назначение:
- основная база данных (PostgreSQL)
- хранение состояния системы
- обработка `notification_jobs` для отправки payment-reminder уведомлений по расписанию

Хранит:
- терапевтов и клиентов
- связи Telegram-чатов
- подключения календаря
- родительские события и инстансы встреч
- статусы оплаты
- очередь уведомлений

Принцип:
- Supabase является источником истины для данных системы.

---

## 4. Make

Назначение:
- orchestration layer для сценариев автоматизации

Отвечает за:
- прием webhook от Telegram
- обработку команд и нажатий кнопок бота
- выдачу ссылки OAuth Google и запись `calendar_connections`
- синхронизацию Google Calendar с Supabase
- формирование и отправку daily digest терапевту
- обновление статуса оплаты после действия клиента

Принцип:
- Make не хранит состояние системы и не является источником истины.

---

## Потоки интеграций

## Sync поток (Google Calendar -> Supabase)

1. Make запускает сценарий синхронизации.
2. Получает события из Google Calendar.
3. Обновляет `calendar_events` и `calendar_event_instances` в Supabase.

## Reminder поток (Supabase -> Telegram)

1. Supabase-триггер при INSERT/UPDATE `calendar_event_instances` создаёт/обновляет `notification_jobs` для нужных встреч.
2. В момент `scheduled_for` сообщение отправляет Supabase-воркер/Edge Function, после чего обновляется статус задачи (`sent`/`failed`).

## Payment confirmation поток (Telegram -> Supabase)

1. Клиент нажимает кнопку в Telegram.
2. Telegram отправляет webhook в Make.
3. Make обновляет `calendar_event_instances.is_paid` и `payment_confirmed_at`.

## Daily digest поток (Supabase -> Telegram)

1. По расписанию Make собирает встречи и их статусы.
2. Формирует digest.
3. Отправляет digest в личный чат терапевта.
