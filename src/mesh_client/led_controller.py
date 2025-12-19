import time
import math
import threading
from enum import Enum
from mesh_common.logging import get_logger
from mesh_common.hardware import RobotHardware, DummyLEDStrip

logger = get_logger("mesh_led")

class LEDState(Enum):
    IDLE = "idle"         # Dim blue pulsing or static
    LISTENING = "listening" # Solid green
    THINKING = "thinking"   # Pulsing gold/yellow
    SPEAKING = "speaking"   # Pulsing cyan
    ERROR = "error"       # Flashing red

class LEDManager(RobotHardware):
    def __init__(self, led_count=8, brightness=50):
        super().__init__()
        self.led_count = led_count
        self.brightness = brightness
        self.current_state = LEDState.IDLE
        self.stop_event = threading.Event()
        self.thread = None
        
        # Hardware Setup
        if self.is_rpi:
            try:
                from rpi_ws281x import Adafruit_NeoPixel, Color
                # Freenove default: Pin 18, 800khz, DMA 10, Brighness (0-255)
                self.strip = Adafruit_NeoPixel(self.led_count, 18, 800000, 10, False, self.brightness, 0)
                self.strip.begin()
                self._Color = Color
                logger.info("Freenove LED strip initialized.")
            except ImportError:
                logger.error("rpi_ws281x not found. Falling back to dummy.")
                self.strip = DummyLEDStrip()
                self._Color = lambda r, g, b: (r, g, b)
            except Exception as e:
                logger.error(f"Failed to initialize LED strip: {e}")
                self.strip = DummyLEDStrip()
                self._Color = lambda r, g, b: (r, g, b)
        else:
            self.strip = DummyLEDStrip()
            self._Color = lambda r, g, b: (r, g, b)

        self.start()

    def set_state(self, state: LEDState):
        if self.current_state != state:
            logger.debug(f"LED State: {state.value}")
            self.current_state = state

    def start(self):
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join()
        self._clear_leds()

    def _clear_leds(self):
        if hasattr(self.strip, 'setPixelColor'):
            for i in range(self.led_count):
                 self.strip.setPixelColor(i, self._Color(0,0,0))
            self.strip.show()
        else:
            self.strip.set_all(0,0,0)

    def _run(self):
        tick = 0
        while not self.stop_event.is_set():
            state = self.current_state
            
            if state == LEDState.IDLE:
                # Dim slow pulse blue
                val = int(10 + 20 * (0.5 + 0.5 * math.sin(tick * 0.1)))
                self._set_all(0, 0, val)
            
            elif state == LEDState.LISTENING:
                # Solid Green
                self._set_all(0, 50, 0)
            
            elif state == LEDState.THINKING:
                # Pulsing Yellow/Gold
                val = int(30 + 70 * (0.5 + 0.5 * math.sin(tick * 0.5)))
                self._set_all(val, int(val * 0.8), 0)
            
            elif state == LEDState.SPEAKING:
                # Rapid Pulse Cyan
                val = int(20 + 80 * (0.5 + 0.5 * math.sin(tick * 0.8)))
                self._set_all(0, val, val)
            
            elif state == LEDState.ERROR:
                # Flashing Red
                if (tick // 5) % 2 == 0:
                    self._set_all(100, 0, 0)
                else:
                    self._set_all(0, 0, 0)
            
            self._show()
            tick += 1
            time.sleep(0.05)

    def _set_all(self, r, g, b):
        if hasattr(self.strip, 'setPixelColor'):
            for i in range(self.led_count):
                self.strip.setPixelColor(i, self._Color(r, g, b))
        else:
            self.strip.set_all(r, g, b)

    def _show(self):
        self.strip.show()

if __name__ == "__main__":
    # Test
    mgr = LEDManager()
    try:
        print("Testing States...")
        for s in LEDState:
            mgr.set_state(s)
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        mgr.stop()
