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
- Система не создает и не изменяет события календаря.

Основные данные:
- `google_event_id` в `calendar_events`
- `google_instance_event_id` в `calendar_event_instances`
- `sync_token` в `calendar_connections` для incremental sync

---

## 2. Telegram Bot API

Назначение:
- отправка reminder-уведомлений клиентам
- подтверждение оплаты через кнопку
- отправка daily digest терапевту

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
- синхронизацию Google Calendar с Supabase
- создание `notification_jobs`
- отправку Telegram-сообщений
- формирование daily digest
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

1. Make создает `notification_jobs` для нужных встреч.
2. В момент `scheduled_for` отправляет сообщение в Telegram.
3. Обновляет статус задачи (`sent`/`failed`).

## Payment confirmation поток (Telegram -> Supabase)

1. Клиент нажимает кнопку в Telegram.
2. Telegram отправляет webhook в Make.
3. Make обновляет `calendar_event_instances.is_paid` и `payment_confirmed_at`.

## Daily digest поток (Supabase -> Telegram)

1. По расписанию Make собирает встречи и их статусы.
2. Формирует digest.
3. Отправляет digest в личный чат терапевта.
