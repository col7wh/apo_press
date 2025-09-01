# core/step_executor.py
"""
Управляет параллельным выполнением температурной и силовой программы.
Использует два независимых движка шагов.
"""
import time
import logging
import threading
from typing import Dict, Any, Optional, List
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
        logging.info(f"SE Пресс-{self.press_id} ({self.name}): загружено {len(program)} шагов")

    def update(self):
        """Вызывается каждый цикл. Выполняет текущий шаг."""
        if not self.active or self.current_step_index >= len(self.program):
            return

        step = self.program[self.current_step_index]
        if not isinstance(step, dict):
            logging.error(f"SE Пресс-{self.press_id} ({self.name}): шаг не словарь: {step}")
            self._complete_step()
            return

        step_type = step.get("step")
        if not step_type:
            logging.warning(f"SE Пресс-{self.press_id} ({self.name}): нет 'step' в {step}")
            self._complete_step()
            return

        # Начало нового шага
        if not self.step_in_progress:
            self.start_time = time.time()
            self.step_in_progress = True
            self._on_step_start(step)

        # Выполнение
        try:
            if self._execute_step(step):
                self._on_step_complete(step)
                self.current_step_index += 1
                self.step_in_progress = False
        except Exception as e:
            logging.error(f"SE Пресс-{self.press_id} ({self.name}): ошибка в шаге {step_type}: {e}")
            self._complete_step()

    def _on_step_start(self, step: Dict):
        """Вызывается при старте шага"""
        state.set(f"press_{self.press_id}_current_step_{self.name}", {
            "index": self.current_step_index,
            "type": step.get("step"),
            "start_time": self.start_time,
            "target_temp": step.get("target_temp"),
            "target_pressure": step.get("target_pressure"),
            "duration": step.get("duration", 0)
        })
        state.set(f"press_{self.press_id}_step_status_{self.name}", "running")
        logging.info(f"SE Пресс-{self.press_id} ({self.name}): шаг {self.current_step_index + 1} '{step.get('step')}'")

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
            return self._execute_lift_to_limit(step)

        elif step_type == "pressure_control":
            return self._execute_pressure_control(step)

        elif step_type == "ramp_pressure":
            return self._execute_ramp_pressure(step)

        elif step_type == "open_mold":
            return self._execute_open_mold(step)

        elif step_type == "pause":
            return self._execute_pause(step)

        else:
            logging.warning(f"SE Пресс-{self.press_id} ({self.name}): неизвестный шаг: {step_type}")
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
            logging.info(f"SE Пресс-{self.press_id}: нагрев до {target_temp}°C завершён")
            self._complete_step()
            return True

        elapsed = time.time() - self.start_time
        if elapsed > max_duration:
            logging.warning(f"SE Пресс-{self.press_id}: превышено время нагрева")
            self._complete_step()
            return True

        return False

    def _execute_ramp_temp(self, step: Dict) -> bool:
        target_temp = step.get("target_temp", 100.0)
        ramp_time = step.get("ramp_time", 0)
        hold_time = step.get("hold_time", 0)

        if not hasattr(self, '_ramp_start_time'):
            self._ramp_start_time = time.time()
            temps = state.get(f"press_{self.press_id}_temps", [20.0] * 8)
            valid_temps = [t for t in temps if t is not None]
            self._start_temp = sum(valid_temps) / len(valid_temps) if valid_temps else 20.0

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
            logging.info(f"SE Пресс-{self.press_id}: выдержка при {target_temp}°C завершена")
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
            logging.info(f"SE Пресс-{self.press_id}: охлаждение завершено")
            self._complete_step()
            return True

        return False

    def _execute_lift_to_limit(self, step: Dict) -> bool:
        try:
            press_cfg = self.hw_config["presses"][self.press_id - 1]
            valves = press_cfg["valves"]
            di_module = self.hw_config["common"]["di_module_2"]
            limit_bit = press_cfg["control_inputs"]["limit_switch"]["bit"]
        except (IndexError, KeyError) as e:
            logging.error(f"SE Пресс-{self.press_id}: ошибка конфига: {e}")
            return False

        state.write_do_bit(valves["lift_up"]["module"], valves["lift_up"]["bit"], True)

        di_value = state.get(f"di_module_{di_module}", 0)
        limit_reached = bool(di_value & (1 << limit_bit))

        if limit_reached:
            state.write_do_bit(valves["lift_up"]["module"], valves["lift_up"]["bit"], False)
            logging.info(f"SE Пресс-{self.press_id}: подъём завершён")
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
            logging.info(f"SE Пресс-{self.press_id}: выдержка при {target_pressure} МПа завершена")
            self._complete_step()
            return True

        return False

    def _execute_ramp_pressure(self, step: Dict) -> bool:
        target_pressure = step.get("target_pressure", 5.0)
        ramp_time = step.get("ramp_time", 0)
        hold_time = step.get("hold_time", 0)

        if not hasattr(self, '_ramp_start_time'):
            self._ramp_start_time = time.time()
            current_press = state.get(f"press_{self.press_id}_pressure", 0.0)
            self._start_pressure = current_press

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
            logging.info(f"SE Пресс-{self.press_id}: выдержка при {target_pressure} МПа завершена")
            self._complete_step()
            return True

        return False

    def _execute_open_mold(self, step: Dict) -> bool:
        if not hasattr(self, '_open_start_time'):
            self._open_start_time = time.time()
            try:
                valves = self.hw_config["presses"][self.press_id - 1]["valves"]
                state.write_do_bit(valves["open"]["module"], valves["open"]["bit"], True)
                state.set(f"press_{self.press_id}_target_temp", None)
            except Exception as e:
                logging.error(f"SE Пресс-{self.press_id}: ошибка при размыкании: {e}")

        elapsed = time.time() - self._open_start_time
        if elapsed >= 5.0:
            try:
                valves = self.hw_config["presses"][self.press_id - 1]["valves"]
                state.write_do_bit(valves["open"]["module"], valves["open"]["bit"], False)
            except Exception as e:
                logging.error(f"SE Пресс-{self.press_id}: ошибка выключения клапана: {e}")
            logging.info(f"SE Пресс-{self.press_id}: размыкание завершено")
            self._complete_step()
            return True

        return False

    def _execute_pause(self, step: Dict) -> bool:
        duration = step.get("duration", 10)
        if not hasattr(self, '_pause_start_time'):
            self._pause_start_time = time.time()

        elapsed = time.time() - self._pause_start_time
        if elapsed >= duration:
            logging.info(f"SE Пресс-{self.press_id}: пауза завершена")
            self._complete_step()
            return True

        return False

    def _complete_step(self):
        """Завершает текущий шаг"""
        state.set(f"press_{self.press_id}_step_status_{self.name}", "completed")
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
            logging.error(f"SE Пресс-{press_id}: не удалось загрузить config: {e}")
            self.hw_config = {}

        # Два движка
        self.temp_engine = StepEngine("temperature", press_id, self.hw_config)
        self.press_engine = StepEngine("pressure", press_id, self.hw_config)

    def load_programs(self, temp_program: List[Dict], pressure_program: List[Dict]):
        """Загружает программы для обоих движков"""
        self.temp_engine.load(temp_program)
        self.press_engine.load(pressure_program)

    def run(self):
        logging.info(f"SE Пресс-{self.press_id}: запущен")
        self.running = True
        while self.running:
            try:
                self.temp_engine.update()
                self.press_engine.update()
            except Exception as e:
                logging.error(f"SE Пресс-{self.press_id}: ошибка в цикле: {e}")
            time.sleep(0.1)

    def stop(self):
        self.running = False
        self.temp_engine.active = False
        self.press_engine.active = False
        logging.info(f"SE Пресс-{self.press_id}: остановлен")