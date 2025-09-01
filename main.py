# main.py
import sys
import json
import time
import logging
import threading
import os
import atexit
from typing import Dict, Any
from core.hardware_interface import HardwareInterface
from core.press_controller import PressController
from core.hardware_daemon import HardwareDaemon
from core.web_interface import WebInterface
from core.control_manager import ControlManager
from core.global_state import state

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [MAIN] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler("app.log", encoding="utf-8"),
        # logging.StreamHandler()
    ]
)

# Глобальные переменные
hardware_interface: HardwareInterface = None
press_controllers: Dict[int, PressController] = {}
running = True
daemon: HardwareDaemon = None  # будет инициализирован в main()
control_managers = {}


def load_system_config() -> Dict[str, Any]:
    try:
        with open("config/system.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logging.critical("M Файл config/system.json не найден.")
        exit(1)
    except Exception as e:
        logging.critical(f"M Ошибка чтения config/system.json: {e}")
        exit(1)


def initialize_hardware() -> HardwareInterface:
    global hardware_interface
    try:
        hardware_interface = HardwareInterface("config/system.json")
        logging.info("M Интерфейс с оборудованием инициализирован.")
        return hardware_interface
    except Exception as e:
        logging.critical(f"M Ошибка инициализации оборудования: {e}")
        exit(1)


def start_press(press_id: int):
    if press_id < 1 or press_id > 3:
        logging.warning("M Пресс должен быть 1, 2 или 3.")
        return

    # Всегда создаём новый экземпляр
    pc = PressController(press_id=press_id, config=hw_config)
    press_controllers[press_id] = pc
    pc.start()
    logging.info(f"M Пресс-{press_id} запущен.")


def stop_press(press_id: int, emergency: bool = False):
    if press_id not in press_controllers:
        logging.info(f"M Пресс-{press_id} не запущен.")
        return

    pc = press_controllers[press_id]
    if emergency:
        pc.emergency_stop()
        logging.warning(f"M Пресс-{press_id}: аварийная остановка!")
    else:
        pc.stop()
        logging.info(f"M Пресс-{press_id}: останов по запросу.")


def show_status():
    print("\n" + "=" * 50)
    for pid in range(1, 4):
        # Читаем из state — единая точка истины
        running = state.get(f"press_{pid}_running", False)
        paused = state.get(f"press_{pid}_paused", False)
        completed = state.get(f"press_{pid}_completed", False)

        temp_step = state.get(f"press_{pid}_current_step_temperature", {})
        press_step = state.get(f"press_{pid}_current_step_pressure", {})

        index_temp = temp_step.get("index", -1)
        index_press = press_step.get("index", -1)
        current_step = max(index_temp, index_press) + 1 if max(index_temp, index_press) >= 0 else "-"

        if running:
            status = "ПАУЗА" if paused else "РАБОТАЕТ"
            print(f"Пресс-{pid}: {status} | Шаг {current_step}")
        else:
            if completed:
                print(f"Пресс-{pid}: ЗАВЕРШЁН")
            else:
                print(f"Пресс-{pid}: ОСТАНОВЛЕН")
    print("=" * 50)


def show_programs():
    print("\n📋 Доступные программы:")
    for pid in range(1, 4):
        path = f"programs/press{pid}.json"
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    prog = json.load(f)
                # 🔢 Считаем шаги
                temp_steps = len(prog.get("temp_program", []))
                press_steps = len(prog.get("pressure_program", []))
                total = temp_steps + press_steps
                print(f"  Пресс {pid}: {total} шагов (T:{temp_steps}, P:{press_steps})")
            except Exception as e:
                print(f"  Пресс {pid}: ❌ ошибка загрузки ({e})")
        else:
            print(f"  Пресс {pid}: ❌ файл не найден")


def command_loop():
    time.sleep(0.19)
    global running
    while running:
        print("\n" + "=" * 50)
        print("🔧 УПРАВЛЕНИЕ ПРЕССАМИ")
        print("=" * 50)
        print("1 — Запустить пресс 1")
        print("2 — Запустить пресс 2")
        print("3 — Запустить пресс 3")
        print("4 — Остановить пресс 1")
        print("5 — Остановить пресс 2")
        print("6 — Остановить пресс 3")
        print("7 — Аварийная остановка всех")
        print("8 — Показать программы")
        print("9 — Показать статус")
        print("0 — Выход")
        print("-" * 50)

        try:
            cmd = input("Выберите действие: ").strip()

            if cmd == "1":
                start_press(1)
            elif cmd == "2":
                start_press(2)
            elif cmd == "3":
                start_press(3)
            elif cmd == "4":
                stop_press(1)
            elif cmd == "5":
                stop_press(2)
            elif cmd == "6":
                stop_press(3)
            elif cmd == "7":
                logging.warning("M Аварийная остановка всех прессов!")
                # 1. Остановить все контроллеры
                for pid in list(press_controllers.keys()):
                    pc = press_controllers[pid]
                    if pc.is_alive():
                        pc.emergency_stop()
                        pc.join(timeout=0.5)

                # 2. Принудительно выключить все DO-модули нагрева
                do_modules = ["34", "35", "36"]  # Пресс 1, 2, 3
                for mod in do_modules:
                    state.write_do(mod, 0, 0)
                    state.set(f"do_state_{mod}", 0)
                    logging.info(f"M Аварийно выключено: DO-{mod}")

                # 3. Сбросить уставки
                for pid in [1, 2, 3]:
                    state.set(f"press_{pid}_target_temp", None)
                    state.set(f"press_{pid}_target_pressure", 0.0)

                logging.warning("M Все прессы аварийно остановлены.")
            elif cmd == "8":
                show_programs()
            elif cmd == "9":
                show_status()
            elif cmd == "33":
                print("ВСЁ состояние системы:")
                print_structured_state()
            elif cmd == "34":
                print("ВСЁ состояние системы:")
                print(state.get_all())
            elif cmd == "d" or cmd == "10":
                print("\n🔧 Запуск диагностики оборудования...")
                try:
                    import subprocess
                    subprocess.run([sys.executable, "diagnose.py"], check=True)
                except Exception as e:
                    print(f"❌ Ошибка запуска diagnose.py: {e}")
                input("Нажмите Enter...")
            elif cmd == "0":
                running = False
            else:
                print("❌ Неверный выбор")
        except (EOFError, KeyboardInterrupt):
            running = False
            break


def cleanup():
    global running, daemon, hardware_interface, control_managers
    running = False
    logging.info("M Выполняется остановка системы...")

    # Остановка PressController
    for pc in press_controllers.values():
        if pc.running:
            pc.stop()
            pc.join(timeout=1.0)

    # Остановка ControlManager
    for cm in control_managers.values():
        cm.stop()
        cm.join(timeout=1.0)

    # Финальная синхронизация: выключить всё
    if hardware_interface:
        do_modules = ["31", "32", "34", "35", "36"]
        for mod in do_modules:
            logging.info(f"M Финальное выключение DO-{mod}")
            hardware_interface._send_command(f"#{mod}0000")
            time.sleep(0.05)
            hardware_interface._send_command(f"#{mod}0B00")

    # Остановка демона
    if daemon is not None:
        daemon.stop()
        daemon.join()

    # Закрытие интерфейса
    if hardware_interface is not None:
        hardware_interface.close()

    logging.info("M Система остановлена.")


def print_structured_state():
    print("\n" + "=" * 60)
    print("📊 СОСТОЯНИЕ СИСТЕМЫ")
    print("=" * 60)

    # --- ДИСКРЕТНЫЕ ВХОДЫ ---
    print("\n🔌 ДИСКРЕТНЫЕ ВХОДЫ")
    print(f"  DI 37 (кнопки):     {bin(state.get('di_module_37', 0))[2:].zfill(16)}")
    print(f"  DI 38 (концевики):  {bin(state.get('di_module_38', 0))[2:].zfill(16)}")

    # --- ТЕМПЕРАТУРА ---
    print("\n🌡️  ТЕМПЕРАТУРА")
    for pid in [1, 2, 3]:
        temps = state.get(f"press_{pid}_temps", [None] * 8)
        target = state.get(f"press_{pid}_target_temp", "N/A")
        status_temp = state.get(f"press_{pid}_step_status_temperature", "stopped")
        print(f"  Пресс-{pid}: {temps[:7]} | Уставка: {target}°C | Статус: {status_temp}")

    # --- ДАВЛЕНИЕ ---
    print("\n⚙️  ДАВЛЕНИЕ")
    for pid in [1, 2, 3]:
        pressure = state.get(f"press_{pid}_pressure", "N/A")
        target = state.get(f"press_{pid}_target_pressure", "N/A")
        status_press = state.get(f"press_{pid}_step_status_pressure", "stopped")
        print(f"  Пресс-{pid}: {pressure} МПа → {target} МПа | Статус: {status_press}")

    # --- ВЫХОДЫ (DO) ---
    print("\n🔌 ВЫХОДЫ (DO)")
    for mod in [31, 32, 33, 34]:
        val = state.get(f"do_state_{mod}", 0)
        print(f"  DO {mod}: {bin(val)[2:].zfill(16)} ({val})")

    # --- ТЕКУЩИЕ ШАГИ ---
    print("\n🔄 ТЕКУЩИЕ ШАГИ")
    for pid in [1, 2, 3]:
        temp_step = state.get(f"press_{pid}_current_step_temperature", {})
        press_step = state.get(f"press_{pid}_current_step_pressure", {})
        if temp_step or press_step:
            print(f"  Пресс-{pid}:")
            if temp_step:
                print(
                    f"    Темп:  {temp_step.get('index', '-')} | {temp_step.get('type', '-')} | Цель: {temp_step.get('target_temp', 'N/A')}°C")
            if press_step:
                print(
                    f"    Давл:  {press_step.get('index', '-')} | {press_step.get('type', '-')} | Цель: {press_step.get('target_pressure', 'N/A')} МПа")

    print("=" * 60)


atexit.register(cleanup)


def main():
    global hardware_interface, daemon, hw_config, control_managers  # ✅ Добавь hw_config

    config = load_system_config()
    logging.info(f"M Система запущена в режиме: {config['mode']}")

    hardware_interface = initialize_hardware()

    config_path = os.path.join("config", "hardware_config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        hw_config = json.load(f)

    daemon = HardwareDaemon(hardware_interface)
    daemon.start()
    logging.info("M HardwareDaemon запущен")
    time.sleep(0.1)
    # show_programs()
    # После создания hw и daemon
    # control_managers = {}
    for pid in [1, 2, 3]:
        cm = ControlManager(press_id=pid, config=hw_config)
        cm.start()
        control_managers[pid] = cm
        # Создаём PressController, но НЕ запускаем
        press_controllers[pid] = PressController(press_id=pid, config=hw_config)

    cmd_thread = threading.Thread(target=command_loop, daemon=True)
    cmd_thread.start()

    # Запуск веб-интерфейса
    web_ui = WebInterface(host="0.0.0.0", port=5000)
    web_ui.start()
    logging.info("M Веб-интерфейс запущен (http://localhost:5000)")

    try:
        while running:
            time.sleep(0.1)
    except KeyboardInterrupt:
        logging.info("M Получен сигнал завершения (Ctrl+C).")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
