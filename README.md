# IdentBridge

Windows-приложение для моста **IDENT ↔ Yeastar S20**.

Один файл `IdentBridge.exe`:

- ставит рабочую папку `C:\ident-bridge`
- поднимает API (uvicorn) на порту **8000**
- keep-alive: при падении перезапускает сам
- автозапуск при старте Windows (Планировщик, UAC)
- небольшое окно: статус / Start / Stop / Restart / лог / `.env`

## Скачать EXE

1. Открой вкладку **Actions** → workflow **Build Windows EXE** → последний успешный run → **Artifacts** → `IdentBridge-windows`
2. Или **Releases** — там же лежит `IdentBridge.exe`

## Установка на ПК клиники

1. Скачай `IdentBridge.exe`
2. Запусти (Windows спросит права при установке автозапуска)
3. Нажми **Install autostart** → **Да** в UAC
4. Открой **Edit .env** и заполни ключи (как в старом `.env`)
5. **Restart**

Готово: bridge работает в фоне, окно можно закрыть.

## Локальная разработка

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m manager.main
```

Headless-сервис:

```bash
python -m manager.main --service
```

## Сборка EXE локально (Windows)

```bash
pip install -r requirements-build.txt
pyinstaller --noconfirm IdentBridge.spec
# → dist\IdentBridge.exe
```
