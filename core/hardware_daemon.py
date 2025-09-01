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
        self.last_pressure_time = 0
        self.last_do_time = 0
        self.last_pressure_time = 0

        state.set_hardware_interface(hardware_interface, daemon_mode=True)
        logging.info("HD HardwareDaemon инициализирован")

    def run(self):
        logging.info("HD HardwareDaemon запущен")
        last_report = time.time()
        while self.running:
            try:
                self._process_cycle()
                # Каждые 10 сек — отчёт
                if time.time() - last_report >= 60.0:
                    self.hw.log_quality_report()
                    # logging.info("Report!")
                    last_report = time.time()
                time.sleep(0.01)
            except Exception as e:
                logging.error(f"HD Ошибка в цикле: {e}", exc_info=True)
                time.sleep(1)

    def _process_cycle(self):
        now = time.time()
        self._schedule_commands(now)

        if not self.command_queue:
            return

        cmd = self.command_queue.pop(0)
        self._execute_command(cmd)

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
            self.last_di_time = now

        if now - self.last_ai_time >= 2.0:
            for pid in self.press_ids:
                ai_module = self.hw.hw_config["presses"][pid - 1]["modules"]["ai"]
                self.command_queue.append({
                    "type": "read_ai",
                    "module": ai_module,
                    "press_id": pid
                })
            # --- ЧТЕНИЕ DO (состояние выходов) ---
            #print("№ 2")
            for module_id in self._get_all_do_modules():
                self.command_queue.append({
                    "type": "read_do",
                    "module": module_id
                })
            self.last_ai_time = now

        if now - self.last_pressure_time >= 0.5:
            pressure_module = self.hw.hw_config["common"].get("ai_pressure_module")
            if pressure_module:
                self.command_queue.append({
                    "type": "read_ai",
                    "module": pressure_module,
                    "purpose": "pressures"  # Множественное число
                })
            self.last_pressure_time = now

        if now - self.last_do_time >= 0.5:  # Каждые 500 мс — синхронизация DO
            self.command_queue.append({"type": "write_do"})
            self.last_do_time = now

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


            elif cmd["type"] == "write_do":
                self._write_outputs()


        except Exception as e:
            logging.error(f"HD Ошибка выполнения команды {cmd}: {e}")

    def _write_outputs(self):
        urgent = state.get("urgent_do", {})
        if not urgent:
            return

        # 🔁 Выполняем все команды
        with self.hw.lock:
            for mid, (low, high) in urgent.items():
                try:
                    if self.hw._send_command(f"#{mid}00{low:02X}") and self.hw._send_command(f"#{mid}0B{high:02X}"):
                        # Только при успехе — удаляем
                        del urgent[mid]
                    #time.sleep(0.1)
                    #print(f"HD write #{mid}00{low:02X} + #{mid}0B{high:02X} ")
                except Exception as e:
                    logging.error(f"HD: ошибка при отправке #{mid}00{low:02X} и #{mid}0B{high:02X}: {e}")

            # После цикла — сохраняем оставшиеся
            state.set("urgent_do", urgent)

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