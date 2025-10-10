# simulator.py
import serial
import time
import json
import os
import threading
import queue

# Очередь для команд от клавиатуры
key_queue = queue.Queue()

try:
    with open('config/config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
except FileNotFoundError:
    print("❌ config.json не найден")
    exit(1)

COM_PORT = "COM7"
BAUDRATE = config["baudrate"]
DATA_DIR = config.get("data_dir", "data")

# Загружаем hardware_config.json
try:
    with open('config/hardware_config.json', 'r', encoding='utf-8') as f:
        hw_config = json.load(f)
except FileNotFoundError:
    print("❌ hardware_config.json не найден")
    exit(1)

# Эмуляция устройств
devices = {
    11: {"model": "I-7017", "ai": [2.412, 2.712, 1.247, 0.224, 0.287, 0.246, 0.209, 0.178]},
    17: {"model": "I-7018", "ai": [20.5, 20.2, 20.4, 18.1, 21.2, 22.0, 22.1, 27.9]},
    18: {"model": "I-7018", "ai": [19.8, 19.6, 19.8, 20.2, 17.7, 21.8, 19.4, 29.8]},
    19: {"model": "I-7018", "ai": [20.1, 19.9, 20.1, 20.4, 20.9, 21.6, 21.7, 27.5]},
    31: {"model": "I-7045", "do": "0000", "di": "0111"},
    32: {"model": "I-7045", "do": "0000", "di": "000C"},
    33: {"model": "I-7045", "do": "0000", "di": "0008"},
    34: {"model": "I-7045", "do": "0000", "di": "0003"},
    37: {"model": "I-7051", "di": "0111"},
    38: {"model": "I-7051", "di": "0007"},
    39: {"model": "I-7051", "di": "0280"}
}

do_bit_effects = {
    (31, 0): {"target": (11, 0), "delta": +0.1},  # lift_up
    (31, 1): {"target": (11, 0), "delta": -0.1},  # lift_down
}

last_update = time.time()


def keyboard_listener():
    """Поток для чтения клавиш"""
    print("\n" + "="*60)
    print("       🖥️  СИМУЛЯТОР DCON — РЕЖИМ ОТЛАДКИ")
    print("       Нажмите клавишу для имитации сигнала")
    print("="*60)
    print("  1, 2, 3     — Старт Пресс-1/2/3")
    print("  S           — Стоп (все прессы)")
    print("  P           — Пауза (все прессы)")
    print("  E           — Аварийный останов (E-Stop)")
    print("  D           — Дверь (открыта/закрыта)")
    print("  C           — Форма (закрыта/открыта)")
    print("  jkL        — Лимит подъёма")
    print("  tyu        — Ручн. Прогрев")
    print("  ?           — Показать меню")
    print("  Q           — Выход")
    print("="*60 + "\n")

    while True:
        try:
            if os.name == 'nt':  # Windows
                import msvcrt
                if msvcrt.kbhit():
                    key = msvcrt.getch().decode('utf-8', errors='ignore').lower()
                    key_queue.put(key)
            else:  # Unix
                import sys, select
                if select.select([sys.stdin], [], [], 0) == ([sys.stdin], [], []):
                    key = sys.stdin.read(1).lower()
                    key_queue.put(key)
        except Exception as e:
            print(f"[KEY] Ошибка ввода: {e}")
        time.sleep(0.05)


def handle_client(ser: serial.Serial):
    print(f"[SIMULATOR] Готов к подключению на {ser.port}", flush=True)

    global last_update
    while True:
        try:
            buffer = ""
            start_time = time.time()
            while time.time() - start_time < 0.5 and ser.in_waiting > 0:
                char = ser.read(1).decode('ascii', errors='ignore')
                if char in '\r':
                    break
                buffer += char
            line = buffer.strip()

            if not line:
                continue

            # === 1. $01M — модель ===
            if len(line) == 4 and line.startswith("$") and line[3] == "M":
                try:
                    addr = int(line[1:3])
                except ValueError:
                    continue
                if addr not in devices:
                    continue
                model_code = {
                    "I-7017": "7017",
                    "I-7018": "7018",
                    "I-7045": "7045",
                    "I-7051": "7051"
                }.get(devices[addr]["model"], "XXXX")
                response = f"!{line[1:3]}{model_code}\r"
                ser.write(response.encode())

            # === 2. #310001 — запись DO ===
            elif line.startswith("#") and len(line) == 7:
                try:
                    addr = int(line[1:3])
                    cmd = line[3:5]
                    data = line[5:7]
                except:
                    continue
                if addr not in devices or devices[addr]["model"] != "I-7045":
                    continue
                do_current = devices[addr]["do"]
                high_byte = do_current[0:2]
                low_byte = do_current[2:4]
                if cmd == "00":
                    new_do = high_byte + data.upper()
                    devices[addr]["do"] = new_do
                elif cmd == "0B":
                    new_do = data.upper() + low_byte
                    devices[addr]["do"] = new_do
                else:
                    continue
                devices[addr]['di'] = new_do
                ser.write(b">\r")

            # === 3. #31 — чтение DO/AI ===
            elif line.startswith("#") and len(line) != 7:
                try:
                    addr = int(line[1:3])
                except ValueError:
                    continue
                if addr not in devices:
                    continue
                device = devices[addr]
                response = ""
                if device["model"] == "I-7045":
                    response = f">\r"
                elif device["model"] in ("I-7017", "I-7018"):
                    response = ">" + "".join(f"{val:+07.3f}" for val in device["ai"])
                if response:
                    ser.write((response + "\r").encode())

            # === 4. @31 — чтение DI ===
            elif line.startswith("@") and len(line) == 3:
                try:
                    addr = int(line[1:3])
                except ValueError:
                    continue
                if addr not in devices:
                    continue
                device = devices[addr]
                if "di" in device:
                    response = f">{device['di'].upper()}\r"
                    ser.write(response.encode())

            # === 5. Эффекты от DO ===
            current_time = time.time()
            if current_time - last_update >= 0.5:
                for (do_addr, bit), effect in do_bit_effects.items():
                    do_value_hex = devices[do_addr].get("do", "0000")
                    do_value = int(do_value_hex, 16)
                    if do_value & (1 << bit):
                        target_ai_addr, channel = effect["target"]
                        delta = effect["delta"]
                        current = devices[target_ai_addr]["ai"][channel]
                        new_value = max(0.0, current + delta * 0.5)
                        devices[target_ai_addr]["ai"][channel] = round(new_value, 3)
                last_update = current_time

            # === 6. Обработка клавиш ===
            try:
                key = key_queue.get_nowait()
                if key == 'q':
                    print("[SIM] Завершение по запросу...")
                    break

                elif key in '123':
                    press_id = int(key)
                    module = int(hw_config["presses"][press_id-1]["control_inputs"]["start_btn"]["module"])
                    bit = hw_config["presses"][press_id-1]["control_inputs"]["start_btn"]["bit"]
                    current = int(devices[module].get("di", "0000"), 16)
                    current ^= (1 << bit)
                    devices[module]["di"] = f"{current:04X}"
                    print(f"✅ [SIM] Старт-{press_id}: {'нажата' if current & (1<<bit) else 'отпущена'} | DI-{module} = {current:04X}")

                elif key == 's':
                    for pid in [1,2,3]:
                        module = int(hw_config["presses"][pid-1]["control_inputs"]["stop_btn"]["module"])
                        bit = hw_config["presses"][pid-1]["control_inputs"]["stop_btn"]["bit"]
                        current = int(devices[module].get("di", "0000"), 16)
                        current ^= (1 << bit)
                        devices[module]["di"] = f"{current:04X}"
                        print(f"✅ [SIM] Стоп-{pid}: {'нажата' if current & (1<<bit) else 'отпущена'}")

                elif key == 'p':
                    for pid in [1,2,3]:
                        module = int(hw_config["presses"][pid-1]["control_inputs"]["pause_btn"]["module"])
                        bit = hw_config["presses"][pid-1]["control_inputs"]["pause_btn"]["bit"]
                        current = int(devices[module].get("di", "0000"), 16)
                        current ^= (1 << bit)
                        devices[module]["di"] = f"{current:04X}"
                        print(f"✅ [SIM] Пауза-{pid}: {'нажата' if current & (1<<bit) else 'отпущена'}")

                elif key == 'e':
                    module = int(hw_config["common"]["di_module_2"])
                    bit = hw_config["common"]["e_stop"]["bit"]
                    current = int(devices[module].get("di", "0000"), 16)
                    current ^= (1 << bit)
                    devices[module]["di"] = f"{current:04X}"
                    status = "активирован" if current & (1<<bit) else "деактивирован"
                    print(f"✅ [SIM] E-Stop: {status} | DI-{module} = {current:04X}")

                elif key == 'd':
                    module = int(hw_config["common"]["di_module_2"])
                    bit = hw_config["common"]["door_closed"]["bit"]
                    current = int(devices[module].get("di", "0000"), 16)
                    current ^= (1 << bit)
                    devices[module]["di"] = f"{current:04X}"
                    status = "открыта" if current & (1<<bit) else "закрыта"
                    print(f"✅ [SIM] Дверь: {status} | DI-{module} = {current:04X}")

                elif key == 'c':
                    module = int(hw_config["common"]["di_module_2"])
                    bit = hw_config["common"]["press_closed"]["bit"]
                    current = int(devices[module].get("di", "0000"), 16)
                    current ^= (1 << bit)
                    devices[module]["di"] = f"{current:04X}"
                    status = "форма закрыта" if current & (1<<bit) else "форма открыта"
                    print(f"✅ [SIM] Форма: {status} | DI-{module} = {current:04X}")

                elif key == 'j':
                    press_id = 1
                    module = int(hw_config["presses"][press_id - 1]["control_inputs"]["limit_switch"]["module"])
                    bit = hw_config["presses"][press_id - 1]["control_inputs"]["limit_switch"]["bit"]
                    current = int(devices[module].get("di", "0000"), 16)
                    current ^= (1 << bit)
                    devices[module]["di"] = f"{current:04X}"
                    status = "достигнут" if current & (1 << bit) else "не достигнут"
                    print(f"✅ [SIM] Лимит-{press_id}: {status} | DI-{module} = {current:04X}")

                elif key == 'k':
                    press_id = 2
                    module = int(hw_config["presses"][press_id - 1]["control_inputs"]["limit_switch"]["module"])
                    bit = hw_config["presses"][press_id - 1]["control_inputs"]["limit_switch"]["bit"]
                    current = int(devices[module].get("di", "0000"), 16)
                    current ^= (1 << bit)
                    devices[module]["di"] = f"{current:04X}"
                    status = "достигнут" if current & (1 << bit) else "не достигнут"
                    print(f"✅ [SIM] Лимит-{press_id}: {status} | DI-{module} = {current:04X}")

                elif key == 'l':
                    press_id = 3
                    module = int(hw_config["presses"][press_id - 1]["control_inputs"]["limit_switch"]["module"])
                    bit = hw_config["presses"][press_id - 1]["control_inputs"]["limit_switch"]["bit"]
                    current = int(devices[module].get("di", "0000"), 16)
                    current ^= (1 << bit)
                    devices[module]["di"] = f"{current:04X}"
                    status = "достигнут" if current & (1 << bit) else "не достигнут"
                    print(f"✅ [SIM] Лимит-{press_id}: {status} | DI-{module} = {current:04X}")

                elif key == 't':
                    press_id = 1
                    module = int(hw_config["presses"][press_id - 1]["control_inputs"]["preheat_btn"]["module"])
                    bit = hw_config["presses"][press_id - 1]["control_inputs"]["preheat_btn"]["bit"]
                    current = int(devices[module].get("di", "0000"), 16)
                    current ^= (1 << bit)
                    devices[module]["di"] = f"{current:04X}"
                    status = "нажата" if current & (1 << bit) else "отпущена"
                    print(f"✅ [SIM] Прогрев-{press_id}: {status} | DI-{module} = {current:04X}")

                elif key == 'y':
                    press_id = 2
                    module = int(hw_config["presses"][press_id - 1]["control_inputs"]["preheat_btn"]["module"])
                    bit = hw_config["presses"][press_id - 1]["control_inputs"]["preheat_btn"]["bit"]
                    current = int(devices[module].get("di", "0000"), 16)
                    current ^= (1 << bit)
                    devices[module]["di"] = f"{current:04X}"
                    status = "нажата" if current & (1 << bit) else "отпущена"
                    print(f"✅ [SIM] Прогрев-{press_id}: {status} | DI-{module} = {current:04X}")

                elif key == 'u':
                    press_id = 3
                    module = int(hw_config["presses"][press_id - 1]["control_inputs"]["preheat_btn"]["module"])
                    bit = hw_config["presses"][press_id - 1]["control_inputs"]["preheat_btn"]["bit"]
                    current = int(devices[module].get("di", "0000"), 16)
                    current ^= (1 << bit)
                    devices[module]["di"] = f"{current:04X}"
                    status = "нажата" if current & (1 << bit) else "отпущена"
                    print(f"✅ [SIM] Прогрев-{press_id}: {status} | DI-{module} = {current:04X}")

                elif key == '?':
                    print("\n" + "="*50)
                    print("       📋 СПРАВКА ПО УПРАВЛЕНИЮ")
                    print("="*50)
                    print("  1,2,3 — Старт Пресс-1/2/3")
                    print("  S     — Стоп (все)")
                    print("  P     — Пауза (все)")
                    print("  E     — E-Stop")
                    print("  D     — Дверь")
                    print("  C     — Форма")
                    print("  L     — Лимит")
                    print("  ?     — Справка")
                    print("  Q     — Выход")
                    print("="*50 + "\n")

            except queue.Empty:
                pass

            time.sleep(0.01)

        except Exception as e:
            print(f"[SIMULATOR] ОШИБКА: {e}", flush=True)
            break


def start_simulator():
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        ser = serial.Serial(COM_PORT, BAUDRATE, timeout=1)

        # Запускаем поток для клавиш
        kb_thread = threading.Thread(target=keyboard_listener, daemon=True)
        kb_thread.start()

        print(f"[SIMULATOR] Запущен на {ser.port}")
        handle_client(ser)
    except Exception as e:
        print(f"[SIMULATOR] Не удалось открыть {COM_PORT}: {e}")


if __name__ == "__main__":
    start_simulator()