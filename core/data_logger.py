# core/data_logger.py
import csv
import logging
import os
import time
from datetime import datetime
from threading import Thread, Lock
from core.global_state import state
from core.plot_thermal_data import ThermalProfilePlotter


class DataLogger:
    def __init__(self):
        self.log_dir = "data"
        os.makedirs(self.log_dir, exist_ok=True)
        self.file = None
        self.writer = None
        self.running = False
        self.thread = None
        self.lock = Lock()
        self.press_id = None
        self.file_path = None

    def start(self, press_id: int):
        with self.lock:
            if self.running:
                self.stop()

            self.press_id = press_id
            self.running = True

            # Создаём файл
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"press{press_id}_{timestamp}.csv"
            filepath = os.path.join(self.log_dir, filename)
            self.file_path = filepath

            self.file = open(filepath, "w", newline="", encoding="utf-8")
            self.writer = csv.writer(self.file)

            # Заголовки
            headers = [
                "timestamp", "step_index", "step_type",
                "temp1", "temp2", "temp3", "temp4", "temp5", "temp6", "temp7",
                "pressure", "target_temp", "target_pressure",
                "lamp_run", "lamp_pause", "lamp_preheat"
            ]
            self.writer.writerow(headers)
            self.file.flush()

            # Запускаем поток
            self.thread = Thread(target=self._log_loop, daemon=True)
            self.thread.start()
            logging.info(f"DL Пресс-{press_id+ 1}: логирование запущено → {filename}")

    def _log_loop(self):
        last_write = time.time()
        while self.running:
            try:
                now = time.time()
                if now - last_write >= 5.0:  # Каждую секунду
                    self._write_row()
                    last_write = now
                time.sleep(0.1)
            except Exception as e:
                logging.info(f"DL Ошибка в логгере: {e}")
                break

    def _write_row(self):
        if not self.writer:
            return

        try:
            # Чтение данных
            step = state.get(f"press_{self.press_id}_current_step_temperature", {})
            index = step.get("index", "")
            step_type = step.get("type", "")

            temps = state.get(f"press_{self.press_id}_temps", [None] * 8)[:7]
            pressure = state.get(f"press_{self.press_id}_pressure", None)
            target_temp = state.get(f"press_{self.press_id}_target_temp", None)
            target_pressure = state.get(f"press_{self.press_id}_target_pressure", None)

            # Состояние ламп
            lamp_do = "32" if self.press_id == 1 else "31"
            do_state = state.get(f"do_state_{lamp_do}", 0)
            lamp_run = bool(do_state & (1 << 3))
            lamp_pause = bool(do_state & (1 << 2))
            lamp_preheat = bool(do_state & (1 << 4))

            # Формат времени
            timestamp = datetime.now().strftime("%H:%M:%S")

            row = [
                timestamp, index, step_type,
                *(f"{t:.1f}" if t is not None else "" for t in temps),
                f"{pressure:.1f}" if pressure is not None else "",
                f"{target_temp:.1f}" if target_temp is not None else "",
                f"{target_pressure:.1f}" if target_pressure is not None else "",
                lamp_run, lamp_pause, lamp_preheat
            ]

            with self.lock:
                if self.writer:
                    self.writer.writerow(row)
                    self.file.flush()

        except Exception as e:
            logging.info(f"DL Ошибка записи строки: {e}")

    def stop(self):
        with self.lock:
            self.running = False
            if self.file:
                self.file.close()
                self.file = None
                logging.info(f"DL Пресс-{self.press_id+ 1}: логирование остановлено")

    def is_running(self):
        with self.lock:
            return self.running

    def plot_view(self):
        plotter = ThermalProfilePlotter(
            show_plot=True,
            save=True,
            ylim_temp=(0, 250)
        )

        result = plotter.plot(self.file_path)
        if result['status'] == 'OK':
            logging.info(f"DL Пресс-{self.press_id+ 1}: Успех {result['message']}")
        else:
            logging.info(f"DL Пресс-{self.press_id+ 1}: Ошибка {result['message']}")

    # .logger.plot_view()