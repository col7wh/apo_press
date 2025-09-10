# core/control_manager.py
import json
import logging
import time
import os
import traceback
from logging.handlers import TimedRotatingFileHandler
from threading import Thread
from core.global_state import state
from core.safety_monitor import SafetyMonitor
from core.press_controller import PressController
from core.temp_control import TemperatureController
from core.pressure_controller import PressureController

os.makedirs("logs", exist_ok=True)


# В ControlManager.__init__


class ControlManager(Thread):
    def __init__(self, press_id: int, config: dict):
        super().__init__(name=f"ControlManager-{press_id}", daemon=True)
        self.press_id = press_id
        self.config = config

        #self.logger = logging.getLogger(f"CM  ControlManager-{press_id}")
        #self.logger.setLevel(logging.INFO)
        #if not self.logger.handlers:
            #handler = logging.FileHandler(f"logs/control_{press_id}.log", encoding="utf-8")
            #formatter = logging.Formatter('%(asctime)s [CTRL-%(name)s] %(levelname)s: %(message)s')
            #handler.setFormatter(formatter)
            #self.logger.addHandler(handler)
        self.setup_control_logger()
        # Загрузка конфигурации
        try:
            common = self.config["common"]
            press_cfg = self.config["presses"][self.press_id - 1]

            self.di_module = common["di_module"]
            self.di_module_2 = common.get("di_module_2")
            self.lamp_do_module = common["do_module_2"]
            self.heating_do_module = press_cfg["modules"]["do"]

            self.btn_config = press_cfg.get("control_inputs", {})
            # self.lamp_config = press_cfg.get("status_outputs", {})
            # Объединяем status_outputs и valves в lamp_config
            self.lamp_config = press_cfg.get("status_outputs", {}).copy()

            # Добавляем клапаны как часть lamp_config
            if "valves" in press_cfg:
                for name, cfg in press_cfg["valves"].items():
                    self.lamp_config[name] = {
                        "module": cfg["module"],
                        "bit": cfg["bit"]
                        # Предполагаем active_high, можно добавить type
                    }
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

        self._last_di_state = {}  # { (module, bit): True/False }
        self.press_controller = None  # Будет создан при старте
        press_controller = PressController(press_id, config)
        self.pressure_controller = PressureController(press_id)
        # Желаемое состояние
        self.desired = {
            "lamp_run": False,
            "lamp_pause": False,
            "lamp_preheat": False,
            "heater": False,
            "lift_up": False,
            "lift_down": False,
            "open": False,
            "close": False
        }

        # Принудительное выключение при старте
        self._ensure_all_off()
        # state.set(f"press_{press_id}_target_pressure", 0.0)  # Например
        # state.set(f"press_{self.press_id}_target_temp", 20.0)
        self.logger.info(f"CM Пресс-{self.press_id} ControlManager инициализирован. Все выходы выключены.")

        # Инициализация контроллеров
        self.temp_controller = TemperatureController(press_id)
        self.temp_controller.start()  # 🔥 Запускаем поток

    def setup_control_logger(self):
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        log_file = f"{log_dir}/control_{self.press_id}.log"

        handler = TimedRotatingFileHandler(
            log_file,
            when="midnight",
            interval=1,
            backupCount=7,
            encoding="utf-8"
        )
        handler.suffix = "%Y-%m-%d"
        formatter = logging.Formatter('%(asctime)s [CTRL-%(name)s] %(levelname)s: %(message)s')
        handler.setFormatter(formatter)

        self.logger = logging.getLogger(f"CM_ControlManager-{self.press_id}")
        self.logger.setLevel(logging.INFO)

        if not self.logger.handlers:
            self.logger.addHandler(handler)

    def _on_start_pressed(self):
        if self.press_controller and self.press_controller.running:
            self.logger.info(f"CM Пресс-{self.press_id}: start_btn нажата, но программа уже запущена")
            return

        # Запускаем PressController
        try:
            self.press_controller = PressController(press_id=self.press_id, config=self.config)
            self.press_controller.start()
            self.logger.info(f"CM Пресс-{self.press_id}: программа запущена по кнопке")
        except Exception as e:
            self.logger.error(f"CM Пресс-{self.press_id}: ошибка запуска программы: {e}")

    def _on_stop_pressed(self):
        # Устанавливаем уставку
        state.set(f"press_{self.press_id}_target_temp", None)

        if self.press_controller and self.press_controller.running:
            self.press_controller.stop()
            self.logger.info(f"CM Пресс-{self.press_id}: останов по кнопке")
        else:
            self.logger.info(f"CM Пресс-{self.press_id}: не запущен")

    def _on_pause_pressed(self):
        if not (self.press_controller and self.press_controller.running):
            return

        if self.press_controller.paused:
            self.press_controller.resume()
            self.logger.info(f"CM Пресс-{self.press_id}: возобновление после паузы")
        else:
            self.press_controller.pause()
            self.logger.info(f"CM Пресс-{self.press_id}: поставлен на паузу")

    def run(self):
        self.logger.info(f"CM Пресс-{self.press_id} ControlManager запущен")
        while self.running:
            try:
                self._update_desired_state()
                self._synchronize_outputs()
                self._poll_buttons()

                # 🔥 Обновляем регулятор давления
                target_pressure = state.get(f"press_{self.press_id}_target_pressure", 0.0)
                if target_pressure > 0:
                    self.pressure_controller.set_target_pressure(target_pressure)
                    self.pressure_controller.update()
                else:
                    self.pressure_controller._stop_all()

                time.sleep(0.1)

            except Exception as e:
                self.logger.error(f"Ошибка в цикле: {e}", exc_info=True)
                time.sleep(1)

    def _update_desired_state(self):
        self.desired = {
            "lamp_run": False,
            "lamp_pause": False,
            "lamp_preheat": False,
            "heater": False,
            "lift_up": False,
            "lift_down": False,
            "open": False,
            "close": False
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

        if state.get(f"press_{self.press_id}_valve_lift_up"):
            self.desired["lift_up"] = True

        if state.get(f"press_{self.press_id}_valve_lift_down"):
            self.desired["lift_down"] = True

        if state.get(f"press_{self.press_id}_valve_open"):
            self.desired["open"] = True

        if state.get(f"press_{self.press_id}_valve_close"):
            self.desired["close"] = True

    def _synchronize_outputs(self):
        """Теперь без групповой записи"""
        if not self.safety.is_safe():
            self._write_lamp_bit("lamp_error", True)
            return
        else:
            self._write_lamp_bit("lamp_error", False)

        # Лампы и клапаны
        for name in ["lamp_run", "lamp_pause", "lamp_preheat",
                     "lamp_auto_heat", "lamp_pressure",
                     "lift_up", "lift_down", "open", "close"]:
            if name in self.lamp_config:
                self._write_lamp_bit(name, self.desired.get(name, False))


        # Пишем только нужные биты
        #self._write_lamp_bit("lamp_run", self.desired.get("lamp_run", False))
        #self._write_lamp_bit("lamp_pause", self.desired.get("lamp_pause", False))
        #self._write_lamp_bit("lamp_preheat", self.desired.get("lamp_preheat", False))
        #self._write_lamp_bit("lamp_auto_heat", self.desired.get("lamp_auto_heat", False))
        #self._write_lamp_bit("lamp_pressure", self.desired.get("lamp_pressure", False))


        # 3. Доп. клапаны (если нужно — раскомментировать)
        # valve_state = self._get_valve_state()
        # self._ensure_do_state("31", valve_state)

    def set_valve(self, valve_name: str, on: bool):
        self.logger.warning(f"CM set_valve  ???")
        if valve_name in self.desired:
            self.desired[valve_name] = on
        else:
            self.logger.warning(f"CM Нет клапана в desired: {valve_name}")

    def _get_lamp_state(self) -> int:
        self.logger.warning(f"CM _get_lamp_state  ???")
        state_val = 0
        # print(self.lamp_config.items())
        for name, cfg in self.lamp_config.items():
            bit = cfg.get("bit")
            if bit is None:
                continue
            if self.desired.get(name, False):
                state_val |= (1 << bit)
        return state_val

    def _write_lamp_bit(self, name: str, on: bool):
        """
        Устанавливает состояние лампы по имени из config.
        Не затрагивает другие биты на модуле.
        """
        if name not in self.lamp_config:
            return

        cfg = self.lamp_config[name]
        module_id = cfg["module"]
        bit = cfg["bit"]
        active_high = cfg.get("type", "active_high") == "active_high"

        # Формируем маску
        mask = 1 << bit

        # Читаем текущее состояние модуля
        current = state.read_digital(module_id) or 0

        # Вычисляем желаемое состояние бита
        if active_high:
            target_bit = on
        else:
            target_bit = not on

        # Обновляем только свой бит
        if target_bit:
            new_state = current | mask
        else:
            new_state = current & ~mask

        # Только если изменилось — отправляем
        if current != new_state:
            low = new_state & 0xFF
            high = (new_state >> 8) & 0xFF
            """" Трассировка
            if module_id == "31":
                stack = traceback.extract_stack()
                filename, line, func, text = stack[-2]
                print(
                    f"🔢 SET_DO: DO-{module_id} {low:02X} {high:02X}  press_{self.press_id}| Вызвано из {func} ({filename}:{line})")
                print(self.desired)
            """
            state.set_do_command(module_id, low, high, urgent=True)
            state.set_do_command(module_id, low, high, urgent=True)

            # Логирование
            action = "ON" if target_bit else "OFF"
            self.logger.debug(f"CM Пресс-{self.press_id}: DO-{module_id} bit {bit} ({name}) → {action}")

    def _ensure_do_state(self, module_id: str, desired: int):
        """Если текущее состояние не совпадает с желаемым — отправить команду"""
        current = state.get(f"do_state_{module_id}")
        # print(current )
        if current != desired:
            low = desired & 0xFF
            high = (desired >> 8) & 0xFF
            urgent = state.get("urgent_do", {})
            urgent[module_id] = (low, high)
            # state.set("urgent_do", urgent)
            state.set_do_command(module_id, low, high, urgent=True)
            # print(f"CM Синхронизация DO-{module_id}: {current or 0:04X} → {desired:04X}")

    def _poll_buttons(self):
        di_value = state.get(f"di_module_{self.di_module}")
        if di_value is not None:
            self._handle_buttons(di_value)

        if self.di_module_2:
            di2_value = state.get(f"di_module_{self.di_module_2}")
            if di2_value is not None:
                self._handle_safety(di2_value)

    def _handle_buttons_(self, value: int):
        """Обработка кнопок на DI-модуле (например, 37)"""
        for name, cfg in self.btn_config.items():
            try:
                module = cfg["module"]
                bit = cfg["bit"]
                btn_type = cfg.get("type", "active_high")

                # Проверяем, что это наш модуль
                if module != str(self.di_module):
                    continue

                # Читаем бит
                bit_set = bool(value & (1 << bit))

                # Учитываем тип сигнала
                if btn_type == "active_low":
                    button_pressed = not bit_set
                else:  # active_high
                    button_pressed = bit_set

                # Реагируем
                if name == "start_btn" and button_pressed:
                    self._on_start_pressed()

                elif name == "stop_btn" and button_pressed:
                    self._on_stop_pressed()

                elif name == "pause_btn" and button_pressed:
                    self._on_pause_pressed()

                elif name == "preheat_btn" and button_pressed:
                    self._on_preheat_pressed()

                elif name == "limit_switch" and bit_set:
                    self._on_limit_switch_reached()

            except Exception as e:
                self.logger.error(f"CM Ошибка обработки кнопки {name}: {e}")

    def _handle_buttons(self, value: int):
        """Обработка кнопок по фронту"""
        for name, cfg in self.btn_config.items():
            try:
                module = cfg["module"]
                bit = cfg["bit"]
                btn_type = cfg.get("type", "active_high")

                if module != str(self.di_module):
                    continue

                # Читаем текущее состояние бита
                bit_set = bool(value & (1 << bit))

                # Учитываем тип сигнала
                if btn_type == "active_low":
                    current = not bit_set
                else:
                    current = bit_set

                # Получаем предыдущее состояние
                key = (module, bit)
                previous = self._last_di_state.get(key, None)

                # Сохраняем текущее состояние
                self._last_di_state[key] = current

                # Пропускаем, если это первый опрос
                if previous is None:
                    continue

                # Срабатывание по фронту: с 0 → 1
                if not previous and current:
                    self._on_button_pressed(name)

            except Exception as e:
                self.logger.error(f"CM Ошибка обработки кнопки {name}: {e}")

    def _on_button_pressed(self, name: str):
        """Единая точка обработки нажатий"""
        if name == "start_btn":
            self._on_start_pressed()
        elif name == "stop_btn":
            self._on_stop_pressed()
        elif name == "pause_btn":
            self._on_pause_pressed()
        elif name == "preheat_btn":
            self._on_preheat_pressed()
        elif name == "limit_switch":
            self._on_limit_switch_reached()
        else:
            self.logger.debug(f"CM Кнопка {name} нажата")

    def _handle_safety(self, value: int):
        # Передаётся в SafetyMonitor
        pass

    def _is_preheat_active(self) -> bool:
        target_temp = state.get(f"press_{self.press_id}_target_temp", None)

        return target_temp is not None

    def _ensure_all_off(self):
        modules = [self.lamp_do_module, self.heating_do_module, "31"]
        print(f"CM press {self.press_id} off modules {modules}")
        urgent = state.get("urgent_do", {})
        for mid in modules:
            state.set_do_command(mid, 0, 0, urgent=True)

    def _on_preheat_pressed(self):
        # Читаем уставку из первого шага программы
        program_path = f"programs/press{self.press_id}.json"
        try:
            with open(program_path, "r", encoding="utf-8") as f:
                program = json.load(f)
            first_step = program.get("temp_program", [{}])[0]
            target_temp = first_step.get("target_temp", 50.0)

            # Устанавливаем уставку
            state.set(f"press_{self.press_id}_target_temp", target_temp)
            self.logger.info(f"CM Пресс-{self.press_id}: ручной прогрев до {target_temp}°C")

        except Exception as e:
            self.logger.error(f"CM Пресс-{self.press_id}: ошибка запуска ручного прогрева: {e}")

    def _on_limit_switch_reached(self):
        # Можно использовать для синхронизации шагов
        # Например, завершить шаг "lift_to_limit"
        state.set(f"press_{self.press_id}_limit_reached", True)
        self.logger.debug(f"CM Пресс-{self.press_id}: достигнут лимит")

    def stop(self):
        self.temp_controller.stop()
        self.temp_controller.join(timeout=1.0)
        self.running = False
        self.pressure_controller.stop()
        self.logger.info(f"CM Пресс-{self.press_id}  ControlManager остановлен")

    def emergency_stop(self):
        self.stop()
        self.logger.warning(f"CM Пресс-{self.press_id} Аварийная остановка")
