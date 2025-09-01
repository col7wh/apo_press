# core/mock_hardware.py

import time
import logging

class MockHardwareInterface:
    """
    Мок-интерфейс для отладки.
    Поддерживает имитацию: нагрева, E-Stop, обрыва термопары.
    """

    def __init__(self):
        self._temperature = 25.0
        self._do_state = 0
        self._e_stop_pressed = False
        self._door_open = False
        self._press_closed = True
        self._thermocouple_broken = False  # ← НОВОЕ: обрыв термопары
        logging.info("MockHardwareInterface: инициализирован")

    def read_temperature(self, press_id: int) -> float:
        """Имитация нормальной работы и обрыва термопары"""
        if self._thermocouple_broken:
            # При обрыве термопара может возвращать 0.0, -1.0 или 9999.0
            return 0.0  # типичное поведение I-7017
        else:
            if self._do_state & 0x01:  # нагрев включён
                self._temperature += 5.0
                if self._temperature > 200:
                    self._temperature = 200
            elif self._temperature > 25.0:
                self._temperature -= 1.0
            return round(self._temperature, 1)

    def read_digital(self, module_id: str) -> int:
        """Имитация DI/DO"""
        if module_id == "37":  # DI-модуль
            value = 0xFFFF
            if self._e_stop_pressed:
                value &= 0xFFFE  # E-Stop
            if self._door_open:
                value &= 0xFFFD  # дверь
            if not self._press_closed:
                value &= 0xFFFB  # пресс
            return value
        return self._do_state

    def write_do(self, module_id: str, low_byte: int, high_byte: int):
        self._do_state = ((high_byte & 0xFF) << 8) | (low_byte & 0xFF)
        logging.info(f"Mock: DO-{module_id} ← LOW=0x{low_byte:02X}, HIGH=0x{high_byte:02X}")

    @property
    def hw_config(self):
        return {
            "presses": [
                {"id": 1, "modules": {"ai": "08", "do": "31"}},
                {"id": 2, "modules": {"ai": "11", "do": "32"}},
                {"id": 3, "modules": {"ai": "17", "do": "33"}}
            ],
            "common": {"di_module": "37"}
        }

    # 🔧 Методы для тестирования аварий
    def trigger_emergency(self):
        self._e_stop_pressed = True
        logging.warning("Mock: активирована аварийная остановка (E-Stop)")

    def release_emergency(self):
        self._e_stop_pressed = False
        logging.info("Mock: E-Stop отпущена")

    def break_thermocouple(self):
        self._thermocouple_broken = True
        logging.warning("Mock: имитация ОТРЫВА ТЕРМОПАРЫ (T=0.0)")

    def fix_thermocouple(self):
        self._thermocouple_broken = False
        logging.info("Mock: термопара восстановлена")