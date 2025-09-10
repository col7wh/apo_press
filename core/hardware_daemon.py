# core/hardware_daemon.py
"""
Фоновый демон: единый цикл с очередью команд.
Если очередь пуста — читаем DI (кнопки, E-Stop).
"""
import time
import logging
import traceback
from threading import Thread
from core.global_state import state


class HardwareDaemon(Thread):
    def __init__(self, hardware_interface):
        super().__init__(name="HardwareDaemon", daemon=True)
        self.hw = hardware_interface
        self.running = True
        self.press_ids = [1, 2, 3]
        self.command_queue = []
        self.last_di_time = 0
        self.last_ai_time = 0
        self.last_do_time = 0
        self.last_pressure_time = 0

        state.set_hardware_interface(hardware_interface, daemon_mode=True)
        logging.info("HD HardwareDaemon инициализирован")

    def run(self):
        logging.info("HD HardwareDaemon запущен")
        last_report = time.time()
        last_urgent_check = 0
        last_heating_check = 0

        while self.running:
            try:
                now = time.time()

                # 1. Планируем команды чтения
                self._schedule_commands(now)

                # 2. Выполняем команды из очереди
                if self.command_queue:
                    cmd = self.command_queue.pop(0)
                    self._execute_command(cmd)

                # 3. Отправляем DO — без команды в очереди
                if now - last_urgent_check >= 0.1:
                    self._write_urgent_do()
                    last_urgent_check = now

                if now - last_heating_check >= 1.0:
                    self._write_heating_do()
                    last_heating_check = now

                # 4. Отчёт
                if now - last_report >= 60.0:
                    self.hw.log_quality_report()
                    last_report = now

                time.sleep(0.01)

            except Exception as e:
                logging.error(f"HD Ошибка в цикле: {e}", exc_info=True)
                time.sleep(1)

    def _schedule_commands(self, now):
        if now - self.last_di_time >= 0.1:
            self.command_queue.append({
                "type": "read_di",
                "module": self.hw.hw_config["common"]["di_module"]
            })
            if self.hw.hw_config["common"].get("di_module_2"):
                self.command_queue.append({
                    "type": "read_di",
                    "module": self.hw.hw_config["common"]["di_module_2"]
                })

            # --- ЧТЕНИЕ DO (состояние выходов) ---
            for module_id in self._get_all_do_modules():
                self.command_queue.append({
                    "type": "read_do",
                    "module": module_id
                })

            self.last_di_time = now

        if now - self.last_ai_time >= 2.0:
            for pid in self.press_ids:
                ai_module = self.hw.hw_config["presses"][pid - 1]["modules"]["ai"]
                self.command_queue.append({
                    "type": "read_ai",
                    "module": ai_module,
                    "press_id": pid
                })
            self.last_ai_time = now

        if now - self.last_pressure_time >= 0.1:
            pressure_module = self.hw.hw_config["common"].get("ai_pressure_module")
            if pressure_module:
                self.command_queue.append({
                    "type": "read_ai",
                    "module": pressure_module,
                    "purpose": "pressures"  # Множественное число
                })
            self.last_pressure_time = now

    def _execute_command(self, cmd):
        try:
            if cmd["type"] == "read_di":
                value = self.hw.read_digital(cmd["module"])
                if value is not None:
                    state.set(f"di_module_{cmd['module']}", value)

            elif cmd["type"] == "read_ai":
                raw = self.hw.read_ai(cmd["module"])
                if raw and len(raw) >= 8:
                    try:
                        values = [float(v) for v in raw[:8]]
                    except (ValueError, TypeError):
                        values = [None] * 8

                    if cmd.get("purpose") == "pressures":
                        # Первые 3 значения — давления прессов 1, 2, 3
                        for pid in range(1, 4):
                            if pid <= len(values):
                                pressure = values[pid - 1]  # values[0], [1], [2]
                                state.set(f"press_{pid}_pressure", pressure)
                                #logging.info(f"HD Давление пресса {pid}: {pressure} МПа")
                    elif "press_id" in cmd:
                        state.set(f"press_{cmd['press_id']}_temps", values[:8])
                        #logging.info(f"HD Температуры пресса {cmd['press_id']}: {values[:8]}")
                    else:
                        logging.warning(f"HD Назначение AI-чтения неизвестно: {cmd}")

            elif cmd["type"] == "read_do":
                value = self.hw.read_digital(cmd["module"])
                #print(f"HD read {cmd['module']} = {value}")
                if value is not None:
                    state.set(f"do_state_{cmd['module']}", value)
                    #print(f"HD try read_digital {cmd['module']}, cyr val in state {value}")

        except Exception as e:
            logging.error(f"HD Ошибка выполнения команды {cmd}: {e}")

    def _write_outputs(self):
        urgent = state.get("urgent_do", {})
        if not urgent:
            return

        # 🔒 Копируем ключи, чтобы итерироваться безопасно
        keys_to_process = list(urgent.keys())
        success = True
        if keys_to_process != {}:
            print(f"HD urgent is {keys_to_process} ")

        with self.hw.lock:
            for mid in keys_to_process:
                if mid not in urgent:
                    continue  # мог быть удалён другим потоком
                low, high = urgent[mid]
                try:
                    cmd_low = f"#{mid}00{low:02X}"
                    cmd_high = f"#{mid}0B{high:02X}"

                    if self.hw._send_command(cmd_low) and self.hw._send_command(cmd_high):
                        # ✅ Успешно отправлено
                        full_value = (high << 8) | low
                        state.set(f"do_state_{mid}", full_value)
                        del urgent[mid]
                    else:
                        success = False
                except Exception as e:
                    logging.error(f"HD: ошибка при отправке #{mid}00{low:02X} и #{mid}0B{high:02X}: {e}")
                    success = False

        # ✅ Сохраняем оставшиеся команды
        if success:
            state.set("urgent_do", {})
        else:
            state.set("urgent_do", urgent)
            print(f"urgent not empty, continue")

    def _write_urgent_do(self):
        urgent = state.get("urgent_do_commands", {})
        if not urgent:
            return

        keys = list(urgent.keys())
        success = True

        with self.hw.lock:
            for mid in keys:
                if mid not in urgent:
                    continue
                low, high = urgent[mid]
                try:
                    if self.hw._send_command(f"#{mid}00{low:02X}") and self.hw._send_command(f"#{mid}0B{high:02X}"):
                        full = (high << 8) | low
                        state.set(f"do_state_{mid}", full)
                        """""    
                        if mid == "31":
                            # 🔍 Получаем стек вызова
                            stack = traceback.extract_stack()
                            # Берём предпоследний кадр — последний это сам set()
                            if low != 0 or high != 0 or self.trig:
                                self.trig = True
                                filename, line, func, text = stack[-2]
                                print(f"🟡 HD: {mid} = {high} {low}| Изменено в {func} ({filename}:{line})")
                            if high == 0 and low == 0:
                                self.trig = False
                        """

                        del urgent[mid]
                    else:
                        success = False
                except Exception as e:
                    logging.error(f"HD: ошибка отправки #{mid}: {e}")
                    success = False

        if success:
            state.set("urgent_do_commands", {})
        else:
            state.set("urgent_do_commands", urgent)

    def _write_heating_do(self):
        heating = state.get("heating_do_commands", {})
        if not heating:
            return

        keys = list(heating.keys())
        success = True

        with self.hw.lock:
            for mid in keys:
                if mid not in heating:
                    continue
                low, high = heating[mid]
                try:
                    if self.hw._send_command(f"#{mid}00{low:02X}") and self.hw._send_command(f"#{mid}0B{high:02X}"):
                        full = (high << 8) | low
                        state.set(f"do_state_{mid}", full)
                        del heating[mid]
                    else:
                        success = False
                except Exception as e:
                    logging.error(f"HD: ошибка отправки #{mid}: {e}")
                    success = False

        if success:
            state.set("heating_do_commands", {})
        else:
            state.set("heating_do_commands", heating)

    def _get_all_do_modules(self):
        """Возвращает список всех DO-модулей, которые нужно читать"""
        modules = set()

        # Из common
        common = self.hw.hw_config["common"]
        if "do_module_1" in common:
            modules.add(common["do_module_1"])
        if "do_module_2" in common:
            modules.add(common["do_module_2"])

        # Из каждого пресса
        for press in self.hw.hw_config["presses"]:
            do_mod = press["modules"]["do"]
            modules.add(do_mod)

        return list(modules)

    def stop(self):
        """Безопасная остановка"""
        self.running = False
        logging.info("HD  HardwareDaemon: остановка запрошена")