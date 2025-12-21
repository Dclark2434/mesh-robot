import time
from mesh_common.logging import get_logger

try:
    from gpiozero import OutputDevice
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False

logger = get_logger("mesh_buzzer")

class BuzzerController:
    def __init__(self, pin=17):
        self.device = None
        if GPIO_AVAILABLE:
            try:
                self.device = OutputDevice(pin)
                logger.info(f"Buzzer initialized on GPIO {pin}")
            except Exception as e:
                logger.error(f"Failed to initialize buzzer: {e}")
        else:
            logger.warning("gpiozero not found. Buzzer disabled.")

    def on(self):
        if self.device: self.device.on()
        
    def off(self):
        if self.device: self.device.off()

    def beep(self, duration=0.25):
        """Standard beep. Default 0.25s, Max 2s."""
        # Enforce limits
        duration = min(duration, 2.0)
        
        if self.device:
            self.device.on()
            time.sleep(duration)
            self.device.off()
        else:
            logger.info(f"[SIMULATION] *BEEP* ({duration}s)")

    def warn(self):
        """Double beep sequence."""
        if self.device:
            self.beep(0.5)
            time.sleep(0.1)
            self.beep(0.5)
        else:
            logger.info("[SIMULATION] *BEEP* *BEEP*")

    def alarm(self):
        """Alarm sequence. Capped at 2s total active time."""
        if self.device:
            # 5 fast beeps
            for _ in range(5):
                self.device.on()
                time.sleep(0.1)
                self.device.off()
                time.sleep(0.1)
        else:
            logger.info("[SIMULATION] *ALARM SIREN*")
