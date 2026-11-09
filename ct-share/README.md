# CT Share

Обмен КТ-снимками по локальной сети клиники.

## Скачать

| Приложение | Скачать |
|------------|---------|
| Админ (дисковод) | [CTShareAdmin.exe](https://github.com/irwkc/ident-bridge/raw/main/ct-share/release/CTShareAdmin.exe) |
| Врач (кабинеты) | [CTShareDoctor.exe](https://github.com/irwkc/ident-bridge/raw/main/ct-share/release/CTShareDoctor.exe) |

[Релизы](https://github.com/irwkc/ident-bridge/releases) · [Actions](https://github.com/irwkc/ident-bridge/actions/workflows/build-ct-share.yml)

## Установка

1. Создайте сетевую папку (шару), доступную всем ПК.
2. На ПК администратора: `CTShareAdmin.exe` → укажите UNC (`\\SERVER\KT`) → при желании **Автозапуск при входе**.
3. На ПК кабинетов: `CTShareDoctor.exe` → тот же путь.

При первом запуске EXE ставится в `C:\ct-share\`, создаёт ярлыки, пишет `config.json`.

Пример папки с диска: `Давтян_Анна_Самвеловна_1983_10_01_NNTViewer&DICOM`  
→ у врача: **Давтян Анна Самвеловна (1983-10-01)**

## Сборка

```bat
build_exe.cmd
```

Разработка без EXE:

```bat
set CT_SHARE_DIR=%CD%\local-data
python -m ct_share.admin_main
python -m ct_share.doctor_main
```
