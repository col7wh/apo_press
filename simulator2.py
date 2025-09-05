# simulator.py
import serial
import time
import json
import os


try:
    with open('config/config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
except FileNotFoundError:
    print("❌ config.json не найден")
    exit(1)

COM_PORT = "COM7"
BAUDRATE = config["baudrate"]
DATA_DIR = config.get("data_dir", "data")

# Эмуляция устройств: do и di как строки в HEX (как у тебя было)
devices = {
    # Аналоговые входы
    11: {"model": "I-7017", "ai": [2.412, 2.512, 0.247, 0.224, 0.287, 0.246, 0.209, 0.178]},
    17: {"model": "I-7018", "ai": [20.5, 20.2, 20.4, 18.1, 21.2, 22.0, 22.1, 27.9]},
    18: {"model": "I-7018", "ai": [19.8, 19.6, 19.8, 20.2, 17.7, 21.8, 19.4, 29.8]},
    19: {"model": "I-7018", "ai": [20.1, 19.9, 20.1, 20.4, 20.9, 21.6, 21.7, 27.5]},

    # Цифровые выходы и входы — как строки, как у тебя было
    31: {"model": "I-7045", "do": "0000", "di": "0111"},  # младший и старший байт
    32: {"model": "I-7045", "do": "0000", "di": "000C"},
    33: {"model": "I-7045", "do": "0000", "di": "0008"},
    34: {"model": "I-7045", "do": "0000", "di": "0003"},
    37: {"model": "I-7051", "di": "0111"},
    38: {"model": "I-7051", "di": "0007"},
    39: {"model": "I-7051", "di": "0280"}
}

# Эффекты: какие биты влияют на какие AI
# Например: если включён бит 0 на DO 31 → давление растёт
do_bit_effects = {
    (31, 0): {"target": (11, 0), "delta": +0.1},  # lift_up
    (31, 1): {"target": (11, 0), "delta": -0.1},  # lift_down
}

last_update = time.time()


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

            # print(f"[SIMULATOR] Получено: {repr(line)}", flush=True)

            # === 1. $01M — определение модели ===
            if len(line) == 4 and line.startswith("$") and line[3] == "M":
                try:
                    addr = int(line[1:3])
                except ValueError:
                    continue

                if addr not in devices:
                    print(f"[SIMULATOR] ❌ Устройство #{addr} не найдено", flush=True)
                    continue

                model_code = {
                    "I-7017": "7017",
                    "I-7018": "7018",
                    "I-7045": "7045",
                    "I-7051": "7051"
                }.get(devices[addr]["model"], "XXXX")

                response = f"!{line[1:3]}{model_code}\r"
                ser.write(response.encode())
                # print(f"[SIMULATOR] Получено: {repr(line)} Отправлено: {repr(response.strip())}", flush=True)

            # === 2. #310001 — запись младшего байта ===
            elif line.startswith("#") and len(line) == 7:
                try:
                    addr = int(line[1:3])  # 31
                    cmd = line[3:5]  # 00 или 0B
                    data = line[5:7]  # данные (01, 02...)
                except:
                    continue

                if addr not in devices:
                    continue

                device = devices[addr]
                if device["model"] != "I-7045":
                    continue

                # Текущее состояние DO (в HEX строке, например "0000")
                do_current = device["do"]  # строка "HHLL"
                high_byte = do_current[0:2]  # старший
                low_byte = do_current[2:4]  # младший

                if cmd == "00":
                    # Запись в младший байт
                    new_do = high_byte + data.upper()
                    device["do"] = new_do
                    # print(f"[SIMULATOR] DO #{addr} младший байт: {low_byte} → {data.upper()} (now {new_do})",flush=True)

                elif cmd == "0B":
                    # Запись в старший байт
                    new_do = data.upper() + low_byte
                    device["do"] = new_do
                    # print(f"[SIMULATOR] DO #{addr} старший байт: {high_byte} → {data.upper()} (now {new_do})",flush=True)


                else:
                    continue
                devices[addr]['di'] = new_do
                # Подтверждение записи — просто >
                ser.write(b">\r")
                # print(f"[SIMULATOR] Получено: {repr(line)} Отправлено: '>' {new_do} | {devices[addr]['di']}", flush=True)

            # === 3. #31 — чтение DO ===
            elif line.startswith("#") and len(line) != 7:
                try:
                    addr = int(line[1:3])
                except ValueError:
                    continue

                if addr not in devices:
                    continue
                response = ""
                device = devices[addr]

                if device["model"] == "I-7045":
                    # Возвращаем текущее состояние DO
                    # response = f">{device['do']}\r\n"
                    response = f">\r"
                elif device["model"] in ("I-7017", "I-7018"):
                    response = ">" + "".join(f"{val:+07.3f}" for val in device["ai"])

                if response:
                    ser.write((response + "\r").encode())
                    # print(f"[SIMULATOR] Получено: {repr(line)} Отправлено: {repr(response)}", flush=True)

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
                    # if device["model"] == "I-7045":
                    # print(f"[SIMULATOR] Получено: {repr(line)} Отправлено: {repr(response.strip())}", flush=True)

            # === 5. Эмуляция реакции на DO (например, давление растёт) ===
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

            time.sleep(0.01)

        except Exception as e:
            print(f"[SIMULATOR] ОШИБКА: {e}", flush=True)
            break


def start_simulator():
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        ser = serial.Serial(COM_PORT, BAUDRATE, timeout=1)
        handle_client(ser)

    except Exception as e:
        print(f"[SIMULATOR] Не удалось открыть {COM_PORT}: {e}")


if __name__ == "__main__":
    start_simulator()
