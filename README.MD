🗂️ Общая структура проекта

│
├── main.py                 # Основной контроллер (CLI)
├── diagnose.py             # Диагностика оборудования
├── simulator2.py            # Симулятор DCON-устройств
├── programs/               # Программы для прессов (JSON)
│   ├── press1.json
│   ├── press2.json
│   └── press3.json
├── config/                 # Конфигурационные файлы
│   ├── system.json         # Режим, COM-порт, тайминги
│   ├── hardware_config.json # Назначение модулей по прессам
│   └── config.json         # Доп. настройки, dcon_devices
├── core/                   # Ядро системы
│   ├── program_manager.py  # Загрузка и кэширование программ
│   ├── press_controller.py # Управление прессом (в потоке)
│   ├── step_executor.py    # Выполнение шагов программы
│   ├── temp_control.py     # Управление нагревом (on/off)
│   ├── safety_monitor.py   # Контроль безопасности
│   ├── hardware_interface.py # Работа с DCON (реальный/симуляция)
│   ├── global_state.py     # Единое состояние системы (в памяти)
│   └── hardware_daemon.py  # Демон: читает/пишет железо, обновляет буфер
├── state.json              # Сохранение состояния (аналог trouble.bin)
├── data/                   # Архивы процессов (CSV)
│   ├── data0.csv
│   └── ...
├── templates/ 
│   ├── dashboard.html
│   └── edit_program.html
├── app.log                 # Логи системы
├── build.bat               # Сборка в .exe (pyinstaller)
└── requirements.txt        # Зависимости: pyserial и др.
