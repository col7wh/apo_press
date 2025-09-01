# core/pid_controller.py
"""
Универсальный ПИД-контроллер с anti-windup и derivative on measurement.
"""
import time


class PIDController:
    def __init__(self, Kp, Ki, Kd, setpoint=0.0, output_limits=(0, 100)):
        self.Kp = Kp
        self.Ki = Ki
        self.Kd = Kd
        self.setpoint = setpoint
        self.output_limits = output_limits

        self._last_input = None
        self._last_error = 0.0
        self._integral = 0.0
        self._last_time = time.time()

        self._proportional = 0.0
        self._derivative = 0.0

        # Для derivative on measurement
        self.derivative_on_measurement = True

    def set_setpoint(self, setpoint):
        self.setpoint = setpoint

    def set_tunings(self, Kp, Ki, Kd):
        self.Kp = Kp
        self.Ki = Ki
        self.Kd = Kd

    def compute(self, input_value):
        now = time.time()
        dt = now - self._last_time

        if dt <= 0:
            return self._proportional + self._integral + self._derivative

        # Ошибка
        error = self.setpoint - input_value

        # Интегральная часть
        self._integral += self.Ki * error * dt
        self._integral = self._clamp(self._integral)

        # Пропорциональная часть
        self._proportional = self.Kp * error

        # Дифференциальная часть
        if self.derivative_on_measurement and self._last_input is not None:
            self._derivative = -self.Kd * (input_value - self._last_input) / dt
        elif self._last_time > 0:
            self._derivative = self.Kd * (error - self._last_error) / dt

        # Сумма
        output = self._proportional + self._integral + self._derivative
        output = self._clamp(output)

        # Сохраняем
        self._last_error = error
        self._last_input = input_value
        self._last_time = now

        return output

    def _clamp(self, value):
        mn, mx = self.output_limits
        return max(mn, min(mx, value))

    def reset(self):
        self._last_input = None
        self._integral = 0.0
        self._last_error = 0.0
        self._last_time = time.time()