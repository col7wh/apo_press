# smart_responder.py
import serial
import time
import binascii

# ——— Настройки ———
PORT = "COM5"        # Порт, на котором приходит *
BAUDRATE = 1200      # Как в сниффере
TIMEOUT = 1

# ——— Глобальные переменные ———
packet_counter = 1   # Сколько раз уже ответили
max_length = 50      # Максимальная длина пакета

def main():
    global packet_counter

    try:
        ser = serial.Serial(PORT, BAUDRATE, timeout=TIMEOUT)
        print(f"[SMART] Слушаю {PORT} @ {BAUDRATE}")
        print("Ожидание команды '*'")
    except Exception as e:
        print(f"[SMART] Ошибка: {e}")
        return

    while True:
        try:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                hex_data = binascii.hexlify(data).decode().upper()

                print(f"[ПРИНЯТО] {data!r} | HEX: {hex_data}")

                if b'*' in data:
                    # Определяем длину следующего пакета
                    length = min(packet_counter, max_length)
                    print(f"[SMART] Ответ {packet_counter}: длина = {length}")

                    # Формируем данные: 01, 02, 03, ..., length
                    values = [i & 0xFF for i in range(1, length + 1)]  # 1, 2, ..., N
                    hex_values = ''.join([f"{v:02X}" for v in values])
                    packet = f"${hex_values}".encode()

                    ser.write(packet)
                    print(f"[ОТПРАВЛЕНО] {packet!r}")

                    packet_counter += 1

            time.sleep(0.1)
        except Exception as e:
            print(f"[SMART] Ошибка: {e}")
            break

if __name__ == "__main__":
    main()