import sys
from mesh_common.logging import get_logger

logger = get_logger("mesh_hardware")

class RobotHardware:
    """Base class for robot hardware components.
    Provides fallback / dummy behavior for development on non-RPi systems.
    """
    def __init__(self):
        self.is_rpi = self._check_is_rpi()
        if self.is_rpi:
            logger.info("Raspberry Pi detected. Hardware modules enabled.")
        else:
            logger.warning("Not on Raspberry Pi. Using dummy hardware components.")

    def _check_is_rpi(self):
        try:
            with open("/sys/firmware/devicetree/base/model", "r") as f:
                model = f.read().lower()
                return "raspberry pi" in model
        except:
            return False

class LEDStripInterface:
    def set_color(self, index, r, g, b):
        pass
    def set_all(self, r, g, b):
        pass
    def show(self):
        pass

class DummyLEDStrip(LEDStripInterface):
    def set_color(self, index, r, g, b):
        logger.debug(f"[DUMMY LED] Set LED {index} to ({r}, {g}, {b})")
    def set_all(self, r, g, b):
        logger.debug(f"[DUMMY LED] Set all LEDs to ({r}, {g}, {b})")
    def show(self):
        pass
