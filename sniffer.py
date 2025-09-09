# sniffer.py
# Перехватывает и анализирует данные, идущие на графический компьютер
import serial
import time
import binascii
from datetime import datetime

# Настройки — попробуй разные, если не видишь данных
PORT = "COM3"  # Порт, который слушаем
BAUDRATE = 1200  # Попробуй: 9600, 19200, 38400, 115200
BYTESIZE = 8
PARITY = 'N'
STOPBITS = 1
TIMEOUT = 2


def sniff():
    try:
        ser = serial.Serial(
            port=PORT,
            baudrate=BAUDRATE,
            bytesize=BYTESIZE,
            parity=PARITY,
            stopbits=STOPBITS,
            timeout=TIMEOUT
        )
        print(f"[SNIFFER] Слушаю порт {PORT} @ {BAUDRATE} 8N1")
        print("-" * 60)

        while True:
            if ser.in_waiting > 0:
                # Читаем всё, что есть
                raw_data = ser.read(ser.in_waiting)
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

                # Выводим в разных форматах
                print(f"[{timestamp}] Длина: {len(raw_data)} байт")

                # HEX
                hex_data = binascii.hexlify(raw_data).decode().upper()
                print(f"  HEX:  {hex_data}")

                # ASCII (если текст)
                ascii_data = ''.join([chr(b) if 32 <= b < 127 else '.' for b in raw_data])
                print(f"  ASCII: {ascii_data}")

                print("-" * 60)
            else:
                time.sleep(0.1)

    except serial.SerialException as e:
        print(f"[SNIFFER] Ошибка порта: {e}")
    except KeyboardInterrupt:
        print("\n[SNIFFER] Остановлен")


if __name__ == "__main__":
    sniff()
