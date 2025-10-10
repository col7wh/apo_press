# gui.py
import logging
import tkinter as tk
from tkinter import scrolledtext, messagebox
import threading
import time
import json
import os
from core.global_state import state


class SimpleGUI:
    CONFIG_FILE = "gui_config.json"
    DEFAULT_SIZE = "800x600"

    def __init__(self, start_press_func, stop_press_func, emergency_stop_func):
        self.start_press = start_press_func
        self.stop_press = stop_press_func
        self.emergency_stop = emergency_stop_func
        self.root = tk.Tk()
        self.root.title("Управление прессами")
        self.root.geometry("800x600")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.running = True

        # Чтение конфига
        if os.path.exists(self.CONFIG_FILE):
            try:
                with open(self.CONFIG_FILE, "r") as f:
                    cfg = json.load(f)
                    geom = cfg.get("geometry", self.DEFAULT_SIZE)
            except:
                geom = self.DEFAULT_SIZE
        else:
            geom = self.DEFAULT_SIZE

        self.root.geometry(geom)

        # Логи
        self.log_text = scrolledtext.ScrolledText(self.root, wrap=tk.WORD, height=15)
        self.log_text.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)

        # После создания log_text
        self.log_handler = TextHandler(self.log_text)
        self.log_handler.setFormatter(
            logging.Formatter('%(asctime)s %(levelname)s: %(message)s', datefmt='%H:%M:%S')
        )
        logging.getLogger().addHandler(self.log_handler)

        # Статус
        self.status_frame = tk.LabelFrame(self.root, text="Статус прессов", padx=10, pady=10)
        self.status_frame.pack(padx=10, pady=5, fill=tk.BOTH)

        self.status_labels = {}
        for pid in [1, 2, 3]:
            label = tk.Label(self.status_frame, text=f"Пресс-{pid + 1}: ОСТАНОВЛЕН", font=("Courier", 10))
            label.grid(row=pid, column=0, sticky="w", pady=2)
            self.status_labels[pid] = label

        # Кнопки
        self.btn_frame = tk.Frame(self.root)
        self.btn_frame.pack(pady=10)

        tk.Button(self.btn_frame, text="Запустить Пресс 2", command=lambda: self.send_command(1)).grid(row=0, column=0,
                                                                                                       padx=5)
        tk.Button(self.btn_frame, text="Запустить Пресс 3", command=lambda: self.send_command(2)).grid(row=0, column=1,
                                                                                                       padx=5)
        tk.Button(self.btn_frame, text="Запустить Пресс 4", command=lambda: self.send_command(3)).grid(row=0, column=2,
                                                                                                       padx=5)

        tk.Button(self.btn_frame, text="Остановить Пресс 2", command=lambda: self.stop_command(1)).grid(row=1, column=0,
                                                                                                        padx=5, pady=2)
        tk.Button(self.btn_frame, text="Остановить Пресс 3", command=lambda: self.stop_command(2)).grid(row=1, column=1,
                                                                                                        padx=5, pady=2)
        tk.Button(self.btn_frame, text="Остановить Пресс 4", command=lambda: self.stop_command(3)).grid(row=1, column=2,
                                                                                                        padx=5, pady=2)

        tk.Button(self.btn_frame, text="Аварийная остановка всех", command=self.emergency_stop,
                  bg="red", fg="white").grid(row=2, column=0, columnspan=3, pady=10)

        # Обновление интерфейса
        self.root.after(1000, self.update_status)

    def log(self, message):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)

    def send_command(self, press_id):
        self.start_press(press_id)

    def stop_command(self, press_id):
        self.stop_press(press_id)

    def emergency_stop(self):
        if messagebox.askyesno("Подтверждение", "Точно аварийно остановить все прессы?"):
            self.emergency_stop()

    def update_status(self):
        if not self.running:
            return

        for pid in [1, 2, 3]:
            running = state.get(f"press_{pid}_running", False)
            paused = state.get(f"press_{pid}_paused", False)
            completed = state.get(f"press_{pid}_completed", False)

            if running:
                status = "ПАУЗА" if paused else "РАБОТАЕТ"
            elif completed:
                status = "ЗАВЕРШЁН"
            else:
                status = "ОСТАНОВЛЕН"

            temp_step = state.get(f"press_{pid}_current_step_temperature", {})
            press_step = state.get(f"press_{pid}_current_step_pressure", {})
            current_step = max(temp_step.get("index", -1), press_step.get("index", -1)) + 1

            step_str = f" | Шаг {current_step}" if current_step > 0 else ""
            self.status_labels[pid].config(text=f"Пресс-{pid + 1}: {status}{step_str}")

        self.root.after(1000, self.update_status)

    def on_closing(self):
        if messagebox.askokcancel("Выход", "Закрыть приложение?"):
            self.log_handler.stop()  # Останавливаем опрос очереди
            # Сохраняем размер и позицию
            cfg = {
                "geometry": self.root.geometry()
            }
            try:
                with open(self.CONFIG_FILE, "w") as f:
                    json.dump(cfg, f)
            except Exception as e:
                print(f"Не удалось сохранить конфиг GUI: {e}")
            self.running = False
            self.root.quit()

    def run(self):
        self.root.mainloop()


import queue  # <-- Добавь в начало файла

class TextHandler(logging.Handler):
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
        self.queue = queue.Queue()
        self.text_widget.after(100, self.poll)  # Каждые 100мс проверяем очередь

    def emit(self, record):
        msg = self.format(record)
        try:
            self.queue.put_nowait(msg)
        except queue.Full:
            pass  # Очередь переполнена — пропускаем сообщение

    def poll(self):
        """Вызывается из главного потока Tk"""
        while True:
            try:
                msg = self.queue.get_nowait()
            except queue.Empty:
                break
            else:
                self.text_widget.configure(state='normal')
                self.text_widget.insert(tk.END, msg + '\n')
                self.text_widget.see(tk.END)
                self.text_widget.configure(state='disabled')
        # Запланировать следующую проверку
        if getattr(self, 'polling', True):
            self.text_widget.after(100, self.poll)

    def stop(self):
        self.polling = False
