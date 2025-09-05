# test_minimal_dcon.py — Минимальный тест для DCON на высокой скорости

import serial
import time
import sys

# === НАСТРОЙКИ ===
PORT = "COM3"  # ← поменяй на свой порт
BAUDRATE = 115200  # ← тестируем именно 115200
TIMEOUT = 1.0
COMMAND = "#17"  # Пример: чтение AI с модуля 17


# COMMAND = "@37"       # Или DI: модуль 37
# COMMAND = "$01"       # Или ping

# === ТЕСТ ===
def send_command(ser: serial.Serial, cmd: str) -> str:
    """Отправка команды и чтение ответа"""
    print(f"Отправляю: {cmd!r}")
    ser.write((cmd + "\r").encode('utf-8'))

    # Буфер для ответа
    buffer = b''
    start_time = time.time()

    while (time.time() - start_time) < TIMEOUT:
        if ser.in_waiting > 0:
            byte = ser.read(1)
            buffer += byte
            if byte == b'\r' or len(buffer) > 100:
                break
        else:
            time.sleep(0.01)
    else:
        print("❌ Таймаут при ожидании ответа")
        return None

    response = buffer.decode('utf-8', errors='ignore').strip()
    print(f"Получено: {response!r}")
    return response


def main():
    try:
        ser = serial.Serial(
            port=PORT,
            baudrate=BAUDRATE,
            timeout=0,  # Ручное управление
            bytesize=8,
            stopbits=1,
            parity='N'
        )
        print(f"✅ Подключено к {PORT} @ {BAUDRATE} baud")

        time.sleep(1)  # Дать устройству стартануть

        # Очистка буфера
        if ser.in_waiting:
            print(f"🧹 Очищаем буфер: {ser.in_waiting} байт")
            ser.reset_input_buffer()

        # Отправляем команду
        response = send_command(ser, COMMAND)

        if response:
            print("✅ Успешно получено:", repr(response))
        else:
            print("❌ Не удалось получить ответ")

        ser.close()
        print("🔌 Порт закрыт")

    except serial.SerialException as e:
        print(f"❌ Ошибка COM-порта: {e}")
    except Exception as e:
        print(f"❌ Непредвиденная ошибка: {e}")


if __name__ == "__main__":
    main()
