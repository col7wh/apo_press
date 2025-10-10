# main.py
import sys
import json
import time
import logging
import threading
import os
import atexit
import argparse  # <-- Добавь в начало файла
from typing import Dict, Any

from core.graph_transmitter import GraphTransmitter
from core.hardware_interface import HardwareInterface
from core.hardware_daemon import HardwareDaemon
from core.web_interface import WebInterface
from core.control_manager import ControlManager
from core.global_state import state
from logging.handlers import TimedRotatingFileHandler

# Глобальные переменные
hardware_interface: HardwareInterface = None
# press_controllers: Dict[int, PressController] = {}
running = True
daemon: HardwareDaemon = None  # будет инициализирован в main()
control_managers = {}


def setup_main_logger():
    os.makedirs("logs", exist_ok=True)
    log_file = "logs/app.log"

    handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",  # Ротация в полночь
        interval=1,  # Каждый день
        backupCount=7,  # Хранить 7 дней
        encoding="utf-8"
    )
    handler.suffix = "%Y-%m-%d"  # app.log.2025-09-01
    # handler.extMatch = r"\d{4}-\d{2}-\d{2}"  # Как распознавать старые

    formatter = logging.Formatter('%(asctime)s [MAIN] %(levelname)s: %(message)s')
    handler.setFormatter(formatter)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    # Убедимся, что нет дублирующих хендлеров
    if not logging.getLogger().hasHandlers():
        logging.getLogger().addHandler(handler)
    logging.info("][ " * 35)
    logging.info("M Логирование инициализировано")


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
    cm = control_managers.get(press_id)
    if cm:
        cm.on_start_pressed()
    else:
        logging.warning(f"M ControlManager для пресса {press_id + 1} не найден")


def stop_press(press_id: int, emergency: bool = False):
    cm = control_managers.get(press_id)
    if not cm:
        logging.info(f"M Пресс-{press_id + 1} не запущен.")
        return

    if emergency:
        cm.emergency_stop()
        if cm.press_controller and cm.press_controller.running:
            cm.press_controller.emergency_stop()
    else:
        cm.stop_cycle()
        logging.info(f"M Пресс-{press_id + 1}: останов по запросу GUI.")


def show_status():
    print("\n" + "=" * 50)
    for pid in range(1, 4):
        # Читаем из state — единая точка истины
        paused = state.get(f"press_{pid}_paused", False)
        completed = state.get(f"press_{pid}_completed", False)

        temp_step = state.get(f"press_{pid}_current_step_temperature", {})
        press_step = state.get(f"press_{pid}_current_step_pressure", {})

        index_temp = temp_step.get("index", -1)
        index_press = press_step.get("index", -1)
        current_step = max(index_temp, index_press) + 1 if max(index_temp, index_press) >= 0 else "-"

        if running:
            status = "ПАУЗА" if paused else "РАБОТАЕТ"
            print(f"Пресс-{pid + 1}: {status} | Шаг {current_step}")
        else:
            if completed:
                print(f"Пресс-{pid + 1}: ЗАВЕРШЁН")
            else:
                print(f"Пресс-{pid + 1}: ОСТАНОВЛЕН")
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
                print(f"  Пресс {pid + 1}: {total} шагов (T:{temp_steps}, P:{press_steps})")
            except Exception as e:
                print(f"  Пресс {pid + 1}: ❌ ошибка загрузки ({e})")
        else:
            print(f"  Пресс {pid + 1}: ❌ файл не найден")


def command_loop():
    time.sleep(0.19)
    global running
    while running:
        print("\n" + "=" * 50)
        print("🔧 УПРАВЛЕНИЕ ПРЕССАМИ")
        print("=" * 50)
        print("1 — Запустить пресс 2")
        print("2 — Запустить пресс 3")
        print("3 — Запустить пресс ")
        print("4 — Остановить пресс 2")
        print("5 — Остановить пресс 3")
        print("6 — Остановить пресс 4")
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

                # 1. Остановить все PressController через ControlManager
                for pid in [1, 2, 3]:
                    cm = control_managers.get(pid)
                    if cm and cm.press_controller and cm.press_controller.running:
                        cm.press_controller.emergency_stop()
                        cm.press_controller.join(timeout=0.5)
                        logging.info(f"M Пресс-{pid + 1}: emergency_stop вызван через ControlManager")

                for mod in ["31", "32", "34", "35", "36"]:
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
            elif cmd == "11":
                if state.get(f"press_drawing", False):
                    state.set(f"press_drawing", False)
                    print("Рисование выключено")
                else:
                    state.set(f"press_drawing", True)
                    print("Рисование включено")
            elif cmd == "33":
                print("ВСЁ состояние системы:")
                print_structured_state()
            elif cmd == "34":
                print("ВСЁ состояние системы:")
                print(state.get_all())
            elif cmd == "35":
                print("ВСЁ состояние системы:")
                print_structured_state_full()
            elif cmd == "44":
                print("PID:")
                for pid in [1, 2, 3]:
                    c = []
                    for zone in range(8):
                        c.append(f"|zone {zone}:")
                        c.append(state.get(f"press_{pid}_temp{zone}_pid", "NaN"))
                    c.append(f"|pressure ")
                    c.append(state.get(f"press_{pid}_valve_pid", "NaN"))
                    print(f"Press {pid} {c}")
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

    # Остановка ControlManager
    for cm in control_managers.values():
        cm.stop()
        cm.join(timeout=1.0)

    # Остановка демона
    if daemon is not None:
        daemon.stop()
        daemon.join()

    # Финальная синхронизация: выключить всё
    if hardware_interface:
        do_modules = ["31", "32", "33", "34"]
        for mod in do_modules:
            hardware_interface._send_command(f"#{mod}0000")
            time.sleep(0.05)
            hardware_interface._send_command(f"#{mod}0B00")
            logging.info(f"M Финальное выключение DO-{mod}")

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
        print(f"  Пресс-{pid + 1}: {temps[:7]} | Уставка: {target}°C | Статус: {status_temp}")

    # --- ДАВЛЕНИЕ ---
    print("\n⚙️  ДАВЛЕНИЕ")
    for pid in [1, 2, 3]:
        pressure = state.get(f"press_{pid}_pressure", "N/A")
        target = state.get(f"press_{pid}_target_pressure", "N/A")
        status_press = state.get(f"press_{pid}_step_status_pressure", "stopped")
        print(f"  Пресс-{pid + 1}: {pressure} МПа → {target} МПа | Статус: {status_press}")

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
            print(f"  Пресс-{pid + 1}:")
            if temp_step:
                print(
                    f"    Темп:  {temp_step.get('index', '-')} | {temp_step.get('type', '-')} | Цель: {temp_step.get('target_temp', 'N/A')}°C")
            if press_step:
                print(
                    f"    Давл:  {press_step.get('index', '-')} | {press_step.get('type', '-')} | Цель: {press_step.get('target_pressure', 'N/A')} МПа")

    print("=" * 60)


def print_structured_state_full():
    print("\n" + "=" * 70)
    print("📊 СОСТОЯНИЕ СИСТЕМЫ")
    print("=" * 70)

    data = state.get_all()

    # --- ПРЕССЫ ---
    for pid in [1, 2, 3]:
        if not any(k.startswith(f"press_{pid}_") for k in data):
            continue

        print(f"\n🔧 ПРЕСС-{pid + 1}")

        # Статус
        paused = data.get(f"press_{pid}_paused", False)
        completed = data.get(f"press_{pid}_completed", False)

        if running:
            status = "⏸️ ПАУЗА" if paused else "▶️ РАБОТАЕТ"
        elif completed:
            status = "✅ ЗАВЕРШЁН"
        else:
            status = "⏹️ ОСТАНОВЛЕН"

        print(f"  Статус: {status}")

        # Температура
        temps = data.get(f"press_{pid}_temps", [None] * 8)[:7]
        target_temp = data.get(f"press_{pid}_target_temp", "N/A")
        step_temp = data.get(f"press_{pid}_current_step_temperature", {})
        step_temp_type = step_temp.get("type", "—")
        step_temp_index = step_temp.get("index", "-")
        step_time_temp = data.get(f"press_{pid}_step_elapsed_temperature", 0.0)

        print(f"  Темп:     {format_temps(temps)}")
        print(f"  Уставка:  {target_temp}°C | Шаг {step_temp_index}: {step_temp_type} ({format_time(step_time_temp)})")

        # Давление
        pressure = data.get(f"press_{pid}_pressure", "N/A")
        target_pressure = data.get(f"press_{pid}_target_pressure", "N/A")
        step_press = data.get(f"press_{pid}_current_step_pressure", {})
        step_press_type = step_press.get("type", "—")
        step_press_index = step_press.get("index", "-")
        step_time_press = data.get(f"press_{pid}_step_elapsed_pressure", 0.0)

        print(f"  Давление: {pressure} МПа → {target_pressure} МПа")
        print(f"            Шаг {step_press_index}: {step_press_type} ({format_time(step_time_press)})")

        # Цикл
        cycle_elapsed = data.get(f"press_{pid}_cycle_elapsed", 0.0)
        print(f"  Время цикла: {format_time(cycle_elapsed)}")

    # --- ВХОДЫ (DI) ---
    print(f"\n🔌 ВХОДЫ (DI)")
    for mod in ["37", "38", "39"]:
        val = data.get(f"di_module_{mod}", 0)
        print(f"  DI-{mod}: {val:04X} ({bin(val)[2:].zfill(16)})")

    # --- ВЫХОДЫ (DO) ---
    print(f"\n⚙️  ВЫХОДЫ (DO)")
    for mod in ["31", "32", "33", "34"]:
        val = data.get(f"do_state_{mod}", 0)
        print(f"  DO-{mod}: {val:04X} ({bin(val)[2:].zfill(16)})")

    # --- ОЧЕРЕДИ ---
    urgent_do = data.get("urgent_do", {})
    heating_do = data.get("heating_do", {})
    print(f"\n📤 ОЧЕРЕДИ ЗАПИСИ")
    if urgent_do:
        for mod, (lo, hi) in urgent_do.items():
            print(f"  СРОЧНО: DO-{mod} → {lo:02X} {hi:02X}")
    else:
        print("  Срочные команды: пусто")

    if heating_do:
        for mod, (lo, hi) in heating_do.items():
            print(f"  НАГРЕВ:  DO-{mod} → {lo:02X} {hi:02X}")
    else:
        print("  Команды нагрева: пусто")

    # --- DCON СТАТИСТИКА ---
    dcon = data.get("dcon_stats", {})
    if dcon:
        print(f"\n📡 DCON СТАТИСТИКА (за {dcon.get('period', 0):.0f} с)")
        print(f"  Качество: {dcon.get('quality', 0):.1f}% | Скорость: {dcon.get('speed', 0):.1f} ком/с")
        print(f"  Всего: {dcon.get('total', 0)}, Good: {dcon.get('good', 0)}, Bad: {dcon.get('bad', 0)}")
        by_mod = ", ".join([f"{k}:{v}" for k, v in dcon.get("by_module", {}).items()])
        print(f"  По модулям: {by_mod}")

    print("=" * 70)


# Вспомогательные функции
def format_temps(temps):
    return " | ".join(f"{t:5.1f}" if t is not None else "  N/A " for t in temps)


def format_time(seconds):
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:2d}:{secs:02d}"


atexit.register(cleanup)


def emergency_stop_all():
    for pid in [1, 2, 3]:
        cm = control_managers.get(pid)
        if cm:
            cm.emergency_stop()


def main():
    global hardware_interface, daemon, hw_config, control_managers

    # Парсим аргументы
    parser = argparse.ArgumentParser(description="Управление прессами")
    parser.add_argument("--gui", action="store_true", help="Запустить с GUI")
    parser.add_argument("--console", action="store_true", help="Принудительно запустить консольный режим")
    args = parser.parse_args()

    setup_main_logger()
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

    # Запуск ControlManager'ов
    for pid in [1, 2, 3]:
        cm = ControlManager(press_id=pid, config=hw_config)
        cm.start()
        control_managers[pid] = cm

    # Выбор режима
    if args.gui and not args.console:
        # Запуск GUI (в основном потоке)
        # Создаём локальные функции после инициализации control_managers
        def start_press_local(press_id):
            if press_id < 1 or press_id > 3:
                logging.warning("M Пресс должен быть 1, 2 или 3.")
                return
            cm = control_managers.get(press_id)
            if cm:
                cm.on_start_pressed()
            else:
                logging.warning(f"M ControlManager для пресса {press_id + 1} не найден")

        def stop_press_local(press_id):
            stop_press(press_id, emergency=False)

        def emergency_stop_local():
            for pid in [1, 2, 3]:
                stop_press(pid, emergency=True)

        try:
            # Запуск веб-интерфейса и графиков так же
            web_ui = WebInterface(host="0.0.0.0", port=5000)
            web_ui.start()
            logging.info("M Веб-интерфейс запущен (http://localhost:5000)")

            graph_tx = GraphTransmitter()
            graph_tx.start()

            # Запуск GUI
            from gui import SimpleGUI
            time.sleep(0.5)  # Даём системе время на инициализацию
            gui = SimpleGUI(start_press_local, stop_press_local, emergency_stop_local)
            gui.run()  # ← блокирует здесь, пока окно не закроют
        except ImportError as e:
            logging.error(f"GUI не может быть запущен: {e}")
            print("Ошибка: не удалось загрузить GUI. Убедитесь, что gui.py на месте.")
            return
    else:
        # Консольный режим — как раньше
        cmd_thread = threading.Thread(target=command_loop, daemon=True)
        cmd_thread.start()

        web_ui = WebInterface(host="0.0.0.0", port=5000)
        web_ui.start()
        logging.info("M Веб-интерфейс запущен (http://localhost:5000)")

        graph_tx = GraphTransmitter()
        graph_tx.start()

        try:
            while running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            logging.info("M Получен сигнал завершения (Ctrl+C).")


# def main():
#     global hardware_interface, daemon, hw_config, control_managers  # ✅ Добавь hw_config
#     setup_main_logger()
#
#     config = load_system_config()
#     logging.info(f"M Система запущена в режиме: {config['mode']}")
#
#     hardware_interface = initialize_hardware()
#
#     config_path = os.path.join("config", "hardware_config.json")
#     with open(config_path, "r", encoding="utf-8") as f:
#         hw_config = json.load(f)
#
#     daemon = HardwareDaemon(hardware_interface)
#     daemon.start()
#     logging.info("M HardwareDaemon запущен")
#     time.sleep(0.1)
#
#     for pid in [1, 2, 3]:
#         cm = ControlManager(press_id=pid, config=hw_config)
#         cm.start()
#         control_managers[pid] = cm
#
#     cmd_thread = threading.Thread(target=command_loop, daemon=True)
#     cmd_thread.start()
#
#     # Запуск веб-интерфейса
#     web_ui = WebInterface(host="0.0.0.0", port=5000)
#     web_ui.start()
#     logging.info("M Веб-интерфейс запущен (http://localhost:5000)")
#
#     # Запуск передатчика на график
#     graph_tx = GraphTransmitter()
#     graph_tx.start()
#
#     try:
#         while running:
#             time.sleep(0.1)
#     except KeyboardInterrupt:
#         logging.info("M Получен сигнал завершения (Ctrl+C).")
#     #finally:
#         #cleanup()


if __name__ == "__main__":
    main()
