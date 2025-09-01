# core/program_manager.py

import json
import logging
import os
from typing import List, Dict, Any, Optional


class ProgramManager:
    """
    Управление программами для прессов.
    Загрузка, валидация, кэширование.
    """
    def __init__(self, programs_dir: str = "programs"):
        # Определяем корень проекта как директорию выше core/
        self.root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.programs_dir = os.path.join(self.root_dir, programs_dir)
        self.cache: Dict[str, List[Dict]] = {}
        self._ensure_dir()

    def _ensure_dir(self):
        """Создаёт папку programs, если её нет"""
        if not os.path.exists(self.programs_dir):
            os.makedirs(self.programs_dir)
            logging.info(f"Создана папка: {self.programs_dir}")


    def load_program(self, press_id: int) -> List[Dict[str, Any]]:
        """Загрузить программу для пресса по ID"""
        filename = os.path.join(self.programs_dir, f"press{press_id}.json")

        # Проверка кэша
        if filename in self.cache:
            return self.cache[filename]

        try:
            if not os.path.exists(filename):
                logging.error(f"❌ Программа не найдена: {filename}")
                return []

            with open(filename, "r", encoding="utf-8") as f:
                program = json.load(f)

            # Простая валидация
            if not isinstance(program, list):
                logging.error(f"❌ Программа {filename} должна быть массивом шагов")
                return []

            for i, step in enumerate(program):
                if not isinstance(step, dict):
                    logging.warning(f"Шаг {i} не является объектом: {step}")
                    continue
                if "step" not in step:
                    logging.warning(f"Шаг {i} без поля 'step': {step}")

            self.cache[filename] = program
            logging.info(f"✅ Программа для пресса {press_id} загружена ({len(program)} шагов)")
            return program

        except Exception as e:
            logging.error(f"❌ Ошибка чтения программы {filename}: {e}")
            return []

    def reload_program(self, press_id: int) -> List[Dict[str, Any]]:
        """Перезагрузить программу (удалить из кэша)"""
        filename = f"{self.programs_dir}/press{press_id}.json"
        if filename in self.cache:
            del self.cache[filename]
        return self.load_program(press_id)


if __name__ == "__main__":
    import time
    pm = ProgramManager()
    for pid in range(1, 4):
        print(f"\n--- Пресс {pid} ---")
        prog = pm.load_program(pid)
        if prog:
            print(f"✅ {len(prog)} шагов")
        else:
            print("❌ Не загружена")
    time.sleep(1)