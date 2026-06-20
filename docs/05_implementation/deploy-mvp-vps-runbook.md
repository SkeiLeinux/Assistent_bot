# Runbook: деплой MVP на VPS (снос старого + git pull)

**Дата:** 2026-06-11. **Исполняет:** С. (команды на сервере — по SSH).
**Что это:** операционное дополнение к [`deploy-e1.md`](deploy-e1.md) под выбранный путь —
**снести старый `lifeManager_bot`** и выкатить `Assistent_bot` через **`git pull` из origin**.
UAT / smoke / обкатку не дублирую — они в `deploy-e1.md` (§0, §5, §7).

**Условные обозначения:** `🔎` — значение известно только тебе (настройка VPS); подставь своё.

**Состояние на момент написания (проверено):**
- `master` синхронизирован с `origin/master`, HEAD — ожидаемо коммит деплоя MVP (на 2026-06-11 был `d1ba9dd деплой MVP`; поверх могли лечь пост-MVP фиксы / инкремент R, так что HEAD может быть новее).
- Код в HEAD целостен (141 `.py`, 0 синтаксических ошибок), точка входа `python -m app.main`.
- Незакоммичено только 2 файла тестов — их коммит в шаге 0.
- Origin: `https://github.com/SkeiLeinux/Assistent_bot.git`.

---

## Повторный деплой (обновление уже развёрнутого MVP)

> Разделы 0–10 ниже — **первичный** перенос (снос старого + клон). После того как
> `assistent-bot` уже поднят, каждое последующее обновление — это короткий цикл ниже.
> БД и `.env` при этом **не трогаем** (они уже на месте, данные сохраняются).

**1. Локально (Windows) — закоммитить и запушить.** VPS тянет из origin: незапушенное на сервер не попадёт.

```powershell
cd E:\пэт-проекты\Assistent_bot
.venv\Scripts\activate
python -m pytest -q ; lint-imports        # гейт: всё зелёное
git add -A
git commit -m "<суть правок>"
git push
```

**2. На VPS — обновить код и перезапустить сервис.**

```bash
cd /opt/Assistent_bot                      # 🔎 целевой каталог
git pull
sudo systemctl restart assistent-bot
journalctl -fu assistent-bot               # чистый старт: миграции → web → "Бот запущен!", без трейсбеков
```

Рестарт нужен из-за любых **Python-правок** (перезагрузка кода). Чисто-фронтовые
правки (`*.jsx`/`*.css` в `app/delivery/miniapp_api/static/`) aiohttp отдаёт с диска
сразу после `pull`, но один рестарт покрывает всё разом.

**3. Условные шаги — только если правка их содержит:**

| Что в правке | Доп. шаг (до рестарта) |
|---|---|
| Новая alembic-миграция | `source .venv/bin/activate && alembic upgrade head` |
| Изменился `requirements.txt` | `source .venv/bin/activate && pip install -r requirements.txt` |
| Только Python/статика, без миграций и новых пакетов | ничего сверх `pull + restart` |

> `main.py` на старте сам прогоняет `alembic upgrade head` — забытую миграцию он
> применит при рестарте; явный шаг лишь ловит ошибку миграции **до** перезапуска.

**4. Фронт-правки — один раз сбросить кэш мини-апы.** WebView Telegram кэширует
агрессивно. Статика отдаётся с `Cache-Control: no-cache` (ревалидация по ETag) —
но уже закэшенный старый `index.html` об этом ещё не знает. После первого деплоя с
этим заголовком закрой мини-апу полностью и переоткрой (или Telegram → Data and
Storage → Storage Usage → Clear Cache). Дальше обновления `*.jsx` подхватываются сами.

**5. Откат** — если новый код упал: `git -C /opt/Assistent_bot reset --hard <prev_sha>`
(или `git pull` обратно на тег) + `systemctl restart assistent-bot`. Миграции БД в
большинстве правок нет → откат кода достаточен; если миграция была — откатывать парой
(см. раздел «Откат» ниже).

---

## 0. Локально (Windows) — до деплоя

Деплой тянет код **из origin**, поэтому всё незапушенное на сервер не попадёт.

```powershell
cd E:\пэт-проекты\Assistent_bot
.venv\Scripts\activate

# 1) тесты + границы зелёные (гейт deploy-e1 §0)
python -m pytest -q
lint-imports

# 2) закоммитить починку тестов и запушить
git add tests/test_analytics_api.py tests/test_nutrition.py
git commit -m "test: починка тестов analytics_api + nutrition"
git push

# 3) (опционально) пометить релиз-кандидат
git tag e1-rc1
git push --tags
```

Проверка: `git status` → clean; `git log --oneline -1` у тебя и на GitHub совпадают.

---

## 1. VPS — остановить и забэкапить старый `lifeManager_bot`

```bash
# зафиксировать старую конфигурацию (понадобятся User/пути для нового юнита):
systemctl cat lifeManager_bot          # 🔎 запиши ExecStart, WorkingDirectory, User

sudo systemctl stop lifeManager_bot
sudo systemctl disable lifeManager_bot

# бэкап кода и БД — рефлекс для отката (deploy-e1 §6):
sudo cp -a /путь/к/старому/релизу /root/backup_lifeManager_$(date +%F)   # 🔎 путь старого релиза
```

Сам юнит и старый код удаляем **в шаге 10** — после того как новый поднялся и прошёл smoke (страховка отката).

---

## 2. Выкатить `Assistent_bot` из origin

**Репозиторий приватный** → клон по HTTPS с паролем не работает (GitHub убрал парольную
аутентификацию). Нужен SSH deploy key (рекомендуется) — настраивается один раз:

```bash
# ключ для деплоя (без пароля — для автоматического pull):
ssh-keygen -t ed25519 -C "assistent-bot-vps" -f ~/.ssh/assistent_deploy -N ""
cat ~/.ssh/assistent_deploy.pub     # скопировать весь вывод
```

GitHub → репозиторий `Assistent_bot` → **Settings → Deploy keys → Add deploy key** →
вставить ключ, «Allow write access» НЕ ставить (read-only достаточно). Затем:

```bash
# конфиг ssh для github (cat > — перезаписывает начисто, без дублей при повторных запусках).
# ВАЖНО: на этом VPS порт 22 к GitHub зарезан → SSH через 443 (ssh.github.com).
cat > ~/.ssh/config <<'EOF'
Host github.com
  HostName ssh.github.com
  Port 443
  User git
  IdentityFile ~/.ssh/assistent_deploy
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
ssh -T git@github.com                # первый раз спросит host key → yes; затем "Hi ...!" — норма

cd /opt                              # 🔎 целевой каталог (твой стандарт размещения)
git clone git@github.com:SkeiLeinux/Assistent_bot.git
cd Assistent_bot
git log --oneline -1                 # ожидаемо: коммит деплоя MVP (на 2026-06-11 был d1ba9dd; HEAD может быть новее — пост-MVP фиксы/инкремент R)
```

> Если порт 22 к GitHub открыт (другой сервер/провайдер) — можно оставить стандартное
> `HostName github.com` без `Port 443`.

Альтернатива — Fine-grained PAT (Contents: Read на один репо) и клон по HTTPS с токеном
вместо пароля. Дальше любой повторный деплой = `git pull` в этом каталоге.

---

## 3. venv (Python 3.10+) + зависимости

Разработка велась на CPython 3.12, но код совместим с **3.10+** (проверено: компиляция на
3.10.12 без ошибок; 3.11/3.12-only API — `StrEnum`, `typing.Self`, `datetime.UTC`,
`asyncio.TaskGroup`, PEP 695 `type` — в коде отсутствуют; зависимости поддерживают 3.10).
На текущем VPS стоит 3.10.12 — этого достаточно.

```bash
python3 --version                       # 3.10+ подходит
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

`import-linter`/`pytest` в requirements для прода безвредны; `tzdata` на Linux тоже (есть системные IANA-зоны).

---

## 4. Прод-`.env` (секреты переносишь ты — их нет в git)

```bash
cp /путь/к/старому/релизу/.env .env     # 🔎 перенести старый .env или создать новый
nano .env
```

Обязательные ключи (из `app/config.py`):

| Ключ | Назначение |
|---|---|
| `BOT_TOKEN` | токен Telegram-бота |
| `DATABASE_URL` | SQLite (async): `sqlite+aiosqlite:///./life_manager.db` — путь относительно корня репо (= `WorkingDirectory`). 🔎 сверь имя файла со старым |
| `ALLOWED_USER_IDS_RAW` | ваши два Telegram ID через запятую |
| `ADMIN_TELEGRAM_ID` | твой ID — туда идут алерты об ошибках |
| `GROQ_API_KEY` | ключ Groq (модуль «Еда», КБЖУ) |
| `WEBAPP_URL` | публичный HTTPS-URL мини-апы; **без него кнопка мини-апы в боте не появится** |

`groq_model`, `webapp_host`, `webapp_port` имеют дефолты в `config.py` — можно не задавать.

---

## 5. Свежая БД с нуля (greenfield)

Старую/тестовую БД **не переносим** (req §10: схема строится с нуля).

```bash
alembic upgrade head      # из корня репо, .venv активен; URL берётся из .env (env.py → settings.database_url)
alembic current           # должна быть ровно одна голова (схема E1)
```

`main.py` при старте сам прогоняет `alembic upgrade head`, но явный шаг ловит ошибки миграций до запуска сервиса.

---

## 6. systemd — новый юнит `assistent-bot.service`

`User` и пути возьми из `systemctl cat lifeManager_bot` (шаг 1).

```ini
[Unit]
Description=Assistent_bot — семейный ассистент (Telegram + Mini App)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ЮЗЕР                                                     # 🔎 как у старого сервиса
WorkingDirectory=/opt/Assistent_bot                          # 🔎 корень репо — ОБЯЗАТЕЛЬНО (CWD: .env и alembic читаются отсюда)
ExecStart=/opt/Assistent_bot/.venv/bin/python -m app.main    # 🔎 путь к python из .venv
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo nano /etc/systemd/system/assistent-bot.service   # вставить юнит выше
sudo systemctl daemon-reload
sudo systemctl enable --now assistent-bot
```

---

## 7. Проверка запуска

```bash
journalctl -fu assistent-bot
```

Ожидаемая последовательность в логе (порядок из `main.py`):
1. `Применяем миграции базы данных...` → `Миграции применены успешно`
2. web-сервер мини-апы на `127.0.0.1:8080`
3. `Бот запущен!`
4. тик scheduler (пустой) раз в 60 с

Трейсбеков быть не должно. `systemctl status assistent-bot` → `active (running)`.

---

## 8. Мини-апа: постоянный HTTPS через Cloudflare named tunnel

`webapp` слушает `127.0.0.1:8080`; наружу HTTPS даёт Cloudflare-туннель. На этом VPS порт 443
занят `xray` (VPN), а Telegram WebApp требует HTTPS строго на 443 → nginx+TLS закрыт. Cloudflare
named tunnel — **исходящее** соединение, не требует открытых портов и не конфликтует с xray.
Нужен домен в Cloudflare (free-план); NS домена делегируются на Cloudflare, зона = Active.

Рабочая конфигурация (домен `lifemanageronline.ru`, поддомен `bot.lifemanageronline.ru`):

```bash
cloudflared tunnel login                                    # выбрать домен в браузере → cert.pem
cloudflared tunnel create assistent                         # запомнить UUID из вывода
cloudflared tunnel route dns assistent bot.lifemanageronline.ru   # CNAME создастся сам
```

`~/.cloudflared/config.yml`:

```yaml
tunnel: <UUID>
credentials-file: /root/.cloudflared/<UUID>.json
protocol: http2          # ОБЯЗАТЕЛЬНО на этом VPS (см. грабли ниже)
ingress:
  - hostname: bot.lifemanageronline.ru
    service: http://localhost:8080
  - service: http_status:404
```

```bash
cloudflared tunnel run assistent     # тест: 4× "Registered tunnel connection", без ошибок → Ctrl+C
cloudflared service install          # как systemd-сервис (автозапуск)
systemctl enable --now cloudflared
# если в логах "config file not found":
#   mkdir -p /etc/cloudflared && cp /root/.cloudflared/config.yml /etc/cloudflared/ && systemctl restart cloudflared
```

**Грабли (стоили времени — не повторять):**

- **QUIC (UDP) не проходит** через VPN-стек/провайдера: соединения рвутся
  (`failed to accept QUIC stream`, `context deadline exceeded`). Лечится `protocol: http2`
  в `config.yml` — TCP вместо UDP. На этом VPS http2 обязателен.
- **Дубли `cloudflared`-процессов** (прошлый запуск не умер) → `unknown error registering the
  connection`, нестабильные коннекты. Перед запуском: `pkill -f cloudflared`, проверить `ps`.

**Привязка к боту и Telegram-вход:**

```bash
nano /opt/Assistent_bot/.env      # WEBAPP_URL=https://bot.lifemanageronline.ru  (ОДНА схема, без хвостового /)
systemctl restart assistent-bot
```

BotFather → menu-button (или `setChatMenuButton`) с URL = `WEBAPP_URL`. `/start` → кнопка
«🏠 Открыть ассистент» открывает мини-апу у обоих.

> Частая ошибка в `.env`: двойная схема `https:https://...` → Telegram «Wrong port number
> specified in the URL». Должна быть ровно одна `https://`.

---

## 9. Smoke E2E на проде

Прогнать по чеклисту **`deploy-e1.md` §5** (это SSOT приёмки): реальный пейринг → **скрытый подарок не виден партнёру** (главный инвариант) → импорт реального `Бюджет общий 2026.xlsx` → фото-еда → напоминание с кнопками → вечерний дайджест → уведомление партнёру о добавлении.

---

## 10. Финализация сноса старого (после успешного smoke)

```bash
sudo rm /etc/systemd/system/lifeManager_bot.service
sudo systemctl daemon-reload
# старый код/venv — удалить или оставить бэкапом:
# sudo rm -rf /путь/к/старому/релизу        # 🔎 только когда уверен; бэкап из шага 1 остаётся
```

---

## Откат (deploy-e1 §6)

Старый код + БД лежат в бэкапе (шаг 1). Откат = вернуть старый юнит, код и старую БД, `systemctl restart`. **Старая версия с новой БД несовместима** — откатывать только парой (код + его БД).

---

## Сводка `🔎` — что подставить под свой VPS

1. Путь старого релиза (для бэкапа и финального удаления).
2. Целевой каталог нового кода (в примере `/opt/Assistent_bot`).
3. Наличие `python3.12` (иначе поставить).
4. Имя файла БД в `DATABASE_URL` (сверить со старым).
5. `User` и точные пути в systemd-юните (из `systemctl cat lifeManager_bot`).
6. HTTPS-слой мини-апы (cloudflared/nginx) и `WEBAPP_URL`; настройка menu-button.
