# core/graph_transmitter.py
"""
Модуль передачи данных на графический ПК.
Работает как отдельный поток, отвечает на '*' пакетом из 66 байт.
"""
import serial
import time
import threading
import logging
import json
from typing import Dict, Any
from core.data_logger import DataLogger
from core.global_state import state

logger = logging.getLogger(__name__)


class GraphTransmitter(threading.Thread):
    """
    Передатчик данных для графического ПК.
    - Работает в отдельном потоке
    - Отвечает на команду '*' пакетом из 66 байт
    - Использует посимвольную отправку для совместимости
    """

    def __init__(self, port: str = "COM5", baudrate: int = 1200, enabled: bool = True):
        super().__init__(daemon=True, name="GraphTransmitter")
        self.port = port
        self.baudrate = baudrate
        self.logger = DataLogger()
        self.enabled = enabled
        self.stop_event = threading.Event()
        self.ser: serial.Serial | None = None

    def load_config(self) -> None:
        """Загружает настройки из system.json"""
        try:
            with open('config/system.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.port = config.get('graph_port', self.port)
            self.baudrate = config.get('graph_baudrate', self.baudrate)
            self.enabled = config.get('graph_enabled', self.enabled)
            logging.info(f"[GRAPH] Конфиг загружен: {self.port} @ {self.baudrate}")
        except Exception as e:
            logging.warning(f"[GRAPH] Не удалось загрузить config/system.json: {e}")

    def start(self) -> None:
        """Переопределённый start() для предварительной настройки"""
        if not self.enabled:
            logging.info("[GRAPH] Отключён в конфигурации")
            return
        self.load_config()
        try:
            self.serial_open()
            super().start()  # Запускаем run()
        except Exception as e:
            logging.error(f"[GRAPH] Не удалось открыть {self.port}: {e}")

    def run(self) -> None:
        """Основной цикл потока"""
        if not self.enabled or self.ser is None:
            return

        while not self.stop_event.is_set():
            try:
                if self.ser.in_waiting > 0:
                    data = self.ser.read(self.ser.in_waiting)
                    if b'*' in data:
                        # logging.info("[GRAPH]  Получено: '*'")
                        self.send_packet()
                time.sleep(0.1)
            except Exception as e:
                logging.error(f"[GRAPH] ️ Ошибка в цикле: {e}")
                if self.ser and self.ser.is_open:
                    self.ser.close()
                time.sleep(1)
                self.serial_open()

        if self.ser and self.ser.is_open:
            self.ser.close()
        logging.info("[GRAPH] Передатчик остановлен")

    def serial_open(self):
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=1,
                write_timeout=2
            )
            time.sleep(1)  # Дать порту стабилизироваться
            logging.info(f"[GRAPH] Подключено к {self.port} @ {self.baudrate}")

        except Exception as e:
            logging.error(f"[GRAPH] Не удалось открыть {self.port}: {e}")

    def send_packet(self) -> None:
        """Формирует и отправляет 66-байтный пакет"""
        packet = [0] * 66

        # # Пресс 1
        # packet[0] = int(state.get('press_1_pressure', 0) * 2 + 0.5)
        # temps1 = state.get('press_1_temps', [0] * 8)[:7]
        # for i in range(7):
        #     if temps1[i] < 0:
        #         packet[1 + i] = 0
        #     else:
        #         packet[1 + i] =int(temps1[i] + 0.5)

        # Пресс 2
        packet[8] = int(state.get('press_1_pressure', 0) * 20 + 0.5)
        temps1 = state.get('press_1_temps', [0] * 8)[:7]
        for i in range(7):
            if temps1[i] < 0:
                packet[9 + i] = 0
            else:
                packet[9 + i] = int(temps1[i] + 0.5)
            # packet[9 + i] = int(temps2[i] + 0.5)

        # Пресс 3
        packet[16] = int(state.get('press_2_pressure', 0) * 20 + 0.5)
        temps2 = state.get('press_2_temps', [0] * 8)[:7]
        for i in range(7):
            if temps2[i] < 0:
                packet[17 + i] = 0
            else:
                packet[17 + i] = int(temps2[i] + 0.5)
            # packet[17 + i] = int(temps3[i] + 0.5)

        # Пресс 4
        packet[24] = int(state.get('press_3_pressure', 0) * 20 + 0.5)
        temps3 = state.get('press_3_temps', [0] * 8)[:7]
        for i in range(7):
            if temps3[i] < 0:
                packet[25 + i] = 0
            else:
                packet[25 + i] = int(temps3[i] + 0.5)
            # packet[17 + i] = int(temps3[i] + 0.5)

        # Уставки температуры (tTarget)
        targets = []

        for i in range(3):
            val = state.get(f'press_{i+1}_target_temp', 0)
            if val is not None:
                targets.append(val)
            else:
                targets.append(0)

        packet[49] = int(targets[0] + 0.5)
        packet[50] = int(targets[1] + 0.5)
        packet[51] = int(targets[2] + 0.5)

        # Уставка давления (pTarget) — общая
        packet[54] = int(50.0 * 2 + 0.5)  # 50.0 бар ×2

        # Время выполнения (в минутах)
        current_min1 = int(state.get('press_1_cycle_elapsed', 0) // 60) % 256
        current_min2 = int(state.get('press_2_cycle_elapsed', 0) // 60) % 256
        current_min3 = int(state.get('press_3_cycle_elapsed', 0) // 60) % 256
        packet[61] = current_min1
        packet[62] = current_min2
        packet[63] = current_min3

        # Логирование HEX
        hex_string = ''.join(f'{b:02X}' for b in packet)
        logging.debug(f"[GRAPH] HEX: {hex_string}")

        for i in range(63):
            if packet[i] < 0:
                packet[i] = 0

        # Посимвольная отправка
        try:
            for b in packet:
                self.ser.write(bytes([b]))
                time.sleep(0.001)
            # logging.info("[GRAPH]  Пакет отправлен")
        except Exception as e:
            logging.error(f"[GRAPH]  Ошибка отправки: {packet}")
            logging.error(f"[GRAPH]  Ошибка отправки: {e}")

    def stop(self) -> None:
        """Остановка потока"""
        self.stop_event.set()
