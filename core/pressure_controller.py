# core/pressure_controller.py
import json
import time
import logging
import os
from core.global_state import state
from core.pid_controller import PIDController


class PressureController:
    def __init__(self, press_id: int):
        self.press_id = press_id
        self.logger = logging.getLogger(f"PC_Pressure-{press_id}")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.FileHandler(f"logs/pressure_{press_id}.log", encoding="utf-8")
            formatter = logging.Formatter('%(asctime)s [PC-%(name)s] %(levelname)s: %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

        # Загрузка конфига
        self.config_h = self._load_config_h()
        self.config = self._load_config()
        self.valves = self.config_h["presses"][press_id - 1]["valves"]
        self.pid = None
        self._last_output = 0.0
        self._last_time = time.time()

        # Инициализация ПИД
        self._setup_pid()

    def _load_config(self):
        with open("config/pid_config.json", "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_config_h(self):
        config_path = os.path.join("config", "hardware_config.json")
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _setup_pid(self):
        pid_cfg = self.config["presses"][self.press_id - 1]["pressure_pid"]
        self.pid = PIDController(
            Kp=pid_cfg["Kp"],
            Ki=pid_cfg["Ki"],
            Kd=pid_cfg["Kd"],
            setpoint=0.0,
            output_limits=(-100, 100)  # -100% = full down, +100% = full up
        )

    def set_target_pressure(self, pressure: float):
        """Устанавливает уставку давления"""
        if self.pid:
            self.pid.set_setpoint(pressure)
            self.logger.info(f"Уставка давления: {pressure} МПа")

    def update(self):
        """Вызывается каждую секунду из HardwareDaemon или ControlManager"""
        target = state.get(f"press_{self.press_id}_target_pressure", 0.0)
        if target <= 0:
            self._stop_all()
            return

        pressure = state.get(f"press_{self.press_id}_pressure", None)
        if pressure is None:
            return

        # Обновляем ПИД
        output = self.pid.compute(pressure)
        self._apply_output(output)

    def _apply_output(self, output: float):
        """Преобразует выход ПИД в управление клапанами"""
        if abs(output) < 5:  # Зона нечувствительности
            self._set_valve("open", False)
            self._set_valve("close", False)
            return

        if output > 0:
            self._set_valve("open", True)
            self._set_valve("close", False)
        else:
            self._set_valve("open", False)
            self._set_valve("close", True)

        self._last_output = output

    def _set_valve(self, valve_name: str, on: bool):
        """Ставит команду в срочную очередь"""
        valve = self.valves.get(valve_name)
        if not valve:
            return

        module = valve["module"]
        bit = valve["bit"]

        current = state.read_digital(module) or 0
        mask = 1 << bit
        new_state = (current | mask) if on else (current & ~mask)

        low = new_state & 0xFF
        high = (new_state >> 8) & 0xFF

        # 🔥 Ставим в срочную очередь
        state.set_do_command(module, low, high, urgent=True)
        self.logger.debug(f"Клапан {valve_name} ({module}.{bit}): {'ON' if on else 'OFF'}")

    def _stop_all(self):
        """Останавливает все клапаны"""
        self._set_valve("open", False)
        self._set_valve("close", False)

    def stop(self):
        """Остановка регулятора"""
        self._stop_all()
        self.logger.info("Регулятор давления остановлен")