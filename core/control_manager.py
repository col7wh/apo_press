# core/control_manager.py
import logging
import time
import os
from threading import Thread
from core.global_state import state
from core.safety_monitor import SafetyMonitor
from core.press_controller import PressController
from core.temp_control import TemperatureController

os.makedirs("logs", exist_ok=True)


class ControlManager(Thread):
    def __init__(self, press_id: int, config: dict):
        super().__init__(name=f"ControlManager-{press_id}", daemon=True)
        self.press_id = press_id
        self.config = config

        self.logger = logging.getLogger(f"CM  ControlManager-{press_id}")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.FileHandler(f"logs/control_{press_id}.log", encoding="utf-8")
            formatter = logging.Formatter('%(asctime)s [CTRL-%(name)s] %(levelname)s: %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

        # Загрузка конфигурации
        try:
            common = self.config["common"]
            press_cfg = self.config["presses"][self.press_id - 1]

            self.di_module = common["di_module"]
            self.di_module_2 = common.get("di_module_2")
            self.lamp_do_module = common["do_module_2"]
            self.heating_do_module = press_cfg["modules"]["do"]

            self.btn_config = press_cfg.get("control_inputs", {})
            self.lamp_config = press_cfg.get("status_outputs", {})
        except Exception as e:
            self.logger.critical(f"CM Пресс-{self.press_id} Ошибка загрузки конфигурации: {e}")
            raise

        self.running = True
        self.press_controller = None
        self.safety = SafetyMonitor(press_id)
        # 🔥 СОХРАНЯЕМ в state для общего доступа
        if not hasattr(state, 'safety_monitors'):
            state.safety_monitors = {}
        state.safety_monitors[press_id] = self.safety

        press_controller = PressController(press_id, config)
        # Желаемое состояние
        self.desired = {
            "lamp_run": False,
            "lamp_pause": False,
            "lamp_preheat": False,
            "heater": False
        }

        # Принудительное выключение при старте
        self._ensure_all_off()
        #state.set(f"press_{press_id}_target_pressure", 0.0)  # Например
        #state.set(f"press_{self.press_id}_target_temp", 20.0)
        self.logger.info(f"CM Пресс-{self.press_id} ControlManager инициализирован. Все выходы выключены.")

        # Инициализация контроллеров
        self.temp_controller = TemperatureController(press_id)
        self.temp_controller.start()  # 🔥 Запускаем поток

    def run(self):
        self.logger.info(f"CM Пресс-{self.press_id} ControlManager запущен")
        while self.running:
            try:
                self._update_desired_state()
                self._synchronize_outputs()
                self._poll_buttons()
                time.sleep(0.1)
            except Exception as e:
                self.logger.error(f"Ошибка в цикле: {e}", exc_info=True)
                time.sleep(1)


    def _update_desired_state(self):
        self.desired = {
            "lamp_run": False,
            "lamp_pause": False,
            "lamp_preheat": False,
            "heater": False
        }

        if not self.safety.is_safe():
            self.desired["lamp_error"] = True
            return

        if self.press_controller and self.press_controller.running:
            self.desired["lamp_run"] = True
            if self.press_controller.paused:
                self.desired["lamp_pause"] = True

        if self._is_preheat_active():
            self.desired["heater"] = True
            self.desired["lamp_preheat"] = True

    def _synchronize_outputs(self):
        """Синхронизация всех DO-модулей"""
        # 1. Лампы (модуль 32)
        lamp_state = self._get_lamp_state()
        self._ensure_do_state(self.lamp_do_module, lamp_state)

        # 2. Нагрев (модуль 34, 35, 36)
        #heater_state = self._get_heater_state()
        #self._ensure_do_state(self.heating_do_module, heater_state)

        # 3. Доп. клапаны (если нужно — раскомментировать)
        # valve_state = self._get_valve_state()
        # self._ensure_do_state("31", valve_state)

    def _get_lamp_state(self) -> int:
        state_val = 0
        #print(self.lamp_config.items())
        for name, cfg in self.lamp_config.items():
            bit = cfg.get("bit")
            if bit is None:
                continue
            if self.desired.get(name, False):
                state_val |= (1 << bit)
        return state_val

    def _get_heater_state(self) -> int:
        # Здесь может быть логика по зонам
        return 0x0001 if self.desired["heater"] else 0x0000

    def _ensure_do_state(self, module_id: str, desired: int):
        """Если текущее состояние не совпадает с желаемым — отправить команду"""
        current = state.get(f"do_state_{module_id}")
        #print(current )
        if current != desired:
            low = desired & 0xFF
            high = (desired >> 8) & 0xFF
            urgent = state.get("urgent_do", {})
            urgent[module_id] = (low, high)
            #state.set("urgent_do", urgent)
            state.set_do_command(module_id, low, high, urgent=True)
            #print(f"CM Синхронизация DO-{module_id}: {current or 0:04X} → {desired:04X}")

    def _poll_buttons(self):
        di_value = state.get(f"di_module_{self.di_module}")
        if di_value is not None:
            self._handle_buttons(di_value)

        if self.di_module_2:
            di2_value = state.get(f"di_module_{self.di_module_2}")
            if di2_value is not None:
                self._handle_safety(di2_value)

    def _handle_buttons(self, value: int):
        # Логика опроса кнопок
        pass

    def _handle_safety(self, value: int):
        # Передаётся в SafetyMonitor
        pass

    def _is_preheat_active(self) -> bool:
        return False

    def _ensure_all_off(self):

        modules = [self.lamp_do_module, self.heating_do_module, "31"]
        print(f"CM press {self.press_id} off modules {modules}")
        urgent = state.get("urgent_do", {})
        for mid in modules:
            state.set_do_command(mid, 0, 0, urgent=True)


    def stop(self):
        self._ensure_all_off()
        self.temp_controller.stop()
        self.temp_controller.join(timeout=1.0)
        self.running = False
        self.logger.info(f"CM Пресс-{self.press_id}  ControlManager остановлен")

    def emergency_stop(self):
        self.stop()
        self.logger.warning(f"CM Пресс-{self.press_id} Аварийная остановка")

