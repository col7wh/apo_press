# core/temp_control.py
"""
Многозонный on/off контроллер температуры с гистерезисом.
Управляет 8 зонами нагрева независимо.
Работает через global_state (шина команд).
"""
import json
import logging
import os
import sys
import threading
import time
from typing import Optional, List

from core.global_state import state
from core.pid_controller import PIDController

# Убедимся, что корень проекта в пути
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class TemperatureController(threading.Thread):
    def __init__(self, id_press: int):
        super().__init__(daemon=True)
        self._pwm_start = {}
        self.press_id = id_press
        self.running = True

        self.config_path = os.path.join("config", "hardware_config.json")
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)

        # Загружаем конфиг пресса
        press_cfg = self.config["presses"][id_press - 1]
        self.ai_module = press_cfg["modules"]["ai"]
        self.do_module = press_cfg["modules"]["do"]
        self.heater_channels = press_cfg["heater_channels"]  # [0,1,2,3,4,5,6,7] или [8,9,...]

        self.zones = len(self.heater_channels)
        self.targets = [None] * self.zones
        self.enabled = [True] * self.zones
        self.heating = [False] * self.zones

        logging.info(f"TC Пресс-{id_press+ 1}: TemperatureController инициализирован. Модуль {self.do_module}")

        # Загрузка конфига
        self.pids = []
        self.offsets = []
        self.pwm_period = 10.0
        self.load_config(self.press_id)

    def load_config_(self, id_press: int):
        self.pids = []
        self.offsets = []
        # Загрузка конфига
        with open("config/pid_config.json", "r") as f:
            pid_cfg = json.load(f)["presses"][id_press - 1]


        for zone_cfg in pid_cfg["zones"]:
            pid = PIDController(
                Kp=zone_cfg["Kp"],
                Ki=zone_cfg["Ki"],
                Kd=zone_cfg["Kd"],
                output_limits=(0, 100)  # % включения
            )
            self.pids.append(pid)

    def load_config(self, id_press: int):
        """Загружает конфиг и обновляет коэффициенты ПИД без сброса состояния"""
        try:
            with open("config/pid_config.json", "r") as f:
                pid_cfg = json.load(f)["presses"][id_press - 1]

            self.pwm_period = pid_cfg["pwm_period"]

            # Обновляем коэффициенты существующих PID
            for i, zone_cfg in enumerate(pid_cfg["zones"]):
                if i < len(self.pids):
                    self.pids[i].set_tunings(
                        Kp=zone_cfg["Kp"],
                        Ki=zone_cfg["Ki"],
                        Kd=zone_cfg["Kd"]
                    )
                else:
                    # Если зон больше — добавляем новые PID
                    pid = PIDController(
                        Kp=zone_cfg["Kp"],
                        Ki=zone_cfg["Ki"],
                        Kd=zone_cfg["Kd"],
                        output_limits=(0, 100)
                    )
                    self.pids.append(pid)

            # Обновляем оффсеты
            self.offsets = [zone["offset"] for zone in pid_cfg["zones"]]

            logging.debug(f"TC Пресс-{id_press + 1}: ПИД-коэффициенты обновлены на лету")

        except Exception as e:
            logging.error(f"TC Пресс-{id_press + 1}: ошибка загрузки PID-конфига: {e}")

    def read_all_temperatures(self) -> List[Optional[float]]:
        """Чтение всех 8 температур из global_state (шины)"""
        try:
            temps = state.read_ai(self.press_id)  # ✅ Читаем через шину
            if not temps or len(temps) < 7:
                logging.warning(f"TC Пресс-{self.press_id+ 1}: недостаточно данных температур")
                return [None] * 7
            return temps
        except Exception as e:
            logging.error(f"TC Пресс-{self.press_id+ 1}: ошибка чтения температур: {e}")
            return [None] * 7

    def _update_do_output(self):
        """Обновляет DO, включая/выключая нужные каналы"""
        for c_zone in range(self.zones):
            ch = self.heater_channels[c_zone]  # Правильный бит на DO-модуле
            desired = self.heating[c_zone]
            print(f"TC heat {self.do_module}, {ch}, {desired}")
            state.write_do_bit(self.do_module, ch, desired)

    def cool_all(self):
        self.running = False
        state.set(f"press_{self.press_id}_target_temp", None)
        for ch in self.heater_channels:
            # state.write_do_bit(self.do_module, ch, False)
            state.set_do_command(self.do_module, 0, 0, urgent=False)

    def run(self):
        logging.info(f"TC Пресс-{self.press_id+ 1}: поток нагрева запущен")
        last_mod = 0
        while self.running:

            config_path = "config/pid_config.json"
            try:
                mod_time = os.path.getmtime(config_path)
                if mod_time > last_mod:
                    self.load_config(self.press_id)
                    last_mod = mod_time
            except OSError:
                pass

            self.update()
            time.sleep(0.1)

    def update(self):
        target_temp = state.get(f"press_{self.press_id}_target_temp")
        # if target is None
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
                state.set_do_command(self.do_module, low, high, urgent=True)
                logging.info(f"TC Пресс-{self.press_id+1}: нагрев выключен (target_temp = None) ")
                logging.debug(f"TC Command{self.do_module, low, high}")
            return

        temps = self.read_all_temperatures()
        # 🔥 Читаем ТЕКУЩЕЕ состояние DO-модуля
        current_state = state.read_digital(self.do_module) or 0
        new_state = current_state  # Начинаем с текущего

        # Определяем свои биты
        my_channels = self.heater_channels  # [0,1,2,3] для Пресса 1, [4,5,6,7] для Пресса 2

        for cur_zone, ch in enumerate(my_channels):
            temp_list = temps[cur_zone]
            if temp_list is None:
                continue

            # PID
            self.pids[cur_zone].set_setpoint(target_temp)

            # Вычисляем выход
            output = self.pids[cur_zone].compute(temps[cur_zone])
            state.set(f"press_{self.press_id}_temp{cur_zone}_pid", round(output, 2))
            should_heat = output > 10  # >10% → включаем

            mask = 1 << ch

            if output >= 100.0:
                # 🔥 Всегда включено
                new_state |= mask
            elif output <= 10.0:
                # ❌ Всегда выключено
                new_state &= ~mask
            else:
                # 🌀 ШИМ: 10% < output < 100%
                on_time = (output / 100.0) * self.pwm_period
                off_time = self.pwm_period - on_time

                # База времени — timestamp зоны
                if not hasattr(self, '_pwm_start'):
                    self._pwm_start = {}
                if cur_zone not in self._pwm_start:
                    self._pwm_start[cur_zone] = time.time()

                dt = time.time() - self._pwm_start[cur_zone]
                phase = dt % self.pwm_period

                if phase < on_time:
                    new_state |= mask  # ON
                else:
                    new_state &= ~mask  # OFF

        # 🔥 Только если изменилось — отправляем
        if current_state != new_state:
            low = new_state & 0xFF
            high = (new_state >> 8) & 0xFF
            state.set_do_command(self.do_module, low, high, urgent=False)

    def stop(self):
        logging.info(f"TC Пресс-{self.press_id+ 1}: остановлен")
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
        print("✅ Мок-режим активирован ----")
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

    # tc = TemperatureController(press_id=press_id, hardware_interface=hw)
    # test
    tc = TemperatureController(id_press=press_id)
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
                # Need refactor
                # tc.set_target(zone, temp)
                input("Нажмите Enter...")
            except:
                print("Ошибка ввода")
                time.sleep(1)

        elif cmd == "2":
            try:
                zone = int(input("Зона (1–8): ")) - 1
                # tc.disable_zone(zone)
                input("Нажмите Enter...")
            except:
                print("Ошибка ввода")
                time.sleep(1)

        elif cmd == "3":
            tc.cool_all()
            input("Нажмите Enter...")

        elif cmd == "4":
            temps = tc.read_all_temperatures()
            # status = tc.update()
            # print("\n📊 Статус зон:")
            # for z in range(8):
            #     t = temps[z] if temps[z] is not None else "N/A"
            #     s = status[z]
            #     print(f"  Зона {z + 1}| {s}")
            # input("\nНажмите Enter...")

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

                    # for z in range(8):
                    #     t = f"{temps[z]:>4.1f}" if temps[z] is not None else " N/A"
                    #     target = f"{status[z]['target']:>5.1f}" if status[z]['target'] is not None else "  N/A"
                    #     cmd = " ВКЛ " if status[z]['heating_cmd'] else " ВЫК "
                    #     real = " ВКЛ " if status[z]['heating_bit'] else " ВЫК "
                    #     stat = f"{status[z]['status']:^7}"

                    # print(f" {z + 1}  | {t} |  {target} | {cmd} | {real} | {stat}")
                    time.sleep(1.0)

            except KeyboardInterrupt:
                print("\n\nВыход из режима реального времени")

        elif cmd == "6":
            try:
                temp = float(input("Введите уставку для всех зон (°C): "))
                # tc.set_target_all(temp)
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
