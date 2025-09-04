# core/web_interface.py
"""
Веб-интерфейс с визуализацией:
- Лампы нагрева по зонам.
- Уставки.
- DI/DO как индикаторы.
Всё работает стабильно, без ошибок в JS.
"""
import os
import time
import threading
import logging
import json
from datetime import datetime
from flask import Flask, render_template, Response, request, redirect, url_for, jsonify
from core.global_state import state


class WebInterface(threading.Thread):
    def __init__(self, host="0.0.0.0", port=5000):
        super().__init__(name="WebInterface", daemon=True)
        self.host = host
        self.port = port

        config_path = os.path.join("config", "hardware_config.json")
        with open(config_path, "r", encoding="utf-8") as f:
            self.hw_config = json.load(f)

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        templates_dir = os.path.join(project_root, "templates")

        self.app = Flask(__name__, template_folder=templates_dir)
        self.setup_routes()

    def setup_routes(self):
        @self.app.route("/")
        def index():
            logging.info("WI Запрос / получен")
            try:
                return render_template("dashboard.html")
            except Exception as e:
                print(f"Ошибка рендеринга шаблона: {e}")
                return f"Ошибка шаблона: {e}", 500

                # Новый маршрут для редактирования программы

        @self.app.route("/edit_program", methods=["GET", "POST"])
        def edit_program():
            programs = []
            program_files = os.listdir('programs')

            # Чтение данных из файлов и добавление индекса
            for idx, file in enumerate(program_files):
                if file.endswith('.json'):
                    with open(os.path.join('programs', file), 'r', encoding='utf-8') as f:
                        program_data = json.load(f)
                        if isinstance(program_data, dict):  # Проверим, что это словарь
                            program_data['index'] = idx  # Добавляем индекс к данным
                        else:
                            print(f"Error: data in file {file} is not a dictionary.")
                        programs.append(program_data)

            #print(f"Programs loaded: {programs}")  # Печатаем, чтобы проверить данные

            if request.method == "POST":
                program_name = request.form.get('program_name')
                if not program_name:
                    return "Имя программы обязательно", 400

                program_data = {}
                for pid in [1, 2, 3]:
                    temp_steps = []
                    press_steps = []
                    for i in range(7):
                        temp_val = request.form.get(f"press{pid}_temp_program_{i}")
                        if temp_val:
                            try:
                                target = float(temp_val)
                                temp_steps.append(
                                    {"step": "ramp_temp", "target_temp": target, "ramp_time": 300, "hold_time": 300})
                            except:
                                pass

                        press_val = request.form.get(f"press{pid}_pressure_program_{i}")
                        if press_val:
                            try:
                                target = float(press_val)
                                press_steps.append({"step": "ramp_pressure", "target_pressure": target, "ramp_time": 60,
                                                    "hold_time": 180})
                            except:
                                pass

                    if temp_steps or press_steps:
                        program_data[f"press{pid}"] = {
                            "temp_program": temp_steps,
                            "pressure_program": press_steps
                        }

                with open(os.path.join('programs', f'{program_name}.json'), 'w', encoding="utf-8") as f:
                    json.dump(program_data, f, indent=4, ensure_ascii=False)

                return redirect(url_for('edit_program'))

            return render_template("edit_program.html", programs=programs)

        @self.app.route("/get_programs")
        def get_programs():
            programs = {}
            for pid in [1, 2, 3]:
                try:
                    with open(f"programs/press{pid}.json", "r", encoding="utf-8") as f:
                        programs[f"press{pid}"] = json.load(f)
                except FileNotFoundError:
                    programs[f"press{pid}"] = {"temp_program": [], "pressure_program": []}
            return jsonify(programs)

        @self.app.route("/save_programs", methods=["POST"])
        def save_programs():
            data = request.get_json()
            for pid in [1, 2, 3]:
                program = data.get(f"press{pid}")
                if program:
                    with open(f"programs/press{pid}.json", "w", encoding="utf-8") as f:
                        json.dump(program, f, ensure_ascii=False, indent=4)
            return "OK"

        @self.app.route("/save_press", methods=["POST"])
        def save_press():
            data = request.get_json()
            press_id = data.get("press_id")
            program = data.get("program")
            if press_id in [1, 2, 3]:
                with open(f"programs/press{press_id}.json", "w", encoding="utf-8") as f:
                    json.dump(program, f, ensure_ascii=False, indent=4)
                return "OK"
            return "Invalid press ID", 400

        @self.app.route("/stream")
        def stream():
            def generate():
                while True:
                    try:
                        data = {
                            "timestamp": self._get_timestamp(),
                            "presses": []
                        }

                        for pid in range(1, 4):
                            try:
                                press_cfg = self.hw_config["presses"][pid - 1]
                                ai_module = press_cfg["modules"]["ai"]
                                do_module = press_cfg["modules"]["do"]
                                heater_channels = press_cfg.get("heater_channels", list(range(8)))
                            except (IndexError, KeyError):
                                continue

                            temps = state.get(f"press_{pid}_temps", [None] * 8)
                            do_state = state.get(f"do_state_{do_module}", 0)

                            heating_bits = [bool(do_state & (1 << ch)) for ch in heater_channels[:7]]

                            try:
                                di_module = self.hw_config["common"]["di_module"]
                                di_value = state.get(f"di_module_{di_module}", 0)
                            except:
                                di_value = 0

                            inputs = []
                            if "control_inputs" in press_cfg:
                                for name, cfg in press_cfg["control_inputs"].items():
                                    bit = cfg.get("bit")
                                    if bit is not None:
                                        inputs.append({
                                            "name": name,
                                            "on": bool(di_value & (1 << bit))
                                        })

                            # 🔥 ИСПРАВЛЕНО: читаем lamp_state для каждого пресса по его config
                            outputs = []
                            if "status_outputs" in press_cfg:
                                for name, cfg in press_cfg["status_outputs"].items():
                                    module_id = cfg["module"]
                                    bit = cfg["bit"]
                                    active_high = cfg.get("type", "active_high") == "active_high"

                                    # Читаем состояние модуля
                                    mod_state = state.get(f"do_state_{module_id}", 0)
                                    bit_set = bool(mod_state & (1 << bit))

                                    # Учитываем тип
                                    is_on = bit_set if active_high else not bit_set

                                    outputs.append({
                                        "name": name,
                                        "on": is_on
                                    })

                            # 🔧 Чтение клапанов из hardware_config
                            valve_outputs = []
                            if "valves" in press_cfg:
                                for name, cfg in press_cfg["valves"].items():
                                    module_id = cfg["module"]
                                    bit = cfg["bit"]

                                    # Читаем состояние модуля
                                    mod_state = state.get(f"do_state_{module_id}", 0)
                                    bit_set = bool(mod_state & (1 << bit))

                                    # Активный уровень
                                    active_high = cfg.get("type", "active_high") == "active_high"
                                    is_on = bit_set if active_high else not bit_set

                                    # Человекочитаемое имя
                                    label_map = {
                                        "lift_up": "Подъём",
                                        "lift_down": "Опускание",
                                        "open": "Давление +",
                                        "close": "Давление –"
                                    }
                                    label = label_map.get(name, name)

                                    valve_outputs.append({
                                        "name": name,
                                        "label": label,
                                        "on": is_on
                                    })

                            temp_step = state.get(f"press_{pid}_current_step_temperature", {})
                            press_step = state.get(f"press_{pid}_current_step_pressure", {})

                            current_step = {
                                "index": temp_step.get("index", press_step.get("index", "-")),
                                "type": f"{temp_step.get('type', '—')} / {press_step.get('type', '—')}",
                                "target_temp": temp_step.get("target_temp", "—"),
                                "target_pressure": press_step.get("target_pressure", "—"),
                                "elapsed": int(time.time() - (temp_step.get("start_time") or press_step.get(
                                    "start_time") or time.time())),
                                "status": state.get(f"press_{pid}_step_status_temperature", "stopped")
                            }
                            data["presses"].append({
                                "id": pid,
                                "temps": [round(float(t), 1) if t not in (None, "N/A") else None for t in temps[:7]],
                                "heating_bits": heating_bits,
                                "running": state.get(f"press_{pid}_running", False),
                                "pressure": state.get(f"press_{pid}_pressure", None),
                                "temp_target": state.get(f"press_{pid}_target_temp", None),
                                "pressure_target": state.get(f"press_{pid}_target_pressure", None),
                                "inputs": inputs,
                                "outputs": outputs,
                                "valve_outputs": valve_outputs,  # ✅ Новый блок
                                "current_step": current_step
                            })

                        # 🔥 Правильный формат SSE
                        json_data = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
                        # Убедимся, что нет новых строк в JSON
                        json_data = json_data.replace('\n', ' ').replace('\r', ' ')
                        yield f"data: {json_data}\n\n"  # Обязательно двойной \n\n

                    except Exception as e:
                        print(f"SSE: Ошибка: {e}")
                        yield 'data: {"presses":[]}\n\n'
                        time.sleep(1)

                    time.sleep(1.0)

            return Response(generate(), mimetype="text/event-stream")

        @self.app.route("/graphs")
        def graphs():
            return render_template("graphs.html")

        @self.app.route("/pid_tune")
        def pid_tune():
            return render_template("pid_tune.html")

        @self.app.route("/get_pid_config")
        def get_pid_config():
            try:
                with open("config/pid_config.json", "r", encoding="utf-8") as f:
                    data = json.load(f)
                return jsonify(data)
            except Exception as e:
                logging.error(f"Ошибка загрузки pid_config.json: {e}")
                # Вернём дефолтную структуру
                return jsonify({
                    "presses": [
                        {
                            "zones": [{"Kp": 2.5, "Ki": 0.15, "Kd": 0.8, "offset": 0.0}] * 8,
                            "pressure_pid": {"Kp": 1.2, "Ki": 0.05, "Kd": 0.4}
                        },
                        {
                            "zones": [{"Kp": 2.5, "Ki": 0.15, "Kd": 0.8, "offset": 0.0}] * 8,
                            "pressure_pid": {"Kp": 1.2, "Ki": 0.05, "Kd": 0.4}
                        },
                        {
                            "zones": [{"Kp": 2.5, "Ki": 0.15, "Kd": 0.8, "offset": 0.0}] * 8,
                            "pressure_pid": {"Kp": 1.2, "Ki": 0.05, "Kd": 0.4}
                        }
                    ]
                })

        @self.app.route("/save_pid_config", methods=["POST"])
        def save_pid_config():
            data = request.get_json()
            # Преобразуем в структуру pid_config.json
            config = {"presses": []}
            for pid in [1, 2, 3]:
                zones = []
                for i in range(8):
                    zones.append({
                        "Kp": data.get(f"p{pid}_kp_{i}", 2.5),
                        "Ki": data.get(f"p{pid}_ki_{i}", 0.15),
                        "Kd": data.get(f"p{pid}_kd_{i}", 0.8),
                        "offset": data.get(f"p{pid}_offset_{i}", 0.0)
                    })
                config["presses"].append({
                    "id": pid,
                    "zones": zones,
                    "pressure_pid": {
                        "Kp": data.get(f"p{pid}_press_kp", 1.2),
                        "Ki": data.get(f"p{pid}_press_ki", 0.05),
                        "Kd": data.get(f"p{pid}_press_kd", 0.4)
                    }
                })
            with open("config/pid_config.json", "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            return "OK"

    def _get_timestamp(self):
        return datetime.now().strftime("%H:%M:%S")

    def run(self):
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)
        logging.info(f"WI Веб-интерфейс запущен на http://{self.host}:{self.port}")

        try:
            self.app.run(host=self.host, port=self.port, threaded=True, use_reloader=False)
        except Exception as e:
            logging.error(f"WI Ошибка веб-сервера: {e}")
