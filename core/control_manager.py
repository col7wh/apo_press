# core/control_manager.py
import json
import logging
import os
import threading
import time
from logging.handlers import TimedRotatingFileHandler
from threading import Thread

from core.global_state import state
from core.press_controller import PressController
from core.pressure_controller import PressureController
from core.safety_monitor import SafetyMonitor
from core.temp_control import TemperatureController


class ControlManager(Thread):
    def __init__(self, press_id: int, config: dict):
        super().__init__(name=f"ControlManager-{press_id}", daemon=True)
        self.press_id = press_id
        self.config = config

        self._setup_control_logger()
        # Загрузка конфигурации
        try:
            common = self.config["common"]
            press_cfg = self.config["presses"][self.press_id - 1]

            self.di_module = common["di_module"]
            self.di_module_2 = common.get("di_module_2")
            self.lamp_do_module = common["do_module_2"]
            self.heating_do_module = press_cfg["modules"]["do"]

            self.btn_config = press_cfg.get("control_inputs", {})
            # Объединяем status_outputs и valves в lamp_config
            self.lamp_config = press_cfg.get("status_outputs", {}).copy()

            # Добавляем клапаны как часть lamp_config
            if "valves" in press_cfg:
                for name, cfg in press_cfg["valves"].items():
                    self.lamp_config[name] = {
                        "module": cfg["module"],
                        "bit": cfg["bit"]
                    }
        except Exception as e:
            self.logger.critical(f"CM Пресс-{self.press_id + 1} Ошибка загрузки конфигурации: {e}")
            raise

        self.running = True
        self.press_controller = None
        self.safety = SafetyMonitor(press_id)
        # 🔥 СОХРАНЯЕМ в state для общего доступа
        if not hasattr(state, 'safety_monitors'):
            state.safety_monitors = {}
        state.safety_monitors[press_id] = self.safety

        self._last_di_state = {}  # { (module, bit): True/False }
        self.open_time = 30

        # Желаемое состояние
        self.desired = {
            "lamp_run": False,
            "lamp_pause": False,
            "lamp_preheat": False,
            "lamp_auto_heat": False,
            "heater": False,
            "lift_up": False,
            "lift_down": False,
            "open": False,
            "close": False
        }

        # Принудительное выключение при старте
        self._ensure_all_off()
        self.logger.info(f"CM Пресс-{self.press_id + 1} ControlManager инициализирован. Все выходы выключены.")

        # Инициализация контроллеров
        self.press_controller = None  # Будет создан при старте
        self.pressure_controller = PressureController(press_id)
        self.temp_controller = TemperatureController(press_id)
        self.temp_controller.start()  # 🔥 Запускаем поток
        self._cur_start_press = None
        self._start_press_time = None  # Время начала нажатия
        self.load_name()

    def _setup_control_logger(self):
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

    def _on_start_confirmed(self):
        if self.press_controller and self.press_controller.running:
            self.logger.info(f"CM Пресс-{self.press_id + 1}: start_btn подтверждён, но программа уже запущена")
            return

        try:
            self.press_controller = PressController(pr_id=self.press_id, config=self.config)
            self.press_controller.start()
            # self.logger.info(f"CM Пресс-{self.press_id + 1}: программа запущена (удержание >3с)")
        except Exception as e:
            self.logger.error(f"CM Пресс-{self.press_id + 1}: ошибка запуска программы: {e}")

    def on_start_pressed(self):
        self.logger.info(f"CM Пресс-{self.press_id + 1}: программа запущена из консоли")
        self._on_start_confirmed()

    def stop_cycle(self):
        # Мягкая остановка
        self._force_open_mold(self.open_time)
        self.clean_stop()

    def clean_stop(self):
        # Мягкая остановка
        state.set(f"press_{self.press_id}_valve_lift_up", False)
        state.set(f"press_{self.press_id}_target_temp", None)
        if self.press_controller and self.press_controller.running:
            self.press_controller.stop()
            self.press_controller.join(timeout=1.0)

    def _on_stop_pressed(self):
        """
        Останавливает программу ТОЛЬКО если она была поставлена на паузу.
        Защита от аварийной остановки без подготовки.
        """
        if not (self.press_controller and self.press_controller.running):
            self.logger.info(f"CM Пресс-{self.press_id + 1}: не запущен")
            state.set(f"press_{self.press_id}_target_temp", None)
            return

        if not self.press_controller.paused:
            self.logger.warning(f"CM Пресс-{self.press_id + 1}: стоп запрещён — сначала нажмите 'Пауза'")
            return

        if self.press_controller and self.press_controller.running:
            # open
            self.stop_cycle()
            self.logger.info(f"CM Пресс-{self.press_id + 1}: останов по кнопке")
        else:
            self.logger.info(f"CM Пресс-{self.press_id + 1}: не запущен")

    def _force_open_mold(self, duration: float):
        """
        Асинхронно открывает форму: опускает пресс на заданное время.
        """

        def open_task():
            try:
                # Включаем клапан
                state.set(f"press_{self.press_id}_valve_lift_down", True)
                self.logger.info(f"CM Пресс-{self.press_id + 1}: клапан 'опустить' включён")

                # Ждём указанное время
                time.sleep(duration)

                # Выключаем
                state.set(f"press_{self.press_id}_valve_lift_down", False)
                self.logger.info(f"CM Пресс-{self.press_id + 1}: клапан 'опустить' выключен (авто-остановка)")

            except Exception as e:
                self.logger.error(f"CM Пресс-{self.press_id + 1}: ошибка в _force_open_mold: {e}")

        # Запускаем в фоновом потоке
        thread = threading.Thread(target=open_task, name=f"ForceOpen-{self.press_id}", daemon=True)
        thread.start()

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
        self.logger.info(f"CM Пресс-{self.press_id + 1} ControlManager запущен")
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
                    self.pressure_controller.stop_all()

                state.get(f"press_{self.press_id}_step_running_pressure", False)
                time.sleep(0.1)
            except Exception as e:
                self.logger.error(f"Ошибка в цикле: {e}", exc_info=True)
                time.sleep(1)

    def _update_desired_state(self):
        self.desired = {
            "lamp_run": False,
            "lamp_pause": False,
            "lamp_preheat": False,
            "lamp_auto_heat": False,
            "heater": False,
            "lift_up": False,
            "lift_down": False,
            "open": False,
            "close": False
        }

        current = state.get(f"press_{self.press_id}_pressure", 0.0)

        if not self.safety.is_safe():
            self.desired["lamp_error"] = True
            return

        if self.press_controller and self.press_controller.running:
            self.desired["lamp_run"] = True
            if self.press_controller.paused:
                self.desired["lamp_pause"] = True

        if self._is_preheat_active() and \
                ((self.press_controller and not self.press_controller.running) or not self.press_controller):
            self.desired["lamp_preheat"] = True

        if self._is_preheat_active() and self.press_controller and self.press_controller.running:
            self.desired["lamp_auto_heat"] = True

        if current > 1 and self.press_controller and self.press_controller.running:
            self.desired["lamp_pressure"] = True

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

            state.set_do_command(module_id, low, high, urgent=True)
            state.set_do_command(module_id, low, high, urgent=True)

            # Логирование
            action = "ON" if target_bit else "OFF"
            self.logger.debug(f"CM Пресс-{self.press_id + 1}: DO-{module_id} bit {bit} ({name}) → {action}")

    def _poll_buttons(self):
        di_value = state.get(f"di_module_{self.di_module}")
        if di_value is not None:
            self._handle_buttons(di_value)

        if self.di_module_2:
            di2_value = state.get(f"di_module_{self.di_module_2}")
            if di2_value is not None:
                self._handle_safety(di2_value)

    def _handle_buttons(self, value: int):
        """Обработка кнопок по фронту и спаду"""
        for name, cfg in self.btn_config.items():
            try:
                module = cfg["module"]
                bit = cfg["bit"]
                btn_type = cfg.get("type", "active_high")

                if module != str(self.di_module):
                    continue

                bit_set = bool(value & (1 << bit))
                current = not bit_set if btn_type == "active_low" else bit_set
                key = (module, bit)
                previous = self._last_di_state.get(key, None)
                self._last_di_state[key] = current

                if previous is None:
                    continue

                # Фронт: 0 → 1
                if not previous and current:
                    if name == "start_btn":
                        self._start_press_time = time.time()  # Начало удержания
                    else:
                        self._on_button_pressed(name)

                # Спад: 1 → 0
                elif previous and not current:
                    if name == "start_btn":
                        self._check_long_press()
            except Exception as e:
                self.logger.error(f"CM Ошибка обработки кнопки {name}: {e}")

    def _check_long_press(self):
        if self._start_press_time is None:
            return

        elapsed = time.time() - self._start_press_time
        self._start_press_time = None  # Сброс

        if elapsed >= 3.0:
            self.logger.info(f"CM Пресс-{self.press_id + 1}: программа запущена (удержание >3с)")
            self._on_start_confirmed()

    def _on_button_pressed(self, name: str):
        """Единая точка обработки нажатий"""
        if name == "start_btn":
            self.on_start_pressed()
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
        # print(f"CM press {self.press_id + 1} off modules {modules}")
        # urgent = state.get("urgent_do", {})
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
            self.logger.info(f"CM Пресс-{self.press_id + 1}: ручной прогрев до {target_temp}°C")

        except Exception as e:
            self.logger.error(f"CM Пресс-{self.press_id + 1}: ошибка запуска ручного прогрева: {e}")

    def _on_limit_switch_reached(self):
        # завершить шаг "lift_to_limit"
        state.set(f"press_{self.press_id}_limit_reached", True)
        self.logger.debug(f"CM Пресс-{self.press_id + 1}: достигнут лимит")

    def stop(self):
        self.temp_controller.stop()
        self.temp_controller.join(timeout=1.0)
        self.running = False
        self.pressure_controller.stop()
        self.logger.info(f"CM Пресс-{self.press_id + 1}  ControlManager остановлен")

    def emergency_stop(self):
        state.set(f"press_{self.press_id}_valve_lift_down", False)
        self._ensure_all_off()
        self.clean_stop()
        # self.stop()
        self.logger.warning(f"CM Пресс-{self.press_id + 1} Аварийная остановка")

    def load_name(self):
        with open(f"programs/press{self.press_id}.json", "r", encoding="utf-8") as f:
            program = json.load(f)
        press_prog = program.get("pressure_program", [])
        index = 0
        # нужно перебором искать шаг открытия и дернуть из него время
        # потом закинуть его в  форсе опен
        while index < len(press_prog):
            step = press_prog[index]
            step_type = step.get("step")
            if step_type == "open_mold":
                self.open_time = step.get("hold_time", 30)
                index += 1
            else:
                index += 1

        state.set(f"press_{self.press_id}_p_name", program.get("name", ""))
