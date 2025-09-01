# core/global_state.py
"""
Глобальная шина команд и состояния системы.
"""

import threading
import traceback
from typing import Dict, Any, List, Optional, Union


class GlobalState:
    def __init__(self):
        self._data: Dict[str, Any] = {}
        self._lock = threading.RLock()
        self._hw = None
        self._daemon_mode = False
        self.safety_monitors = {}

    def set_hardware_interface(self, hw, daemon_mode: bool = False):
        """Устанавливает интерфейс (вызывается из HardwareDaemon)"""
        self._hw = hw
        #print(f"GS Поднят HW daemon_mode = {daemon_mode}")
        self._daemon_mode = daemon_mode

    def read_ai(self, press_id: int) -> List[Optional[float]]:
        key = f"press_{press_id}_temps"
        with self._lock:
            return self._data.get(key, [None] * 8)

    def read_digital(self, module_id: Union[str, int]) -> Optional[int]:
        module_id = str(module_id)
        key = f"di_module_{module_id}"

        with self._lock:
            # Сначала пробуем DI
            value = self._data.get(key)
            if value is not None:
                return value

            # Если нет DI — пробуем состояние DO (для модулей типа 31, 34)
            do_key = f"do_state_{module_id}"
            value = self._data.get(do_key)
            #print(f"GS try read_digital {module_id}, cyr val in state {value} | кей {do_key}")
            #if value !=0: print("="*30)
            if value is not None:
                return value

            # Для симуляции: возвращаем 0, если ничего нет
            # Можно добавить логирование для отладки
            # logging.debug(f"STATE: Нет данных для {key} или {do_key}")
            #print(f"GS try read_digital {module_id}, byt not found whis key {do_key}")
            return 0  # или None — зависит от политики

    def write_do(self, module_id: Union[str, int], low_byte: int, high_byte: int):
        """Всегда ставит команду в очередь urgent_do"""
        with self._lock:
            urgent = self._data.get("urgent_do", {})
            # Копируем, чтобы не мутировать напрямую
            urgent = urgent.copy() if urgent else {}
            urgent[f"{module_id}"] = (low_byte, high_byte)
            self._data["urgent_do"] = urgent
            # Опционально: логирование
            #print(f"STATE: DO-{module_id} в очередь: {low_byte:02X}, {high_byte:02X}")

    def write_do_bit(self, module_id: Union[str, int], channel: int, on: bool):

        current = self.read_digital(module_id)
        if current is None:
            return
        mask = 1 << channel
        new_state = (current | mask) if on else (current & ~mask)
        low = new_state & 0xFF
        high = (new_state >> 8) & 0xFF

        #if on:
            #print(f"reqest mod {module_id}, ch {channel} on {on}")
            #print(f"STATE: write_do_bit → module={module_id}, current={current}, new_state={new_state}")  # 🔥 Добавь это
        self.write_do(module_id, low, high)

    def set(self, key: str, value: Any):
        with self._lock:
            self._data[key] = value
            if key.startswith("do_state_"):
                # 🔍 Получаем стек вызова
                stack = traceback.extract_stack()
                # Берём предпоследний кадр — последний это сам set()
                filename, line, func, text = stack[-2]
                #print(f"🟡 STATE: {key} = {value} | Изменено в {func} ({filename}:{line})")

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def update(self, updates: Dict[str, Any]):
        with self._lock:
            self._data.update(updates)

    def clear(self):
        with self._lock:
            self._data.clear()

    # --- Методы для HardwareDaemon ---
    def get_urgent_do_commands(self) -> Dict[str, tuple]:
        """Возвращает и очищает срочные команды DO"""
        with self._lock:
            commands = self._data.get("urgent_do", {})
            self._data["urgent_do"] = {}
            return commands.copy()

    def get_heating_do_commands(self) -> Dict[str, tuple]:
        """Возвращает и очищает команды нагрева"""
        with self._lock:
            commands = self._data.get("heating_do", {})
            self._data["heating_do"] = {}
            return commands.copy()

    def get_all(self) -> dict:
        """
        Возвращает полную копию всех данных в state.
        Полезно для отладки и веб-интерфейса.
        """
        with self._lock:
            return self._data.copy()  # Возвращаем копию, чтобы избежать изменений извне

    def set_do_state(self, module_id: str, value: int):
        with self._lock:
            self._data[f"do_state_{module_id}"] = value
            # Логирование
            stack = traceback.extract_stack()
            filename, line, func, text = stack[-2]
            # print(f"🟢 DO_STATE: {module_id} = {value:04X} | {func} ({filename}:{line})")

# Единый экземпляр
state = GlobalState()