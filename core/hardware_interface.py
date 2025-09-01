# core/hardware_interface.py

import json
import time
import logging
import os
from typing import Optional, List, Dict, Any, Union
from core.global_state import state  # ✅ Добавлен импорт
import threading

# Будем использовать pyserial в реальном режиме
try:
    import serial
except ImportError:
    serial = None

# Создаём отдельный логгер для hardware_interface
hardware_logger = logging.getLogger('HardwareInterface')
hardware_logger.setLevel(logging.INFO)

# Проверяем, нет ли уже обработчиков (чтобы избежать дублирования)
if not hardware_logger.handlers:
    # Создаём папку, если её нет
    import os
    os.makedirs("logs", exist_ok=True)
    handler = logging.FileHandler('logs/hardware.log', encoding='utf-8')
    formatter = logging.Formatter('%(asctime)s [HI] %(levelname)s: %(message)s')
    handler.setFormatter(formatter)
    hardware_logger.addHandler(handler)

# 🔥 Ключевая строка: отключаем передачу наверх
hardware_logger.propagate = False


def _simulate_response(command: str) -> str:
    """Простая имитация ответа"""
    if command.startswith("$"):
        return "!317017"
    elif command.startswith("#") and "00" not in command and "0B" not in command:
        return "+0000+0015+0023+0040+0055"
    elif command.startswith("@"):
        return ">1234"
    return "OK"


def _is_urgent_module(module_id: str) -> bool:
    """Определяет, срочный ли модуль (клапаны, лампы)"""
    # Пример: DO-модули 37, 38, 39 — срочные; 34 — нагрев
    urgent_modules = {"37", "38", "39", "33", "32"}
    return module_id in urgent_modules


def _is_valid_response(command: str, response: str) -> bool:
    if not response:
        return False

    if command.startswith("#") and len(command) == 3:  # AI
        return '+' in response and len(response) > 10
    elif command.startswith("@"):  # DI
        return '>' in response and any(c in '0123456789ABCDEF' for c in response.split('>')[-1])
    elif command.startswith("$"):  # Ping
        return '!' in response
    elif command.startswith("#") and ">" in response:
        return True  # ← Разрешаем пустой ответ
    return True


class HardwareInterface:
    """
    Унифицированный интерфейс для работы с DCON-устройствами.
    Поддерживает режимы: real (COM-порт) и simulation (simulator.py).
    Используется:
    - HardwareDaemon — для прямого чтения/записи.
    - diagnose.py — для диагностики.
    - Остальные модули должны использовать global_state.
    """

    def __init__(self, config_path: str = "config/system.json", direct_mode: bool = False):
        self.config_path = config_path
        self.direct_mode = direct_mode  # ✅ Новый флаг
        self.config = self._load_config()
        self.mode = self.config.get("mode", "simulation")  # real / simulation
        self.baudrate = self.config.get("baudrate", 9600)
        self.timeout = self.config.get("timeout", 1.0)
        self.lock = threading.RLock()  # 🔥 Добавлено
        hardware_logger.info(f"HI Lock создан: {id(self.lock)}")
        self.stats = {
            "total_commands": 0,
            "good_responses": 0,
            "bad_responses": 0,
            "mid_responses": 0,
            "ai_responses": 0,
            "di_responses": 0,
            "do_responses": 0,
            "commands_by_module": {},
            "last_reset": time.time()
        }

        # Определяем корень проекта
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(project_root, "config", "hardware_config.json")

        with open(config_path, "r", encoding="utf-8") as f:
            self.hw_config = json.load(f)

        # Инициализация интерфейса
        self._initialize_interface()

    def _load_config(self) -> Dict[str, Any]:
        """Загрузка system.json"""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            hardware_logger.error(f"HI Не удалось загрузить конфиг: {e}")
            raise

    def _initialize_interface(self):
        """Инициализация COM или симулятора"""
        if self.mode == "real":
            port = self.config.get("com_port", "COM1")
            if serial is None:
                raise ImportError("HI pyserial не установлен. Установите: pip install pyserial")
            try:
                self.serial = serial.Serial(
                    port=port,
                    baudrate=self.baudrate,
                    timeout=self.timeout,
                    bytesize=8,
                    stopbits=1,
                    parity='N'
                )
                hardware_logger.info(f"HI COM-порт {port} открыт.")
            except Exception as e:
                hardware_logger.error(f"HI Ошибка открытия COM-порта: {e}")
                raise
        else:
            hardware_logger.info("HI Режим симуляции активирован.")
            self.serial = None

    def _send_command(self, command: str) -> Optional[str]:
        """Отправка команды и получение ответа"""
        # Извлекаем ID модуля из команды
        if command.startswith(("$", "#", "@")) and len(command) >= 3:
            module_id = command[1:3]
            self.stats["commands_by_module"][module_id] = self.stats["commands_by_module"].get(module_id, 0) + 1

        try:
            if self.mode == "real":
                # time.sleep(0.2)
                if self.serial.in_waiting:
                    self.serial.reset_input_buffer()
                    time.sleep(0.1)

                # Отправка
                self.serial.write((command + "\r\n").encode())

                # ⏸️ Пауза после AI-запроса
                if command.startswith("#") and len(command) == 3:
                    time.sleep(0.05)  # Небольшая пауза, чтобы начать чтение

                # 🔁 Ручное чтение с таймаутом
                raw = b''
                start_time = time.time()
                while (time.time() - start_time) < 0.3:  # Макс 500 мс
                    if self.serial.in_waiting:
                        byte = self.serial.read(1)
                        raw += byte
                        # Условие окончания: \n или переполнение
                        if byte == b'\n' or len(raw) > 100:
                            break
                    else:
                        time.sleep(0.01)  # Не грузим CPU

                response = raw.decode('utf-8', errors='ignore').strip()
                #if (len(response) > 5 and len(response) != 57) or len(response) <= 4:
                    #hardware_logger.info(f"HI DCON: {command} -> {response}")
                    #return None
                if command.startswith("#") and len(command) >= 4:
                    self.stats["do_responses"] += 0.5
                    #print(f"HI DCON: {command} -> {response}")

                # Анализ ответа
                is_good = _is_valid_response(command, response)

                if is_good:
                    self.stats["good_responses"] += 1
                else:
                    self.stats["bad_responses"] += 1
                    hardware_logger.info(f"HI DCON: {command} -> {response}")

                    if self.serial.in_waiting:
                        self.serial.reset_input_buffer()
                    time.sleep(0.05)  # Даём шине "передохнуть"

                return response if is_good else None

            else:
                # logging.info(f"HI SIM: {command}")
                return _simulate_response(command)
        except Exception as e:
            hardware_logger.error(f"HI Ошибка при отправке команды '{command}': {e}")
            # time.sleep(2)
            return None

    def read_ai(self, module_id: str) -> List[str]:
        """
        Читает все 8 значений с AI-модуля.
        Возвращает список строк: ['0020.8', '0020.5', ...]
        """
        command = f"#{module_id}"
        response = self._send_command(command)
        self.stats["ai_responses"] += 1
        # hardware_logger.info(f"HI DCON: {command} -> {response}")

        if not response:
            return None

        # ✅ Улучшенный парсинг с очисткой
        clean = response.strip().lstrip('>').strip()
        if not clean.startswith('+'):
            return None

        values = []
        for part in clean.split('+'):
            cleaned = ''.join(c for c in part.strip() if c.isdigit() or c == '.')
            if cleaned and cleaned.count('.') <= 1:
                values.append(cleaned)
        return values if len(values) >= 8 else None

    def read_digital(self, module_id: Union[str, int]) -> Optional[int]:
        """Чтение DI/DO: возвращает 16-битное значение"""
        try:
            mid = f"{int(module_id):02d}"
            command = f"@{mid}"
            response = self._send_command(command)
            #if int(module_id) ==34:
                #print(f"HI2 DCON: {command} -> {response}")
            self.stats["di_responses"] += 1
            if response and response.startswith('>'):
                hex_str = response[1:].strip()
                return int(hex_str, 16)
            return None
        except Exception as e:
            hardware_logger.error(f"HI RD Ошибка чтения DI/DO с модуля {module_id}: {e}")
            self.stats["mid_responses"] += 1
            time.sleep(0.2)
            mid = f"{int(module_id):02d}"
            command = f"@{mid}"
            response = self._send_command(command)
            # hardware_logger.info(f"HI2 DCON: {command} -> {response}")

            if response and response.startswith('>'):
                hex_str = response[1:].strip()
                fix = int(hex_str, 16)
                hardware_logger.error(f"HI RD fix")
                return fix
            hardware_logger.error(f"HI RD 2 -Ошибка чтения DI/DO с модуля {module_id}: {e}")
            return None

    def write_do(self, module_id: Union[str, int], byte_low: int = 0, byte_high: int = 0):
        """
        Запись DO: ставит команду в очередь global_state.
        Отправка будет выполнена HardwareDaemon.
        """
        mid = f"{int(module_id):02d}"
        cmd_low = f"#{mid}00{byte_low:02X}"
        cmd_high = f"#{mid}0B{byte_high:02X}"
        #print(f"HI write_do вызван: module={module_id}, low={byte_low:02X}, high={byte_high:02X}")
        hardware_logger.info(f"HI write_do вызван: module={module_id}, low={byte_low:02X}, high={byte_high:02X}")
        if self.direct_mode:
            # ✅ Прямая отправка — как в старом режиме
            self._send_command(cmd_low)
            time.sleep(0.1)
            self._send_command(cmd_high)
            #self.stats["do_responses"] += 1
            # hardware_logger.info(f"DO: модуль {mid}, low=0x{byte_low:02X}, high=0x{byte_high:02X} (прямая отправка)")
        else:
            #self.stats["do_responses"] += 1
            # Определяем приоритет
            if _is_urgent_module(mid):
                urgent_queue = state.get("urgent_do", {})
                urgent_queue[mid] = (byte_low, byte_high)
                state.set("urgent_do", urgent_queue)
                hardware_logger.info(f"HI DO: модуль {mid}, low=0x{byte_low:02X}, high=0x{byte_high:02X} (в очередь: срочно)")
            else:
                heating_queue = state.get("heating_do", {})
                heating_queue[mid] = (byte_low, byte_high)
                state.set("heating_do", heating_queue)
                hardware_logger.info(f"HI DO: модуль {mid}, low=0x{byte_low:02X}, high=0x{byte_high:02X} (в очередь: нагрев)")

    def write_do_bit(self, module_id: Union[str, int], channel: int, on: bool):
        """
        Включить/выключить один канал (бит) на DO-модуле.
        Сначала читает текущее состояние, затем изменяет один бит.
        """
        if not (0 <= channel <= 15):
            hardware_logger.error(f"HI Некорректный канал: {channel}. Должен быть 0–15")
            return False

        try:
            with self.lock:  # 🔒 Блокировка шины
                # Читаем текущее состояние
                current = self.read_digital(module_id)
                if current is None:
                    #hardware_logger.error(f"HI WDB Не удалось прочитать состояние DO-{module_id}")
                    current = self.read_digital(module_id)
                    if current is None:
                        hardware_logger.error(f"HI WDB 2- Не удалось прочитать состояние DO-{module_id}")
                        time.sleep(0.2)
                        # time.sleep(5)
                        return False

                # Формируем новое состояние
                mask = 1 << channel
                new_state = current | mask if on else current & ~mask

                # Разделяем на low и high байты
                low_byte = new_state & 0xFF
                high_byte = (new_state >> 8) & 0xFF

                # Записываем
                mid = f"{int(module_id):02d}"
                low = f"#{mid}00{low_byte:02X}"
                high = f"#{mid}0B{high_byte:02X}"
                self._send_command(f"#{mid}00{low:02X}")
                time.sleep(0.05)
                self._send_command(f"#{mid}0B{high:02X}")
                # self.write_do(module_id, low_byte, high_byte)
                self.stats["do_responses"] += 1
                # hardware_logger.info(f"HI DO-{module_id}.{channel} {'ВКЛ' if on else 'ВЫКЛ'} (состояние: {new_state:04X})")
            return True

        except Exception as e:
            hardware_logger.error(f"HI Ошибка управления каналом {channel} на DO-{module_id}: {e}")
            time.sleep(0.2)
            return False

    def log_quality_report(self):
        now = time.time()
        period = now - self.stats["last_reset"]
        total = self.stats["total_commands"]
        good = self.stats["good_responses"]
        bad = self.stats["bad_responses"]
        mid = self.stats["mid_responses"]
        ai = self.stats["ai_responses"]
        di = self.stats["di_responses"]
        do = self.stats["do_responses"]
        by_mod = self.stats["commands_by_module"]
        total = good + bad

        #print(period, total, good, bad)
        if total > 0:
            quality = (good / total) * 100
            speed = total/period
            hardware_logger.info("-------------------------------------------------------")
            hardware_logger.info(
                f" DCON Quality: {quality:.1f}% ({good}/{total}) "
                f"[Good: {good}, Bad: {bad}] over {period:.1f}s, speed {speed}com/s, AI {ai}, DI {di}, DO {do}, MID {mid}"
            )
            hardware_logger.info(f"{by_mod}")
        # 🔁 Обновляем state для веб-интерфейса
        state.set("dcon_stats", {
            "total": total,
            "good": good,
            "bad": bad,
            "quality": round(quality, 1) if total > 0 else 0.0,
            "speed": round(speed, 1),
            "by_module": self.stats["commands_by_module"].copy(),
            "ai": ai,
            "di": di,
            "do": do,
            "mid": mid,
            "period": round(period, 1)
        })


        # Сброс для следующего периода
        self.stats = {
            "total_commands": 0,
            "good_responses": 0,
            "bad_responses": 0,
            "mid_responses": 0,
            "ai_responses": 0,
            "di_responses": 0,
            "commands_by_module": {},
            "do_responses": 0,
            "last_reset": now
        }

    def close(self):
        """Закрытие соединения"""
        if self.mode == "real" and self.serial and self.serial.is_open:
            self.serial.close()
            hardware_logger.info("HI COM-порт закрыт.")


if __name__ == "__main__":
    # Для теста
    hw = HardwareInterface("config/system.json")
    print("AI-08:", hw.read_ai("17"))
    print("DI/DO 37:", hex(hw.read_digital(37)))
    hw.write_do(37, 0x01, 0x00)
    hw.close()
