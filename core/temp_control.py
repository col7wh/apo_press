# core/temp_control.py
"""
Многозонный on/off контроллер температуры с гистерезисом.
Управляет 8 зонами нагрева независимо.
Работает через global_state (шина команд).
"""
import os
import json
import time
import logging
import sys
import threading
from typing import Optional, List, Dict
from core.global_state import state  # ✅ Используем шину
from core.pid_controller import PIDController

# Убедимся, что корень проекта в пути
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class TemperatureController(threading.Thread):
    def __init__(self, press_id):
        super().__init__(daemon=True)
        self.press_id = press_id
        self.running = True

        self.config_path = os.path.join("config", "hardware_config.json")
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)

        self.hysteresis = 2.0

        # Загружаем конфиг пресса
        press_cfg = self.config["presses"][press_id - 1]
        self.ai_module = press_cfg["modules"]["ai"]
        self.do_module = press_cfg["modules"]["do"]
        self.heater_channels = press_cfg["heater_channels"]  # [0,1,2,3,4,5,6,7] или [8,9,...]

        self.zones = len(self.heater_channels)
        self.targets = [None] * self.zones
        self.enabled = [True] * self.zones
        self.heating = [False] * self.zones

        logging.info(f"TC Пресс-{press_id}: TemperatureController инициализирован. Модуль {self.do_module}")
        self.pids = []
        self.offsets = []

        # Загрузка конфига
        with open("config/pid_config.json", "r") as f:
            pid_cfg = json.load(f)["presses"][press_id - 1]

        for zone_cfg in pid_cfg["zones"]:
            pid = PIDController(
                Kp=zone_cfg["Kp"],
                Ki=zone_cfg["Ki"],
                Kd=zone_cfg["Kd"],
                output_limits=(0, 100)  # % включения
            )
            self.pids.append(pid)
            self.offsets.append(zone_cfg["offset"])

    def set_target(self, zone: int, temp: float):
        """Установить уставку для зоны (0–7)"""
        if 0 <= zone < self.zones:
            self.targets[zone] = temp
            self.enabled[zone] = True
            logging.info(f"TC Пресс-{self.press_id}, зона {zone+1}: уставка = {temp}°C")
        else:
            logging.error(f"TC Пресс-{self.press_id}: некорректная зона: {zone}")

    def set_target_all(self, temp: float):
        """Установить уставку для всех зон"""
        for zone in range(self.zones):
            self.targets[zone] = temp
            self.enabled[zone] = True
        logging.info(f"TC Пресс-{self.press_id}: уставка {temp}°C установлена для всех зон")

    def disable_zone(self, zone: int):
        """Отключить зону (например, при обрыве термопары)"""
        if 0 <= zone < self.zones:
            self.enabled[zone] = False
            self.targets[zone] = None
            self._update_do_output()  # Обновляем DO
            logging.warning(f"TC Пресс-{self.press_id}, зона {zone+1}: отключена")
        else:
            logging.error(f"TC Пресс-{self.press_id}: некорректная зона: {zone}")

    def read_all_temperatures(self) -> List[Optional[float]]:
        """Чтение всех 8 температур из global_state (шины)"""
        try:
            temps = state.read_ai(self.press_id)  # ✅ Читаем через шину
            if not temps or len(temps) < 8:
                logging.warning(f"TC Пресс-{self.press_id}: недостаточно данных температур")
                return [None] * 8
            return temps
        except Exception as e:
            logging.error(f"TC Пресс-{self.press_id}: ошибка чтения температур: {e}")
            return [None] * 8

    def _update_do_output(self):
        """Обновляет DO, включая/выключая нужные каналы"""
        for zone in range(self.zones):

            ch = self.heater_channels[zone]  # Правильный бит на DO-модуле
            desired = self.heating[zone]
            print(f"TC heat {self.do_module}, {ch}, {desired}")
            state.write_do_bit(self.do_module, ch, desired)

    def _read_do_state(self) -> int:
        """Читает текущее состояние DO из global_state"""
        try:
            value = state.read_digital(self.do_module)
            return value if value is not None else 0
        except Exception as e:
            logging.error(f"TC Пресс-{self.press_id}: ошибка чтения DO: {e}")
            return 0

    def heat_to_(self,  zones: List[int] = None) -> bool:
        """
        Упрощённый режим: нагрев до температуры.
        Возвращает True, когда все зоны достигли цели (с гистерезисом).
        """
        target_temp = state.get(f"press_{self.press_id}_target_temp", None)
        logging.info(f"TC Пресс-{self.press_id}, нагрев до температуры {target_temp}:")
        if zones is None:
            zones = list(range(self.zones))  # Все зоны

        # Устанавливаем уставку
        for zone in zones:
            if self.enabled[zone]:
                self.targets[zone] = target_temp

        # Читаем температуры
        temps = self.read_all_temperatures()

        # Проверяем, достигнута ли цель
        all_reached = True
        for zone in zones:
            if not self.enabled[zone]:
                continue
            temp = temps[zone]
            if temp is None:
                all_reached = False
                continue
            # Достигли, если temp >= target - hysteresis
            if temp < target_temp - self.hysteresis:
                all_reached = False
        logging.info(f"TC Пресс-{self.press_id}, все зоны достигли {all_reached}:")
        return all_reached

    def heat_to(self, target_temp: float):
        """Запуск нагрева — как защёлка"""
        state.set(f"press_{self.press_id}_target_temp", target_temp)
        self.running = True
        if not self.is_alive():
            self.start()

    def cool_all(self):
        self.running = False
        state.set(f"press_{self.press_id}_target_temp", None)
        for ch in self.heater_channels:
            #state.write_do_bit(self.do_module, ch, False)
            state.set_do_command(self.do_module, 0, 0, urgent=False)

    def run(self):
        logging.info(f"TC Пресс-{self.press_id}: поток нагрева запущен")
        while self.running:
            self.update()
            time.sleep(0.1)

    def update(self):
        target_temp = state.get(f"press_{self.press_id}_target_temp")
        if target_temp is None:
            # 🔥 Выключаем ТОЛЬКО свои каналы
            current_state = state.read_digital(self.do_module) or 0
            new_state = current_state

            for ch in self.heater_channels:
                mask = 1 << ch
                new_state &= ~mask  # Сбрасываем бит

            if current_state != new_state:
                low = new_state & 0xFF
                high = (new_state >> 8) & 0xFF
                state.set_do_command(self.do_module, low, high, urgent=False)
                logging.info(f"TC Пресс-{self.press_id}: нагрев выключен (target_temp = None)")
            return

        temps = self.read_all_temperatures()
        # 🔥 Читаем ТЕКУЩЕЕ состояние DO-модуля
        current_state = state.read_digital(self.do_module) or 0
        new_state = current_state  # Начинаем с текущего

        # Определяем свои биты
        my_channels = self.heater_channels  # [0,1,2,3] для Пресса 1, [4,5,6,7] для Пресса 2

        for zone, ch in enumerate(my_channels):
            t = temps[zone]
            if t is None:
                continue

            # гистерезис
            # should_heat = t < target_temp - self.hysteresis

            # PID
            # Применяем оффсет
            temp_with_offset = temps[zone] + self.offsets[zone]
            self.pids[zone].set_setpoint(target_temp)

            # Вычисляем выход
            output = self.pids[zone].compute(temp_with_offset)
            should_heat = output > 10  # >10% → включаем

            mask = 1 << ch
            if should_heat:
                new_state |= mask
            else:
                new_state &= ~mask

        # 🔥 Только если изменилось — отправляем
        if current_state != new_state:
            low = new_state & 0xFF
            high = (new_state >> 8) & 0xFF
            state.set_do_command(self.do_module, low, high, urgent=False)
            # print(f"TC Пресс-{self.press_id}: DO-{self.do_module} → 0x{new_state:04X} "
            # f"(было: 0x{current_state:04X}) ишем в gs state.read_digital")

    def stop(self):
        logging.info(f"TC Пресс-{self.press_id}: остановлен")
        self.running = False

# -----------------------------
# Режим отладки: __main__
# -----------------------------

if __name__ == "__main__":
    import os

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

    print("🔧 Тестирование TemperatureController — многозонный нагрев")

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

    #tc = TemperatureController(press_id=press_id, hardware_interface=hw)
    # test
    tc = TemperatureController(press_id=press_id)
    print(f"\n🔧 Управление нагревом пресса {press_id} запущено")

    while True:
        clear_screen()
        print(f"🔧 УПРАВЛЕНИЕ НАГРЕВОМ — Пресс {press_id}")
        print("1 — Установить уставку для зоны")
        print("2 — Отключить зону")
        print("3 — Отключить весь нагрев")
        print("4 — Показать статус всех зон")
        print("5 — Режим реального времени (обновление на месте)")
        print("6 — Установить уставку для всех зон и начать нагрев")
        print("0 — Выход")
        print("-" * 50)

        cmd = input("Выберите действие: ").strip()

        if cmd == "1":
            try:
                zone = int(input("Зона (1–8): ")) - 1
                temp = float(input("Уставка (°C): "))
                tc.set_target(zone, temp)
                input("Нажмите Enter...")
            except:
                print("Ошибка ввода")
                time.sleep(1)

        elif cmd == "2":
            try:
                zone = int(input("Зона (1–8): ")) - 1
                tc.disable_zone(zone)
                input("Нажмите Enter...")
            except:
                print("Ошибка ввода")
                time.sleep(1)

        elif cmd == "3":
            tc.cool_all()
            input("Нажмите Enter...")

        elif cmd == "4":
            temps = tc.read_all_temperatures()
            status = tc.update()
            print("\n📊 Статус зон:")
            for z in range(8):
                t = temps[z] if temps[z] is not None else "N/A"
                s = status[z]
                print(f"  Зона {z+1}| {s}")
            input("\nНажмите Enter...")

        elif cmd == "5":
            print("\n📊 РЕАЛЬНОЕ ВРЕМЯ — Пресс {press_id} (Ctrl+C для выхода)")
            print("Зона | Темп | Уставка | Команда | Реально | Статус")
            print("-----|------|---------|---------|---------|---------")
            for _ in range(8):
                print("     |      |         |         |         |         ")
            try:
                while True:
                    clear_screen()
                    print(f"📊 РЕАЛЬНОЕ ВРЕМЯ — Пресс {press_id} (Ctrl+C для выхода)")
                    print("Зона | Темп | Уставка | Команда | Реально | Статус")
                    print("-----|------|---------|---------|---------|---------")

                    temps = tc.read_all_temperatures()
                    status = tc.update()

                    for z in range(8):
                        t = f"{temps[z]:>4.1f}" if temps[z] is not None else " N/A"
                        target = f"{status[z]['target']:>5.1f}" if status[z]['target'] is not None else "  N/A"
                        cmd = " ВКЛ " if status[z]['heating_cmd'] else " ВЫК "
                        real = " ВКЛ " if status[z]['heating_bit'] else " ВЫК "
                        stat = f"{status[z]['status']:^7}"

                        print(f" {z + 1}  | {t} |  {target} | {cmd} | {real} | {stat}")
                    time.sleep(1.0)

            except KeyboardInterrupt:
                print("\n\nВыход из режима реального времени")

        elif cmd == "6":
            try:
                temp = float(input("Введите уставку для всех зон (°C): "))
                tc.set_target_all(temp)
                print(f"✅ Уставка {temp}°C применена ко всем зонам")
                input("Нажмите Enter, чтобы продолжить...")
            except ValueError:
                print("❌ Ошибка: введите число")
                time.sleep(1)

        elif cmd == "0":
            print("Завершение...")
            if choice == "2":
                hw.close()
            break
        else:
            print("Неверный выбор")
            time.sleep(1)