# diagnose.py

import json
import time
import logging
from core.hardware_interface import HardwareInterface
from typing import Union

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [DIAG] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler("diagnostics.log", encoding="utf-8"),
        #logging.StreamHandler()
    ]
)

# Глобальные переменные
hw = None
hw_config = None


def load_hardware_config():
    try:
        with open("config/hardware_config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.critical(f"Не удалось загрузить hardware_config.json: {e}")
        return None


def test_connection(module_id: str, module_type: str = ""):
    command = f"${module_id}M"
    response = hw._send_command(command)
    if response and response.startswith(f"!{module_id}"):
        logging.info(f" {module_type}-{module_id}: связь OK")
        print(f"✅ {module_type}-{module_id}: связь OK")
        return True
    else:
        logging.error(f" {module_type}-{module_id}: нет ответа")
        print(f"❌ {module_type}-{module_id}: нет ответа")
        return False


def read_ai(module_id: str):
    """Чтение и парсинг AI с поддержкой формата +4.231, +20.500, +0020.9"""
    command = f"#{module_id}"
    response = hw._send_command(command)
    logging.info(f"DCON: {command} -> {response}")
    #print(f"DCON: {command} -> {response}")
    if not response:
        logging.error(f" AI-{module_id}: нет ответа")
        return None

    # Удаляем '>' и пробелы
    clean = response.strip().lstrip('>').strip()

    if not clean.startswith('+'):
        logging.error(f" AI-{module_id}: ответ не начинается с '+': {clean}")
        return None

    # Разбиваем по '+' и убираем пустые
    raw_values = [val.strip() for val in clean.split('+') if val.strip()]

    if not raw_values:
        logging.error(f" AI-{module_id}: не удалось извлечь значения")
        return None

    # Логируем как сырые строки
    logging.info(f" AI-{module_id}: сырые данные: {raw_values}")
    return raw_values  # возвращаем список строк


def read_di_do(module_id: str):
    """Чтение DI/DO: возвращает HEX и BIN, логирует как есть"""
    command = f"@{module_id}"
    response = hw._send_command(command)
    logging.info(f"DCON: {command} -> {response}")
    #print(f"DCON: {command} -> {response}")

    if not response or not response.startswith('>'):
        logging.error(f" DI/DO-{module_id}: нет ответа или ошибка формата")
        return None

    hex_str = response[1:].strip()
    try:
        value = int(hex_str, 16)
        binary = f"{value:016b}"
        logging.info(f" DI/DO-{module_id}: HEX={hex_str}, BIN={binary}")
        return {"hex": hex_str, "bin": binary, "int": value}
    except ValueError:
        logging.error(f" DI/DO-{module_id}: не удалось разобрать HEX: {hex_str}")
        return None


def write_do(module_id: Union[str, int], byte_low: int, byte_high: int):
    """Обёртка для hw.write_do — вызывает через глобальный hw"""
    global hw
    if hw is None:
        logging.error(" HardwareInterface не инициализирован")
        return

    try:
        # Вызываем метод из hardware_interface
        hw.write_do(module_id, byte_low, byte_high)
    except Exception as e:
        logging.error(f" Ошибка вызова hw.write_do: {e}")


def toggle_do_channel(module_id: str, channel: int, on: bool):
    if channel < 0 or channel > 15:
        logging.error("Канал должен быть 0–15")
        return

    current = hw.read_digital(module_id) or 0
    mask = 1 << channel
    if on:
        new_state = current | mask
        action = "включён"
    else:
        new_state = current & ~mask
        action = "выключен"

    hw.write_do(module_id, new_state & 0xFF, (new_state >> 8) & 0xFF)
    time.sleep(0.1)

    # Проверка
    readback = hw.read_digital(module_id)
    if readback is not None and bool(readback & mask) == on:
        logging.info(f" DO-{module_id}.{channel} {action}")
    else:
        logging.error(f" DO-{module_id}.{channel}: ошибка {action}")


def test_all_presses():
    logging.info(" Проверка всех прессов...")
    for press in hw_config["presses"]:
        pid = press["id"]
        print("+++++++Проверка прессa "+ str(pid)+" ++++++++++++++")
        ai = press["modules"]["ai"]
        do = press["modules"]["do"]
        test_connection(ai, "AI")
        read_ai(ai)
        test_connection(do, "DO")
        read_di_do(do)
        print("==================================")
    test_common_modules()


def test_common_modules():
    print("+++++++test_common_modules++++++++++")
    ai = hw_config["common"]["ai_pressure_module"]
    di = hw_config["common"]["di_module"]
    di2 = hw_config["common"]["di_module_2"]
    do1 = hw_config["common"]["do_module_1"]
    do2 = hw_config["common"]["do_module_2"]
    test_connection(ai, "AI - Pressure ")
    read_ai(ai)
    test_connection(di, "DI1 - Buttons ")
    read_di_do(di)
    test_connection(di2, "DI2 - Buttons ")
    read_di_do(di2)
    test_connection(do1, "DO1 - Lamps ")
    read_di_do(do1)
    test_connection(do2, "DO2 - Lamps ")
    read_di_do(do2)
    print("==================================")


def manual_do_control():
    print("\n🔧 Ручное управление DO (введите 00 для выхода)")
    global hw
    if hw is None:
        logging.error("❌ hw не инициализирован")
        return

    while True:
        try:
            mod_input = input("Модуль (ID): ").strip()
            if mod_input == "00":
                break
            if not mod_input.isdigit():
                logging.error("ID модуля должен быть числом")
                continue

            low_hex = input("LOW (HEX, 00–FF): ").strip()
            high_hex = input("HIGH (HEX, 00–FF): ").strip()

            byte_low = int(low_hex, 16)
            byte_high = int(high_hex, 16)
            module_id = int(mod_input)

            logging.info(f" Запись DO: модуль={module_id}, LOW=0x{byte_low:02X}, HIGH=0x{byte_high:02X}")
            write_do(module_id, byte_low, byte_high)  # вызывает обёртку

        except ValueError:
            logging.error("Некорректное HEX-значение. Используйте 00–FF.")
        except Exception as e:
            logging.error(f"Ошибка: {e}")

def interactive_do_channel():
    print("\n🔧 Управление отдельным каналом DO")
    try:
        mod = input("Модуль DO: ").strip()
        ch = int(input("Канал (0–15): "))
        action = input("Действие (on/off): ").strip().lower()
        if action == "on":
            toggle_do_channel(mod, ch, True)
        elif action == "off":
            toggle_do_channel(mod, ch, False)
        else:
            logging.error("Введите on или off")
    except Exception as e:
        logging.error(f"Ошибка: {e}")


def read_all_ai():
        """Чтение всех AI-модулей из dcon_devices"""
        logging.info(" Чтение всех AI-модулей...")

        try:
            with open("config/config.json", "r", encoding="utf-8") as f:
                config = json.load(f)
            devices = config.get("dcon_devices", [])
        except Exception as e:
            logging.error(f"Не удалось загрузить config.json: {e}")
            return

        # Фильтруем только AI-модули
        ai_modules = [dev for dev in devices if dev["type"] in ("7017", "7018")]
        # print(ai_modules)

        if not ai_modules:
            logging.warning(" В dcon_devices нет AI-модулей (7017/7018)")
            return

        for dev in ai_modules:
            try:
                module_id = f"{int(dev['id']):02d}"
                read_ai(module_id)
            except Exception as e:
                logging.error(f" Ошибка при чтении AI-{module_id}: {e}")


def read_all_di_do():
    logging.info(" Чтение всех DI/DO...")
    print("📌 Чтение всех DI/DO...")

    try:
        with open("config/config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
        devices = config.get("dcon_devices", [])
    except Exception as e:
        logging.error(f"Не удалось загрузить config.json: {e}")
        return

    # Фильтруем только AI-модули
    di_modules = [dev for dev in devices if dev["type"] in ("7045", "7051")]
    # print(ai_modules)

    if not di_modules:
        logging.warning(" В dcon_devices нет DI-модулей (7051/7045)")
        return

    for dev in di_modules:
        try:
            module_id = f"{int(dev['id']):02d}"
            read_di_do(module_id)
        except Exception as e:
            logging.error(f" Ошибка при чтении DI-{module_id}: {e}")

def check_all_connections():
    logging.info("Проверка связи со всеми модулями...")
    print("🔌 Проверка связи со всеми модулями...")
    for press in hw_config["presses"]:
        ai = press["modules"]["ai"]
        do = press["modules"]["do"]
        test_connection(ai, "AI")
        test_connection(do, "DO")
    test_common_modules()


def show_status_summary():
    print("\n" + "="*50)
    print("КРАТКИЙ ОТЧЁТ ПО ОБОРУДОВАНИЮ")
    print("="*50)
    for i, press in enumerate(hw_config["presses"], 1):
        ai_mod = press["modules"]["ai"]
        do_mod = press["modules"]["do"]
        ai_temp = read_ai(ai_mod)
        temp_str = []
        for value in ai_temp:
            num = round(float(value), 1)
            formatted = f"{num:.1f}" + '°C'
            temp_str.append(formatted)

        do_val = hw.read_digital(do_mod)
        do_bin = f"{do_val:016b}" if do_val else "????"
        print(f"Пресс {i}: T={temp_str} | DO={do_bin}")

    ai = hw_config["common"]["ai_pressure_module"]
    di = hw_config["common"]["di_module"]
    di2 = hw_config["common"]["di_module_2"]
    do1 = hw_config["common"]["do_module_1"]
    do2 = hw_config["common"]["do_module_2"]

    ai_temp = read_ai(ai)
    temp_str = []
    for value in ai_temp:
        num = round(float(value), 1)
        formatted = f"{num:.1f}" + ' bar'
        temp_str.append(formatted)

    di_val = hw.read_digital(di)
    di_val2 = hw.read_digital(di2)
    di_bin = f"{di} = {di_val:016b} | {di2} = {di_val2:016b}" if di_val else "????"
    do_val = hw.read_digital(do1)
    do_val2 = hw.read_digital(do2)
    do_bin = f"{do1} = {do_val:016b} | {do2} = {do_val2:016b}" if do_val else "????"
    print(f"Общие DI: {di_bin}")
    print(f"Общие DO: {do_bin}")
    print(f"Давление : {temp_str}")
    print("="*50)

def show_network():
    """Чтение текущих значений всех модулей из dcon_devices"""
    config_path = "config/config.json"

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        devices = config.get("dcon_devices", [])
        if not devices:
            logging.warning(f"В {config_path} нет dcon_devices.")
            return
    except Exception as e:
        logging.error(f"Не удалось загрузить {config_path}: {e}")
        return

    print("\n" + "="*60)
    print("🔍 СЫРЫЕ ДАННЫЕ С МОДУЛЕЙ (из dcon_devices)")
    print("="*60)

    # AI-модули
    ai_modules = [d for d in devices if d["type"] in ("7017", "7018")]
    if ai_modules:
        print("📡 АНАЛОГОВЫЕ ВХОДЫ (AI):")
        for dev in ai_modules:
            module_id = f"{int(dev['id']):02d}"
            data = read_ai(module_id)
            if data:
                print(f"  AI-{module_id}: {' + '.join(data)}")

    # DI/DO-модули
    dio_modules = [d for d in devices if d["type"] in ("7045", "7051", "7052", "7060")]
    if dio_modules:
        print("\n🔢 ДИСКРЕТНЫЕ ВХОДЫ/ВЫХОДЫ (DI/DO):")
        for dev in dio_modules:
            module_id = f"{int(dev['id']):02d}"
            data = read_di_do(module_id)
            if data:
                print(f"  DO/DI-{module_id}: HEX={data['hex']}, BIN={data['bin']}")

    print("="*60)


def scan_network():
    """Сканирование сети DCON: поиск всех отвечающих модулей"""
    logging.info(" Сканирование сети DCON (адреса 01–40)...")
    print("🔍 Сканирование сети DCON (адреса 01–40)...")
    discovered = []

    for addr in range(1, 40):
        module_id = f"{addr:02d}"
        command = f"${module_id}M"
        response = hw._send_command(command)
        time.sleep(0.1)  # избегаем перегрузки порта

        if response and response.startswith(f"!{module_id}"):
            model_code = response[3:]  # после !xx
            model = "Неизвестно"
            if model_code == "7017":
                model = "I-7017"  # 8-канальный AI
            elif model_code == "7018":
                model = "I-7018"  # 8-канальный AI (другой тип)
            elif model_code == "7051":
                model = "I-7051"  # 8-канальный DO
            elif model_code == "7045":
                model = "I-7045"  # DO/DO
            # Добавь свои модели при необходимости

            numeric_id = int(module_id)  # "33" → 33

            info = {
                "id": numeric_id,
                "model": model,
                "type": model_code
            }
            discovered.append(info)
            logging.info(f" Найден {model} по адресу {module_id}, ответ {response}")
            print(f"✅ Найден {model} по адресу {module_id}")

    if not discovered:
        logging.warning(" Ни одного модуля DCON не найдено.")
        return

    print("\n" + "="*60)
    print("🌐 РЕЗУЛЬТАТЫ СКАНИРОВАНИЯ DCON-СЕТИ")
    print("="*60)
    for dev in discovered:
        print(f"📍 {dev['id']} | {dev['model']} ")
    print("="*60)

    # Предложить обновить dcon_devices в config/config.json
    save = input("\nОбновить dcon_devices в config/config.json? (y/n): ").strip().lower()
    if save != 'y':
        return

    config_path = "config/config.json"

    try:
        # Читаем СУЩЕСТВУЮЩИЙ config.json
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
        logging.info(f"Загружен существующий конфиг: {config_path}")
    except FileNotFoundError:
        logging.warning(f"{config_path} не найден. Создаём новый.")
        config_data = {}
    except Exception as e:
        logging.error(f"Ошибка чтения {config_path}: {e}. Создаём новый.")
        config_data = {}

    # Обновляем ТОЛЬКО ветку dcon_devices
    config_data["dcon_devices"] = discovered

    # Сохраняем обратно
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        logging.info(f"✅ {config_path} успешно обновлён: dcon_devices записан.")
        print(f"Файл {config_path} обновлён.")
    except Exception as e:
        logging.error(f"❌ Не удалось сохранить {config_path}: {e}")


def main_menu():
    print("\n🔧 СИСТЕМА ДИАГНОСТИКИ DCON")
    print("1 — Проверить связь со всеми модулями")
    print("2 — Прочитать все AI (температуры)")
    print("3 — Прочитать все DI/DO")
    print("4 — Полная диагностика всех прессов")
    print("5 — Проверить общие модули (DI)")
    print("6 — Управление DO: напрямую (LOW/HIGH)")
    print("7 — Управление DO: отдельный канал (on/off)")
    print("8 — Краткий отчёт по системе")
    print("9 — Сканирование сети DCON (автоопределение модулей)")
    print("10 — Вывод всех текущих значений DCON ")
    print("0 — Выход")

    while True:
        choice = input("\nВыберите действие: ").strip()

        if choice == "1":
            check_all_connections()
        elif choice == "2":
            read_all_ai()
        elif choice == "3":
            read_all_di_do()
        elif choice == "4":
            test_all_presses()
        elif choice == "5":
            test_common_modules()
        elif choice == "6":
            manual_do_control()
        elif choice == "7":
            interactive_do_channel()
        elif choice == "8":
            show_status_summary()
        elif choice == "9":
            scan_network()
        elif choice == "10":
            show_network()
        elif choice == "0":
            logging.info("Диагностика завершена.")
            break
        else:
            print("❌ Неверный выбор. Введите 1–9.")


def main():
    global hw, hw_config

    logging.info("Запуск диагностики DCON...")
    hw_config = load_hardware_config()

    if not hw_config:
        exit(1)

    try:
        hw = HardwareInterface("config/system.json", direct_mode=True)
    except Exception as e:
        logging.critical(f"Ошибка инициализации интерфейса: {e}")
        exit(1)

    try:
        main_menu()
    except KeyboardInterrupt:
        logging.info("Диагностика прервана пользователем.")
    except Exception as e:
        logging.critical(f"Ошибка: {e}")
    finally:
        hw.close()
        logging.info("Соединение закрыто.")


if __name__ == "__main__":
    main()