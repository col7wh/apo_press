# core/web_interface.py
"""
Легковесный веб-интерфейс с обновлением в реальном времени через SSE.
"""
import os
import threading
import logging
import json
from datetime import datetime
from flask import Flask, render_template, Response
from core.global_state import state
import time
from core.hardware_interface import HardwareInterface


class WebInterface(threading.Thread):
    def __init__(self, host="0.0.0.0", port=5000):
        super().__init__(name="WebInterface", daemon=True)
        self.host = host
        self.port = port
        self.app = Flask(__name__)
        self.setup_routes()
        self.hw_config = None
        self.app = Flask(__name__, template_folder="templates")  # Укажи путь к шаблонам

    def setup_routes(self):
        @self.app.route("/")
        def index():
            return render_template("dashboard.html")

        @self.app.route("/stream")
        def stream():
            def generate():
                while True:
                    data = {
                        "timestamp": self._get_timestamp(),
                        "presses": {},
                        "di": {
                            "common1": f"{state.get('di_module_37', 0):04X}",
                            "bits1": f"{state.get('di_module_37', 0):016b}",
                            "common2": f"{state.get('di_module_38', 0):04X}",
                            "bits2": f"{state.get('di_module_38', 0):016b}"
                        }
                    }

                    for pid in range(1, 4):
                        temps = state.get(f"press_{pid}_temps", [None] * 8)
                        # 🔧 Получаем правильный DO-модуль
                        do_module = self._get_do_module_for_press(pid)
                        if do_module:
                            do_state = state.get(f"do_state_{do_module}", 0)
                        else:
                            do_state = 0

                        data["presses"][str(pid)] = {
                            "temps": [t for t in temps[:7]],
                            "do": f"{do_state:04X}",
                            "do_": f"{do_state:016b}",
                            "running": state.get(f"press_{pid}_running", False),
                            "pressure": state.get(f"press_{pid}_pressure", None)
                        }
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                    time.sleep(1.0)  # Обновление каждую секунду

            return Response(generate(), mimetype="text/event-stream")

        @self.app.route("/api")
        def api():
            return {
                "presses": {
                    pid: {
                        "temps": state.get(f"press_{pid}_temps", [None] * 8)[:4],
                        "running": state.get(f"press_{pid}_running", False)
                    } for pid in range(1, 4)
                },
                "di_common1": state.get("di_common", 0),
                "timestamp": self._get_timestamp()
            }

        @self.app.route("/debug")
        def debug():
            all_data = state.get_all()
            return "<pre>" + json.dumps(all_data, indent=2, ensure_ascii=False) + "</pre>"

    def _get_timestamp(self):
        return datetime.now().strftime("%H:%M:%S")

    def _get_do_module_for_press(self, press_id: int) -> str:
        """Получить DO-модуль для пресса из config"""
        try:
            return self.hw_config["presses"][press_id - 1]["modules"]["do"]
        except (IndexError, KeyError):
            return None

    def run(self):
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)
        logging.info(f"WI Веб-интерфейс запущен на http://{self.host}:{self.port}")

        config_path = os.path.join("config", "hardware_config.json")
        with open(config_path, "r", encoding="utf-8") as f:
            self.hw_config = json.load(f)

        try:
            self.app.run(host=self.host, port=self.port, threaded=True, use_reloader=False)
        except Exception as e:
            logging.error(f"WI Ошибка веб-сервера: {e}")
