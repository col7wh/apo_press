# emulator.py
import serial
import time

# Настройки
PORT = "COM5"  # Порт, на который приходит * от графического ПК
BAUDRATE = 1200  # Скорость из сниффера

# Константы
NUM_OF_PRESSES = 3
NUM_OF_COUPLS = 7  # Количество термопар на пресс


def exchange_graph(ser, pressures, temperatures, t_targets, t_seconds, programs):
    """Формирует и отправляет 66-байтный пакет, как в exchangeGraph()"""
    buf_data = bytearray(66)

    for press_id in range(NUM_OF_PRESSES):
        # Давление ×2 (с округлением)
        buf_data[8 * press_id] = int(pressures[press_id] * 2 + 0.5)

        # Температуры (целое число)
        for iC in range(NUM_OF_COUPLS):
            temp = temperatures[press_id][iC]
            buf_data[iC + press_id * 8 + 1] = int(temp + 0.5)

        # Уставка температуры
        buf_data[48 + press_id] = int(t_targets[press_id] + 0.5)

        # Время в минутах
        buf_data[60 + press_id] = int(t_seconds[press_id] / 60)

        # Номер программы (заглушка)
        buf_data[54] = 100

    # Для ещё большей наглядности
    print("📊 Данные по прессам:")
    for i in range(NUM_OF_PRESSES):
        print(f"  Пресс {i + 1}: P×2={buf_data[8 * i]}, T={buf_data[8 * i + 1:8 * i + 8]}")

    # Преобразуем в HEX-строку (для удобства)
    hex_string = ''.join(f'{b:02X}' for b in buf_data)
    print(f"📤 HEX: {hex_string}")

    try:
        packet = bytes.fromhex(hex_string)
        for b in packet:
            ser.write(bytes([b]))
            time.sleep(0.001)  # 1 мс между байтами

    except serial.SerialTimeoutException:
        print("❌ Ошибка: превышено время записи (write timeout)")
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")


def main():
    try:
        ser = serial.Serial(PORT, BAUDRATE, timeout=1)
        print(f"[EMULATOR] Слушаю {PORT} @ {BAUDRATE} (ожидание *)")
    except Exception as e:
        print(f"[EMULATOR] Ошибка: {e}")
        return

    while True:
        try:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                if b'*' in data:
                    print(f"[EMULATOR] Получено: '*'")
                    # Пример данных
                    pressures = [3.0, 2.5, 4.0]
                    temperatures = [
                        [140, 151, 155, 158, 158, 155, 130],
                        [142, 150, 156, 157, 159, 154, 131],
                        [139, 152, 154, 159, 157, 156, 129]
                    ]
                    t_targets = [150, 155, 145]
                    t_seconds = [120, 180, 240]
                    programs = [1, 2, 3]

                    exchange_graph(ser, pressures, temperatures, t_targets, t_seconds, programs)
            time.sleep(0.1)
        except Exception as e:
            print(f"[EMULATOR] Ошибка: {e}")
            break


if __name__ == "__main__":
    main()
