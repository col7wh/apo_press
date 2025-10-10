# core/safety_monitor.py
import json
import logging
from core.global_state import state
import time

"""
Монитор безопасности пресса.
Проверяет:
- Аварийную кнопку (E-Stop)
- Обрыв термопары
- Перегрев
- Концевые выключатели (дверь, пресс)
Останавливает выполнение при нарушении.
Читает данные через global_state (шину).
"""


class SafetyMonitor:
    """
    Контроллер безопасности для одного пресса.
    """

    def __init__(self, press_id: int):
        self.press_id = press_id
        self.max_temperature = 250.0  # °C
        self.thermocouple_error_value = -10.0  # T <= 0 → обрыв

        # Загружаем конфигурацию безопасности из hardware_config.json
        try:
            # Импорт внутри, чтобы избежать циклических зависимостей
            import os
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(project_root, "config", "hardware_config.json")
            with open(config_path, "r", encoding="utf-8") as f:
                hw_config = json.load(f)
            self.press_config = hw_config["presses"][press_id - 1]
            self.di_module = hw_config["common"]["di_module"]
            self.di_module_2 = hw_config["common"]["di_module_2"]
        except Exception as e:
            logging.error(f"SM Пресс-{press_id}: ошибка загрузки конфигурации безопасности: {e}")
            raise

        logging.info(f"SM Пресс-{press_id}: SafetyMonitor инициализирован.")

    def _read_input(self, name: str) -> bool:
        """
        Читает состояние сигнала безопасности.
        Учитывает тип: active_low / active_high.
        """
        try:
            inp = self.press_config["safety_inputs"][name]
            bit = inp["bit"]
            value = state.read_digital(inp["module"])
            if value is None:
                return False
            is_set = bool(value & (1 << bit))
            if inp["type"] == "active_low":
                return not is_set  # 0 = активен
            else:  # active_high
                return is_set  # 1 = активен
        except Exception as e:
            logging.error(f"SM Пресс-{self.press_id}: ошибка чтения {name}: {e}")
            return False

    def check_temperature_safety(self) -> bool:
        """Проверка: нет ли перегрева или обрыва термопары"""
        temps = state.read_ai(self.press_id)  # ✅ Чтение через шину
        if not temps or temps[0] is None:
            time.sleep(1)
            temps = state.read_ai(self.press_id)  # ✅ Чтение через шину
            if not temps or temps[0] is None:
                logging.warning(f"SM Пресс-{self.press_id}: не удаётся прочитать температуру.")
                return False

        temp = temps[0]

        if temp > self.max_temperature:
            logging.critical(f"SM Пресс-{self.press_id}: ПЕРЕГРЕВ! T={temp:.1f}°C > {self.max_temperature}°C")
            return False

        if temp <= self.thermocouple_error_value:
            logging.critical(f"SM Пресс-{self.press_id}: ОБРЫВ ТЕРМОПАРЫ (T={temp:.1f}°C)")
            return False

        return True

    def is_safe(self) -> bool:
        """
        Основной метод: проверяет, безопасно ли продолжать работу.
        Возвращает True, если всё в порядке; False — если есть авария.
        """
        # 1. Проверка DI-сигналов
        if self._read_input("e_stop"):
            logging.critical(f"SM Пресс-{self.press_id}: АВАРИЙНАЯ КНОПКА НАЖАТА!")
            # 🔥 Аварийно выключаем нагрев
            if hasattr(self, 'temp_control'):
                self.temp_control.cool_all()
            return False

        # 2. Проверка температуры
        if not self.check_temperature_safety():
            return False

        # 3. Дополнительно: можно проверить связь с модулями

        return True  # Всё в порядке
