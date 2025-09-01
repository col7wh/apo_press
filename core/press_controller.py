# core/press_controller.py
import threading
import logging
import time
import json
import os
import sys
from typing import Dict, Any, List
from core.step_executor import StepExecutor
from core.safety_monitor import SafetyMonitor
from core.global_state import state


class PressController(threading.Thread):
    def __init__(self, press_id: int, config: dict):
        super().__init__(name=f"PressCtrl-{press_id}", daemon=True)
        self.press_id = press_id
        self.running = False
        self.completed = False
        self.paused = False
        self.config = config  # ✅ Сохраняем
        self.current_step_index = 0
        self.executor: StepExecutor = None

        # Используем ОБЩИЙ SafetyMonitor из ControlManager
        self.safety = state.safety_monitors.get(press_id)
        if not self.safety:
            logging.warning(f"РС Пресс-{press_id}: общий SafetyMonitor не найден, создаём новый")
            self.safety = SafetyMonitor(press_id)
            state.safety_monitors[press_id] = self.safety

    def run(self):
        """Основной цикл выполнения программы"""
        # Загружаем обе программы
        try:
            with open(f"programs/press{self.press_id}.json", "r", encoding="utf-8") as f:
                program = json.load(f)
            temp_prog = program.get("temp_program", [])
            press_prog = program.get("pressure_program", [])
        except Exception as e:
            logging.error(f"РС Пресс-{self.press_id}: ошибка загрузки программы: {e}")
            return

        if not temp_prog and not press_prog:
            logging.error(f"РС Пресс-{self.press_id}: обе программы пусты")
            return

        state.set(f"press_{self.press_id}_running", True)
        state.set(f"press_{self.press_id}_paused", False)
        state.set(f"press_{self.press_id}_completed", False)
        self.running = True
        self.completed = False
        logging.info("=====================================================================================")
        logging.info(f"РС Пресс-{self.press_id}: запуск программы (temp: {len(temp_prog)}, pressure: {len(press_prog)})")
        # logging.info(f"РС Пресс-{self.press_id}: выполнение ({program})")

        # Создаём и запускаем StepExecutor
        self.executor = StepExecutor(self.press_id)
        self.executor.load_programs(temp_prog, press_prog)
        self.executor.start()

        logging.info(f"РС Пресс-{self.press_id}: StepExecutor запущен")

        logging.info("=====================================================================================")
        # Основной цикл: следим за безопасностью и состоянием
        while self.running and self.safety.is_safe():
            # Ничего не делаем — StepExecutor работает сам
            time.sleep(0.1)

        # Если вышли из цикла — останавливаем executor
        if self.executor and self.executor.is_alive():
            logging.info(f"РС Пресс-{self.press_id}: остановка StepExecutor")
            self.executor.stop()
            self.executor.join(timeout=1.0)

        state.set(f"press_{self.press_id}_running", False)
        state.set(f"press_{self.press_id}_paused", False)
        state.set(f"press_{self.press_id}_completed", True)
        self.running = False
        self.completed = True
        logging.info(f"РС Пресс-{self.press_id}: программа завершена.")

    def stop(self):
        if not self.running:
            return

        logging.info(f"РС Пресс-{self.press_id}: остановка по запросу")
        self.running = False
        state.set(f"press_{self.press_id}_running", False)
        state.set(f"press_{self.press_id}_paused", False)
        state.set(f"press_{self.press_id}_completed", True)
        # 1. Остановить StepExecutor
        if self.executor and self.executor.is_alive():
            self.executor.stop()
            self.executor.join(timeout=1.0)

        # 2. Сбросить уставку
        state.set(f"press_{self.press_id}_target_temp", None)
        state.set(f"press_{self.press_id}_target_pressure", 0.0)

        # 3. Выключить всё на DO
        do_module = self.config["presses"][self.press_id - 1]["modules"]["do"]
        urgent = state.get("urgent_do", {})
        urgent[do_module] = (0, 0)
        state.set("urgent_do", urgent)

        # state.write_do(do_module, 0, 0)

        # 4. Обновить статус
        state.set(f"press_{self.press_id}_step_status_temperature", "stopped")
        state.set(f"press_{self.press_id}_step_status_pressure", "stopped")

        logging.info(f"РС Пресс-{self.press_id}: остановлен. Уставка сброшена.")

    def emergency_stop(self):
        """Аварийная остановка"""
        logging.warning(f"РС Пресс-{self.press_id}: аварийная остановка!")
        self.running = False
        if self.executor and self.executor.is_alive():
            self.executor.stop()
        self.safety.emergency = True

    def pause(self):
        """Пауза"""
        state.set(f"press_{self.press_id}_paused", True)
        self.paused = True
        state.set(f"press_{self.press_id}_step_status", "paused")
        logging.info(f"РС Пресс-{self.press_id}: поставлен на паузу.")


# -----------------------------
# Режим отладки: __main__
# -----------------------------

if __name__ == "__main__":
    def clear_screen():
        sys.stdout.write("\033[H")
        sys.stdout.write("\033[J")
        sys.stdout.flush()


    def clear_line(count=1):
        for _ in range(count):
            sys.stdout.write("\033[K")
            sys.stdout.write("\033[A")
        sys.stdout.write("\033[B" * count)
        sys.stdout.flush()


    print("🔧 Тестирование PressController — многозонный нагрев")

    print("Выберите режим:")
    print("1 — Мок-режим (имитация)")
    print("2 — Реальный режим (железо)")
    choice = input("> ").strip()

    if choice == "1":
        from core.mock_hardware import MockHardwareInterface

        hw = MockHardwareInterface()
        print("✅ Мок-режим активирован")
    elif choice == "2":
        from core.hardware_interface import HardwareInterface
        import os

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(project_root, "config", "system.json")

        if not os.path.exists(config_path):
            print(f"❌ Файл конфигурации не найден: {config_path}")
            exit(1)

        try:
            hw = HardwareInterface(config_path)
            print(f"✅ Подключено к реальному оборудованию")
        except Exception as e:
            print(f"❌ Ошибка инициализации интерфейса: {e}")
            exit(1)

    try:
        press_id = int(input("Введите ID пресса (1,2,3): "))
        if press_id not in (1, 2, 3):
            raise ValueError
    except:
        print("❌ Неверный ID")
        exit(1)

    pc = PressController(press_id=press_id, hardware_interface=hw)
    print(f"\n🔧 Управление прессом {press_id} запущено")

    while True:
        clear_screen()
        print(f"🔧 УПРАВЛЕНИЕ ПРЕССОМ — Пресс {press_id}")
        print("1 — Запустить программу")
        print("2 — Приостановить")
        print("3 — Возобновить")
        print("4 — Остановить")
        print("5 — Показать статус")
        print("0 — Выход")
        print("-" * 50)

        cmd = input("Выберите действие: ").strip()

        if cmd == "1":
            if not pc.running:
                pc.start()
                print("✅ Программа запущена")
            else:
                print("⚠️ Уже запущено")
            input("Нажмите Enter...")

        elif cmd == "2":
            if pc.running and not pc.paused:
                pc.pause()
                print("⏸️ Приостановлено")
            else:
                print("⚠️ Нельзя приостановить")
            input("Нажмите Enter...")

        elif cmd == "3":
            if pc.paused:
                pc.resume()
                print("▶️ Возобновлено")
            else:
                print("⚠️ Нельзя возобновить")
            input("Нажмите Enter...")

        elif cmd == "4":
            if pc.running:
                pc.stop()
                print("⏹️ Остановлено")
            else:
                print("⚠️ Уже остановлено")
            input("Нажмите Enter...")

        elif cmd == "5":
            status = "РАБОТАЕТ" if pc.running else "ОСТАНОВЛЕН"
            if pc.paused:
                status = "ПАУЗА"
            if pc.completed:
                status = "ЗАВЕРШЁН"

            print(f"\n📊 Статус пресса {press_id}: {status}")
            if pc.current_step_index >= 0:
                print(f"   Текущий шаг: {pc.current_step_index + 1} / {len(pc.program)}")
            else:
                print("   Шаг: не начат")
            input("Нажмите Enter...")

        elif cmd == "0":
            print("Завершение...")
            if choice == "2":
                hw.close()
            break

        else:
            print("Неверный выбор")
            time.sleep(1)
