# MeowServerManager (MSM)

CLI для управления модпаком и сервером Minecraft (NeoForge / Forge / Fabric).
Поиск и установка модов с Modrinth и CurseForge, разрешение зависимостей,
синхронизация клиентской сборки, профили, манифесты и запуск/стоп сервера.

## Важное предупреждение

Этот инструмент написан ИИ примерно на 90%. Кода человека тут мало — в основном
ревью и правки. Поэтому:

- Ошибки и неочевидные кейсы вполне возможны.
- Поведение в edge-cases может быть не таким, как ожидается.
- CurAP-ключ CurseForge и пути к твоему серверу — твои, не мои.
- Делай бэкапы перед массовыми операциями (`scan`, `sync-clients`, `remove`).
- PR и issue приветствуются, но не обещай людям идеальную стабильность.

Если что-то сломалось — смотри лог, MSM пишет в `logging` и в консоль.

## Возможности

- Поиск модов на Modrinth (без ключа) и CurseForge (нужен API-ключ).
- Установка модов с автоматическим разрешением зависимостей.
- Авто-определение стороны (client/server/both) по метаданным провайдера.
- Манифест установленных модов с хэшами, версиями и метаданными.
- Синхронизация клиентской сборки из `mods/` в `client_mods/`.
- Профили для разных серверов/лоадеров/версий.
- Управление сервером: `start`, `stop`, `restart`, `status`.
- `scan` — построение/обновление манифеста по существующим JAR'ам.
- Ручные overrides стороны для модов с ошибочной классификацией.

## Установка

Требуется Python 3.11+.

```bash
git clone <repo-url> msm
cd msm
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -e .
```

После установки доступна команда `msm`.

## Настройка

Скопируй пример конфига и отредактируй:

```bash
cp config.example.toml config.toml
```

Пример минимального `config.toml`:

```toml
[active]
profile = "default"

[profiles.default]
mods_dir = "../mods"
client_mods_dir = "../client_mods"
loader = "neoforge"
game_version = "1.21.1"
run_script = "./run.sh"
server_dir = ".."

[apis]
curseforge_api_key = ""

[modrinth]
user_agent = "MeowServerManager/1.0.0"

[download]
concurrent_downloads = 5
auto_resolve_deps = true

[sync]
# false — копировать в клиент вообще всё (рекомендуется: лишний мод не ломает
# вход, а отсутствующий — ломает). true — пропускать server-only моды.
exclude_server_only = false
# Список slug/имён/файлов, которые никогда не копируются в клиент.
server_only = []

[side_overrides]
# Только для форсирования стороны. Например:
# lithostitched = "both"
```

CurseForge-ключ можно задать через переменную окружения `CF_API_KEY` вместо
`config.toml`.

## Примеры использования

```bash
# Поиск
msm search "create" --provider modrinth --loader neoforge -g 1.21.1

# Информация о моде
msm info "create"

# Установить мод (и его зависимости) в активный профиль
msm install "create" --provider modrinth

# Установить с форсированной стороной
msm install "modernfix" --side server

# Список установленных модов
msm list

# Собрать манифест по существующей папке mods/
msm scan
# Перепроверить ранее неизвестные моды (поиск по имени файла)
msm scan --refresh

# Синхронизировать клиентскую сборку (dry-run сначала)
msm sync-clients --dry-run
msm sync-clients

# Удалить мод по паттерну имени
msm remove "cave_dweller" --side server

# Профили
msm profile create survival --loader neoforge -g 1.21.1 -m ../mods -s ..
msm profile use survival
msm profile list
msm profile show

# Сервер
msm server start
msm server status
msm server stop
msm server restart

# Overrides стороны
msm side set lithostitched both
msm side list
msm side unset lithostitched
```

## Структура манифеста

Манифест хранится в `{profile}_manifest.toml` рядом с `config.toml`. Для каждого
мода: slug, project_id, провайдер, сторона, версии, хэши SHA1/SHA512, размер,
категории и зависимости. Используется для `sync-clients`, `remove` и аудита.

## Политика синхронизации клиента

По умолчанию `sync-clients` копирует в `client_mods/` вообще всё, что есть в
`mods/`, кроме явно исключённого. Логика простая:

- Отсутствующий на клиенте content/dependency-мод ломает вход на сервер.
- Лишний мод на клиенте — безобиден.

Если хочешь более строгую фильтрацию, включи `exclude_server_only = true` и/или
заполни `server_only` в `[sync]`.

## Стоит помнить

- CurseForge Core API требует ключ. Без него работает только Modrinth.
- Авто-определение стороны берётся из метаданных Modrinth/CurseForge. Иногда
  они врут — для этого есть `msm side set`.
- `scan` по хэшу не находит файлы, которых нет на Modrinth (например, CurseForge-
  exclusive). Для таких случаев есть `scan --refresh` — поиск по имени файла.

## Лицензия

MIT. Делай что хочешь, но без гарантий.
