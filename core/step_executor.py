# core/step_executor.py
"""
Управляет параллельным выполнением температурной и силовой программы.
Использует два независимых движка шагов.
"""
import time
import logging
import threading
from typing import Dict, List
from core.global_state import state


class StepEngine:
    """
    Универсальный исполнитель последовательности шагов.
    Может управлять температурой, давлением, движением.
    """
    def __init__(self, name: str, press_id: int, hw_config: dict, done_callback=None):
        self.name = name
        self.press_id = press_id
        self.hw_config = hw_config
        self.done_callback = done_callback
        self.program: List[Dict] = []
        self.current_step_index = 0
        self.step_in_progress = False
        self.start_time = 0.0
        self.active = False

    def load(self, program: List[Dict]):
        """Загружает программу в движок"""
        self.program = program
        self.current_step_index = 0
        self.active = True
        state.set(f"press_{self.press_id}_{self.name}_completed", False)
        logging.info(f"SE Пресс-{self.press_id+1} ({self.name}): загружено {len(program)} шагов")

    def update(self, pause_mode=False):
        """Вызывается каждый цикл. Выполняет текущий шаг."""
        if self.current_step_index >= len(self.program):
            state.set(f"press_{self.press_id}_{self.name}_completed", True)
        if not self.active or self.current_step_index >= len(self.program):
            # ✅ Останавливаем счёт времени, если программа закончилась
            if state.get(f"press_{self.press_id}_step_running_{self.name}", False):
                state.set(f"press_{self.press_id}_step_running_{self.name}", False)
            return

        step = self.program[self.current_step_index]
        if not isinstance(step, dict):
            logging.error(f"SE Пресс-{self.press_id+1} ({self.name}): шаг не словарь: {step}")
            self._complete_step()
            return

        step_type = step.get("step")
        if not step_type:
            logging.warning(f"SE Пресс-{self.press_id+1} ({self.name}): нет 'step' в {step}")
            self._complete_step()
            return

        # Начало нового шага
        if not self.step_in_progress:
            self.start_time = time.time()
            self.step_in_progress = True
            self._on_step_start(step)

        # ✅ Обновляем время шага
        if self.step_in_progress and not pause_mode:
            elapsed = time.time() - self.start_time
            state.set(f"press_{self.press_id}_step_elapsed_{self.name}", elapsed)

        # Выполнение
        try:
            if self._execute_step(step):
                # Сохраним предыдущую уставку
                last = step.get("target_temp") or 1
                state.set(f"press_{self.press_id}_last_target_tem", last)
                self._on_step_complete(step)
                self.current_step_index += 1
                self.step_in_progress = False
        except Exception as e:
            self._complete_step()
            self.step_in_progress = False
            elapsed = time.time() - self.start_time
            target = state.get(f"press_{self.press_id}_target_temp")
            logging.error(f"SE Пресс-{self.press_id+1} ({self.name}): ошибка в шаге {step_type}: {e}")
            # logging.error(f"SE Пресс-{step}, target={target} ")
            logging.info(f"SE Пресс-{self.name}, id {threading.get_native_id()} ")
            val = state.get(f"press_{self.press_id}_current_step_ramp_temp")
            logging.info(f"SE Пресс-{self.name} = {val} ")

    def _on_step_start(self, step: Dict):
        """Вызывается при старте шага"""
        self.start_time = time.time()
        self.step_in_progress = True

        # Определяем длительность шага
        if step["step"] == "ramp_temp":
            ramp = step.get("ramp_time", 1)
            hold = step.get("hold_time", 1)
            ramp = ramp if ramp is not None else 0
            hold = hold if hold is not None else 0
            duration = int(ramp + hold)

        elif step["step"] == "ramp_pressure" or step["step"] == "hold":
            ramp = step.get("ramp_time", 1)
            hold = step.get("hold_time", 1)
            ramp = ramp if ramp is not None else 0
            hold = hold if hold is not None else 0
            duration = int(ramp + hold)
        elif step["step"] == "open_mold":
            duration = step.get("hold_time", 12)
        elif step["step"] in ["pause", "cool", "pressure_control"]:
            duration = step.get("duration", 0)
        else:
            duration = 0  # для шагов без времени

        # Сохраняем длительность
        state.set(f"press_{self.press_id}_step_duration_{self.name}", duration)

        state.set(f"press_{self.press_id}_current_step_{self.name}", {
            "index": self.current_step_index,
            "type": step.get("step"),
            "start_time": self.start_time,
            "target_temp": step.get("target_temp"),
            "target_pressure": step.get("target_pressure"),
            "с": step.get("duration", 0),
            "duration": duration
        })
        state.set(f"press_{self.press_id}_step_status_{self.name}", "running")
        # ✅ Устанавливаем время шага в state
        state.set(f"press_{self.press_id}_step_elapsed_{self.name}", 0.0)
        state.set(f"press_{self.press_id}_step_running_{self.name}", True)
        logging.info(f"SE Пресс-{self.press_id+1} ({self.name}): шаг {self.current_step_index + 1} '{step.get('step')}'")

    def _on_step_complete(self, step: Dict):
        """Вызывается при завершении шага"""
        state.set(f"press_{self.press_id}_step_status_{self.name}", "completed")

        if self.done_callback:
            self.done_callback(step)

    def _execute_step(self, step: Dict) -> bool:
        """Выполняет один шаг. Возвращает True, если завершён."""
        step_type = step["step"]

        # === ТЕМПЕРАТУРА ===
        if step_type == "heat":
            return self._execute_heat(step)

        elif step_type == "ramp_temp":
            return self._execute_ramp_temp(step)

        elif step_type == "cool":
            return self._execute_cool(step)

        # === ДАВЛЕНИЕ И ДВИЖЕНИЕ ===
        elif step_type == "lift_to_limit":
            return self._execute_lift_to_limit()

        elif step_type == "pressure_control":
            return self._execute_pressure_control(step)

        elif step_type == "ramp_pressure" or step_type == "hold":
            return self._execute_ramp_pressure(step)

        elif step_type == "open_mold":
            return self._execute_open_mold(step)

        elif step_type == "pause":
            return self._execute_pause(step)

        else:
            logging.warning(f"SE Пресс-{self.press_id+1} ({self.name}): неизвестный шаг: {step_type}")
            self._complete_step()
            return True

    def _execute_heat(self, step: Dict) -> bool:
        target_temp = step.get("target_temp", 50.0)
        max_duration = step.get("max_duration", 600)

        state.set(f"press_{self.press_id}_target_temp", target_temp)

        temps = state.get(f"press_{self.press_id}_temps", [None] * 8)
        all_reached = True
        for t in temps:
            if t is not None and t < target_temp - 2.0:
                all_reached = False
                break

        if all_reached:
            logging.info(f"SE Пресс-{self.press_id+ 1}: нагрев до {target_temp}°C завершён")
            self._complete_step()
            return True

        elapsed = time.time() - self.start_time
        if elapsed > max_duration:
            logging.warning(f"SE Пресс-{self.press_id+ 1}: превышено время нагрева")
            self._complete_step()
            return True

        return False

    def _execute_ramp_temp(self, step: Dict) -> bool:
        target_temp = step.get("target_temp", 100.0)
        ramp_time = step.get("ramp_time", 0) or 1
        hold_time = step.get("hold_time", 0) or 1

        if target_temp is None or target_temp == 0:
            logging.info(f"SE Пресс-{self.press_id + 1}: target_temp is None")
            target_temp = 99

        if not hasattr(self, '_ramp_start_time'):
            self._ramp_start_time = time.time()

            # Если шаг первый, то средняя уставка, если не первый - то предыдущая
            last = state.get(f"press_{self.press_id}_last_target_tem", 1)
            if last == 1 or None:
                temps = state.get(f"press_{self.press_id}_temps", [20.0] * 7)
                valid_temps = [t for t in temps[:7] if t is not None]
            else:
                valid_temps = [last] * 7
            self._start_temp = sum(valid_temps) / len(valid_temps) if valid_temps else 20.0
            logging.info(f"SE Press {self.press_id+ 1} Start temp = {self._start_temp}")
            logging.info(f"SE Current {valid_temps}")

        elapsed = time.time() - self._ramp_start_time

        if elapsed < ramp_time and ramp_time > 0:
            ratio = elapsed / ramp_time
            current_target = self._start_temp + (target_temp - self._start_temp) * ratio
            state.set(f"press_{self.press_id}_target_temp", current_target)
            return False

        state.set(f"press_{self.press_id}_target_temp", target_temp)

        if not hasattr(self, '_hold_start_time'):
            self._hold_start_time = time.time()

        hold_elapsed = time.time() - self._hold_start_time
        if hold_elapsed >= hold_time:
            logging.info(f"SE Пресс-{self.press_id+ 1}: выдержка при {target_temp}°C завершена")
            # state.set(f"press_{self.press_id}_target_temp", 0)
            self._complete_step()
            return True

        return False

    def _execute_cool(self, step: Dict) -> bool:
        duration = step.get("duration", 300)
        if not hasattr(self, '_cool_start_time'):
            self._cool_start_time = time.time()
            state.set(f"press_{self.press_id}_target_temp", None)

        elapsed = time.time() - self._cool_start_time
        if elapsed >= duration:
            logging.info(f"SE Пресс-{self.press_id+ 1}: охлаждение завершено")
            self._complete_step()
            return True

        return False

    def _execute_lift_to_limit(self) -> bool:
        try:
            press_cfg = self.hw_config["presses"][self.press_id - 1]
            di_module = press_cfg["control_inputs"]["limit_switch"]["module"]
            limit_bit = press_cfg["control_inputs"]["limit_switch"]["bit"]
        except (IndexError, KeyError) as e:
            logging.error(f"SE Пресс-{self.press_id+ 1}: ошибка конфига: {e}")
            return False

        # write
        state.set(f"press_{self.press_id}_valve_lift_up", True)
        # wheat
        di_value = state.get(f"di_module_{di_module}", 0)
        limit_reached = bool(di_value & (1 << limit_bit))

        if limit_reached:
            state.set(f"press_{self.press_id}_valve_lift_up", False)
            logging.info(f"SE Пресс-{self.press_id+ 1}: подъём завершён")
            self._complete_step()
            return True

        return False

    def _execute_pressure_control(self, step: Dict) -> bool:
        target_pressure = step.get("pressure", 5.0)
        duration = step.get("duration", 180)

        if not hasattr(self, '_pressure_start_time'):
            state.set(f"press_{self.press_id}_target_pressure", target_pressure)
            self._pressure_start_time = time.time()

        elapsed = time.time() - self._pressure_start_time
        if elapsed >= duration:
            logging.info(f"SE Пресс-{self.press_id+ 1}: выдержка при {target_pressure} МПа завершена")
            self._complete_step()
            return True

        return False

    def _execute_ramp_pressure(self, step: Dict) -> bool:
        target_pressure = step.get("target_pressure", 5.0)
        ramp_time = step.get("ramp_time", 0)
        hold_time = step.get("hold_time", 0)

        if not hasattr(self, '_ramp_start_time'):
            self._ramp_start_time = time.time()
            self._start_pressure = target_pressure

        elapsed = time.time() - self._ramp_start_time

        if elapsed < ramp_time and ramp_time > 0:
            ratio = elapsed / ramp_time
            current_target = self._start_pressure + (target_pressure - self._start_pressure) * ratio
            state.set(f"press_{self.press_id}_target_pressure", current_target)
            return False

        state.set(f"press_{self.press_id}_target_pressure", target_pressure)

        if not hasattr(self, '_hold_start_time'):
            self._hold_start_time = time.time()

        hold_elapsed = time.time() - self._hold_start_time
        if hold_elapsed >= hold_time:
            logging.info(f"SE Пресс-{self.press_id+ 1}: выдержка при {target_pressure} МПа завершена")
            self._complete_step()
            return True

        return False

    def _execute_open_mold(self, step: Dict) -> bool:
        state.set(f"press_{self.press_id}_target_pressure", 0)
        state.set(f"press_{self.press_id}_valve_open", False)
        state.set(f"press_{self.press_id}_valve_close", False)
        hold_time = step.get("hold_time", 30)

        result = self.dir_execute_open_mold(hold_time)
        if result:
            logging.info(f"SE Пресс-{self.press_id+ 1}: размыкание завершено")
            self._complete_step()
            state.set(f"press_{self.press_id}_pressure_completed", True)
        return result

    def dir_execute_open_mold(self, hold_time: int) -> bool:
        if not hasattr(self, 'open_start_time'):
            self.open_start_time = time.time()
            try:
                state.set(f"press_{self.press_id}_valve_lift_down", True)
            except Exception as e:
                logging.error(f"SE Пресс-{self.press_id+ 1}: ошибка при размыкании: {e}")

        elapsed = time.time() - self.open_start_time
        if elapsed >= hold_time:
            try:
                state.set(f"press_{self.press_id}_valve_lift_down", False)
            except Exception as e:
                logging.error(f"SE Пресс-{self.press_id+ 1}: ошибка выключения клапана: {e}")
            return True

        return False

    def _execute_pause(self, step: Dict) -> bool:
        duration = step.get("duration", 10)
        if not hasattr(self, '_pause_start_time'):
            self._pause_start_time = time.time()

        elapsed = time.time() - self._pause_start_time
        if elapsed >= duration:
            logging.info(f"SE Пресс-{self.press_id+ 1}: пауза завершена")
            self._complete_step()
            return True

        return False

    def _complete_step(self):
        """Завершает текущий шаг"""
        state.set(f"press_{self.press_id}_step_status_{self.name}", "completed")
        state.set(f"press_{self.press_id}_valve_lift_up", False)
        state.set(f"press_{self.press_id}_valve_lift_down", False)
        # ✅ Останавливаем счёт времени
        state.set(f"press_{self.press_id}_step_running_{self.name}", False)
        # Удаляем временные атрибуты
        for attr in ['_ramp_start_time', '_hold_start_time', '_pressure_start_time',
                     '_open_start_time', '_pause_start_time', '_cool_start_time']:
            if hasattr(self, attr):
                delattr(self, attr)


class StepExecutor(threading.Thread):
    """
    Основной исполнитель программы пресса.
    Управляет двумя независимыми потоками: температура и давление.
    """
    def __init__(self, press_id: int):
        super().__init__(name=f"StepExecutor-{press_id}", daemon=True)
        self.press_id = press_id
        self.running = False

        # Загружаем конфиг
        try:
            import json
            with open("config/hardware_config.json", "r", encoding="utf-8") as f:
                self.hw_config = json.load(f)
        except Exception as e:
            logging.error(f"SE Пресс-{press_id+ 1}: не удалось загрузить config: {e}")
            self.hw_config = {}

        # Два движка
        self.temp_engine = StepEngine("temperature", press_id, self.hw_config)
        self.press_engine = StepEngine("pressure", press_id, self.hw_config)

    def load_programs(self, temp_program: List[Dict], pressure_program: List[Dict]):
        """Загружает программы для обоих движков"""
        self.temp_engine.load(temp_program)
        self.press_engine.load(pressure_program)

    def run(self):
        logging.info(f"SE Пресс-{self.press_id+ 1}: запущен = id {threading.get_native_id()} ")
        state.set(f"press_{self.press_id}_cycle_end", False)
        # ✅ Начинаем отсчёт общего времени
        state.set(f"press_{self.press_id}_cycle_start_time", time.time())
        state.set(f"press_{self.press_id}_cycle_elapsed", 0.0)
        state.set(f"press_{self.press_id}_cycle_running", True)

        self.running = True
        while self.running:
            try:
                is_paused = state.get(f"press_{self.press_id}_paused", False)
                # Обновляем логику шагов (но не время)
                self.temp_engine.update(pause_mode=is_paused)
                self.press_engine.update(pause_mode=is_paused)

                # Закончились ли шаги?
                running_pressure = state.get(f"press_{self.press_id}_pressure_completed", False)
                running_temperature = state.get(f"press_{self.press_id}_temperature_completed", False)

                # ✅ Обновляем общее время цикла
                if not is_paused and state.get(f"press_{self.press_id}_cycle_running", False):
                    start = state.get(f"press_{self.press_id}_cycle_start_time")
                    if start:
                        elapsed = time.time() - start
                        total = state.get(f"press_{self.press_id}_cycle_elapsed", 0.0)
                        # Накапливаем, но не пересчитываем каждый раз
                        state.set(f"press_{self.press_id}_cycle_elapsed", total + elapsed)
                        state.set(f"press_{self.press_id}_cycle_start_time", time.time())

                # Окончание цикла
                if running_temperature and running_pressure:
                    # self.running = False  Рано
                    state.set(f"press_{self.press_id}_target_temp", None)
                    state.set(f"press_{self.press_id}_completed", True)
                    logging.info(f"SE Пресс-{self.press_id+ 1}: Шаги завершены")

            except Exception as e:
                logging.error(f"SE Пресс-{self.press_id+ 1}: ошибка в цикле: {e}")
            time.sleep(0.04)

        logging.info(f"SE Пресс-{self.press_id+ 1}: Шаги завершены")

        # ✅ При остановке — останавливаем счёт
        state.set(f"press_{self.press_id}_cycle_running", False)

    def stop(self):
        self.running = False
        self.temp_engine.active = False
        self.press_engine.active = False
        # ✅ Останавливаем счёт времени
        state.set(f"press_{self.press_id}_cycle_running", False)
        logging.info(f"SE Пресс-{self.press_id+ 1}: остановлен поток")
