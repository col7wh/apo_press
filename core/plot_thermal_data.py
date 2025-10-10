# plot_thermal_data.py

import os
import tkinter as tk
from tkinter import filedialog
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime
import matplotlib.dates as mdates


class ThermalProfilePlotter:
    """
    Чистый класс для построения графика.
    НЕ занимается открытием файлов — только строит по переданному пути.
    """

    def __init__(
        self,
        show_plot=False,
        save=True,
        ylim_temp=None,
        ylim_pressure=None,
        temp_colors=None,
        pressure_color='orangered',
        background_style='darkgrid'
    ):
        self.show_plot = show_plot
        self.save = save
        self.ylim_temp = ylim_temp
        self.ylim_pressure = ylim_pressure
        self.temp_colors = temp_colors
        self.pressure_color = pressure_color
        self.background_style = background_style

    def plot(self, file_path):
        """
        Основной метод. Принимает путь к файлу как аргумент.

        Параметры:
            file_path (str): Путь к CSV-файлу.

        Возвращает:
            dict: {'status': 'OK' или 'ERROR', 'message': str}
        """
        if not os.path.isfile(file_path):
            return {'status': 'ERROR', 'message': f'Файл не найден: {file_path}'}

        try:
            df = pd.read_csv(file_path)

            required_cols = ['timestamp', 'temp1', 'pressure', 'target_temp']
            for col in required_cols:
                if col not in df.columns:
                    return {'status': 'ERROR', 'message': f'Не хватает колонки: {col}'}

            # Парсим время
            today = datetime.now().strftime('%Y-%m-%d')
            df['datetime'] = pd.to_datetime(today + ' ' + df['timestamp'], format='%Y-%m-%d %H:%M:%S')

            # Стиль
            if self.background_style == 'darkgrid':
                plt.style.use('dark_background')
                facecolor = '#121212'
                text_color = 'white'
                grid_color = '#444444'
            elif self.background_style == 'whitegrid':
                plt.style.use('seaborn-v0_8-whitegrid')
                facecolor = 'white'
                text_color = 'black'
                grid_color = 'lightgray'
            else:
                plt.style.use('default')
                facecolor = 'white'
                text_color = 'black'
                grid_color = 'lightgray'

            fig, ax1 = plt.subplots(figsize=(12, 7), facecolor=facecolor)
            fig.subplots_adjust(left=0.08, right=0.88, top=0.92, bottom=0.12)

            time = df['datetime']

            # === Температура ===
            temp_cols = [f'temp{i}' for i in range(1, 8) if f'temp{i}' in df.columns]
            temps = df[temp_cols]

            default_colors = ['#00aaff', '#40c0ff', '#66d9ff', '#8ad4ff', '#aaddff', '#ccf0ff', '#e6f7ff']
            colors = self.temp_colors or default_colors[:len(temp_cols)]

            for i, col in enumerate(temp_cols):
                ax1.plot(time, temps[col], label=col, color=colors[i], linewidth=1.6, alpha=0.9)

            ax1.plot(time, df['target_temp'], 'w--', linewidth=2.2, label='target_temp', alpha=0.95)

            ax1.set_ylabel('Температура (°C)', fontsize=11, color=text_color)
            ax1.tick_params(axis='y', labelcolor=text_color, labelsize=9)
            ax1.grid(True, axis='y', linestyle='--', alpha=0.3, color=grid_color)

            if self.ylim_temp:
                ax1.set_ylim(self.ylim_temp)
            else:
                all_temps = pd.concat([temps, df['target_temp']], axis=1).stack()
                min_val = max(0, all_temps.min() - 5)
                max_val = all_temps.max() + 5
                ax1.set_ylim(min_val, max_val)

            # === Давление ===
            ax2 = ax1.twinx()
            ax2.plot(time, df['pressure'], color=self.pressure_color, linewidth=2.0, label='pressure', alpha=0.9)
            if 'target_pressure' in df.columns and df['target_pressure'].notna().any():
                ax2.plot(time, df['target_pressure'], '--', color=self.pressure_color, linewidth=1.4, alpha=0.7, label='target_pressure')

            ax2.set_ylabel('Давление', fontsize=11, color=self.pressure_color)
            ax2.tick_params(axis='y', labelcolor=self.pressure_color, labelsize=9)

            if self.ylim_pressure:
                ax2.set_ylim(self.ylim_pressure)
            else:
                max_press = df['pressure'].max()
                if 'target_pressure' in df.columns:
                    max_press = max(max_press, df['target_pressure'].max())
                ax2.set_ylim(0, max_press * 1.08)

            # === Ось X: Умная разметка времени ===
            duration = (time.iloc[-1] - time.iloc[0]).total_seconds()

            if duration <= 30 * 60:
                locator = mdates.MinuteLocator(interval=5)
                fmt = '%H:%M'
            elif duration <= 2 * 3600:
                locator = mdates.MinuteLocator(interval=15)
                fmt = '%H:%M'
            elif duration <= 6 * 3600:
                locator = mdates.HourLocator(interval=1)
                fmt = '%H:%M'
            else:
                locator = mdates.HourLocator(interval=2)
                fmt = '%m-%d %H:%M'

            ax1.xaxis.set_major_locator(locator)
            ax1.xaxis.set_major_formatter(mdates.DateFormatter(fmt))
            plt.setp(ax1.xaxis.get_majorticklabels(), rotation=0, ha='center', fontsize=10, color=text_color)

            # === Легенда справа ===
            lines1, labels1 = ax1.get_legend_handles_labels()
            lines2, labels2 = ax2.get_legend_handles_labels()
            legend = ax1.legend(
                lines1 + lines2, labels1 + labels2,
                loc='upper left',
                bbox_to_anchor=(1.02, 1),
                fontsize=9,
                frameon=True,
                fancybox=False,
                edgecolor='none',
                facecolor=facecolor if self.background_style == 'darkgrid' else 'white',
                labelcolor=text_color
            )

            plt.title(f"Термический профиль: {os.path.basename(file_path)}", fontsize=13, color=text_color, pad=15)

            # === Сохранение ===
            if self.save:
                base_path = os.path.splitext(file_path)[0]
                image_path = base_path + '.png'
                fig.savefig(image_path, dpi=180, bbox_inches='tight', facecolor=fig.get_facecolor())
                message = f'График сохранён: {image_path}'
            else:
                message = 'График не сохранён.'

            if self.show_plot:
                plt.show()  # Теперь безопасно — в main потоке
            else:
                plt.close(fig)

            return {'status': 'OK', 'message': message}

        except Exception as e:
            return {'status': 'ERROR', 'message': f'Ошибка при обработке файла: {str(e)}'}


# =============================
# Запуск как автономный скрипт
# =============================

if __name__ == "__main__":
    print("Запуск ThermalProfilePlotter — выберите CSV файл...")

    # Создаём Tk только здесь, в main потоке
    root = tk.Tk()
    root.withdraw()  # Скрыть главное окно

    file_path = filedialog.askopenfilename(
        title="Выберите CSV-файл",
        filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
    )

    if not file_path:
        print("Файл не выбран.")
    else:
        # Создаём экземпляр и вызываем plot с явным путём
        plotter = ThermalProfilePlotter(
            show_plot=True,
            save=True,
            background_style='darkgrid'
        )
        result = plotter.plot(file_path)
        print(result['message'])

    # Явно завершаем Tk
    try:
        root.destroy()
    except:
        pass