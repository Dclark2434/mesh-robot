import time
try:
    from smbus2 import SMBus
except ImportError:
    SMBus = None

from mesh_common.logging import get_logger
from mesh_common.hardware import RobotHardware, DummyServo

logger = get_logger("mesh_servo")

def map_value(value, from_low, from_high, to_low, to_high):
    return (to_high - to_low) * (value - from_low) / (from_high - from_low) + to_low

class PCA9685:
    def __init__(self, address=0x40):
        self.address = address
        if SMBus:
            self.bus = SMBus(1)
            self.write(0x00, 0x00) # MODE1
        else:
            self.bus = None

    def write(self, reg, value):
        if self.bus:
            try:
                self.bus.write_byte_data(self.address, reg, value)
            except Exception as e:
                logger.error(f"I2C Write Error [Addr: 0x{self.address:02x}, Reg: 0x{reg:02x}]: {e}")

    def read(self, reg):
        if self.bus:
            try:
                return self.bus.read_byte_data(self.address, reg)
            except Exception as e:
                logger.error(f"I2C Read Error [Addr: 0x{self.address:02x}, Reg: 0x{reg:02x}]: {e}")
        return 0

    def set_pwm_freq(self, freq):
        if not self.bus: return
        prescaleval = 25000000.0 / 4096.0 / float(freq) - 1.0
        prescale = int(prescaleval + 0.5)
        oldmode = self.read(0x00)
        newmode = (oldmode & 0x7F) | 0x10 # sleep
        self.write(0x00, newmode)
        self.write(0xFE, prescale) # PRESCALE
        self.write(0x00, oldmode)
        time.sleep(0.005)
        self.write(0x00, oldmode | 0x80)

    def set_pwm(self, channel, on, off):
        if not self.bus: return
        self.write(0x06 + 4 * channel, on & 0xFF)
        self.write(0x07 + 4 * channel, on >> 8)
        self.write(0x08 + 4 * channel, off & 0xFF)
        self.write(0x09 + 4 * channel, off >> 8)

class ServoController(RobotHardware):
    def __init__(self):
        super().__init__()
        self.pwm40 = None
        self.pwm41 = None
        
        if self.is_rpi and SMBus:
            try:
                # Freenove Big Hexapod uses two PCA9685 boards:
                # 0x41 handles channels 0-15 (Head and some legs)
                # 0x40 handles channels 16-31 (The rest of the legs)
                self.pwm41 = PCA9685(0x41)
                self.pwm41.set_pwm_freq(50)
                
                self.pwm40 = PCA9685(0x40)
                self.pwm40.set_pwm_freq(50)

                # Enable Servo Power (GPIO 4 low = Enable?)
                # Freenove uses: OutputDevice(4).off() to enable.
                try:
                    from gpiozero import OutputDevice
                    self.power_pin = OutputDevice(4)
                    self.power_pin.off() # Enable power
                    logger.info("Servo power enabled (GPIO 4 low).")
                except ImportError:
                    logger.warning("gpiozero not found, cannot toggle servo power pin.")
                except Exception as e:
                    logger.warning(f"Failed to control servo power pin: {e}")
                
                # Keep track of current angles
                self.angles = {} 
                logger.info("PCA9685 boards (0x40, 0x41) successfully initialized via smbus2.")
            except Exception as e:
                logger.error(f"Failed to init PCA9685: {e}")
                import traceback
                logger.error(traceback.format_exc())
                self.pwm41 = DummyServo()
                self.pwm40 = DummyServo()
        else:
            if not self.is_rpi:
                logger.info("Not on Raspberry Pi. Using Dummy Servo.")
            elif not SMBus:
                logger.error("smbus2 module not found! Head movement will be disabled. Run 'pip install smbus2'.")
            
            self.pwm41 = DummyServo()
            self.pwm40 = DummyServo()
        
        # Always initialize angles map
        self.angles = {}

    def _ensure_power(self):
        """Checks if servo power is enabled, and enables if not."""
        # Using a simple attribute to track state to avoid excessive GPIO calls
        # Assuming power is enabled on init.
        # But we can just force it ON if we recently relaxed.
        if hasattr(self, 'power_pin') and self.power_pin:
             # If we are using gpiozero, check value? 
             # OutputDevice.value is 1 if on, 0 if off.
             # Logic: off() -> Enable (Low Active). on() -> Disable (High Active).
             if self.power_pin.value == 1: # It is currently High (OFF)
                 logger.info("Auto-Enabling Servo Power...")
                 self.power_pin.off() # Enable
                 time.sleep(0.05) # Stabilize

    def set_angle(self, channel, angle):
        self._ensure_power() # Wake up if needed
        
        angle = max(0, min(180, angle))
        
        # Determine which board to use based on Freenove logic
        if channel < 16:
            target_pwm = self.pwm41
            target_channel = channel
        else:
            target_pwm = self.pwm40
            target_channel = channel - 16

        if isinstance(target_pwm, DummyServo):
            target_pwm.set_angle(channel, angle)
            return

        # Map 0-180 to 500-2500us pulse, then to 0-4095 (12-bit)
        duty_cycle = map_value(angle, 0, 180, 500, 2500)
        off_count = int(map_value(duty_cycle, 0, 20000, 0, 4095))
        
        if channel in [0, 8, 16] or logger.level <= 10: 
             logger.info(f"[SERVO] Ch {channel} -> {angle} deg -> PWM {off_count}")
             
        target_pwm.set_pwm(target_channel, 0, off_count)
        self.angles[channel] = angle

    def relax(self):
        # Relax both boards
        logger.info("Relaxing Servos (Cutting PWM & Power)...")
        for board in [self.pwm41, self.pwm40]:
            if isinstance(board, DummyServo):
                board.relax()
            else:
                for i in range(16):
                    board.set_pwm(i, 0, 4096)
        
        # Cut Power Rail (GPIO 4 High)
        if hasattr(self, 'power_pin') and self.power_pin:
            self.power_pin.on() # Disable Power
            logger.info("Servo Power Rail Disabled.")

class HeadController:
    """Specialized controller for the Hexapod head servos."""
    def __init__(self, servo_ctrl: ServoController):
        self.ctrl = servo_ctrl
        self.tilt_channel = 0
        self.pan_channel = 1
        self.neutral_tilt = 90
        self.neutral_pan = 90
        
    def look_neutral(self):
        self.ctrl.set_angle(self.pan_channel, self.neutral_pan)
        self.ctrl.set_angle(self.tilt_channel, self.neutral_tilt)

    def look_up(self, degrees=20):
        self.ctrl.set_angle(self.tilt_channel, self.neutral_tilt + degrees)

    def look_down(self, degrees=20):
        # Cap downward gaze to avoid hitting chest
        degrees = min(degrees, 30)
        self.ctrl.set_angle(self.tilt_channel, self.neutral_tilt - degrees)

    def look_left(self, degrees=30):
        self.ctrl.set_angle(self.pan_channel, self.neutral_pan + degrees)

    def look_right(self, degrees=30):
        self.ctrl.set_angle(self.pan_channel, self.neutral_pan - degrees)

    def look_at(self, pan_offset, tilt_offset):
        """Moves head to specific offsets from neutral. Positive Tilt = UP, Positive Pan = LEFT."""
        # Sanity check ranges
        tilt_target = self.neutral_tilt + tilt_offset
        pan_target = self.neutral_pan + pan_offset
        
        # Clamp loosely (servos usually 0-180)
        tilt_target = max(30, min(150, tilt_target)) # Don't hit body (30)
        pan_target = max(0, min(180, pan_target))
        
        self.ctrl.set_angle(self.pan_channel, pan_target)
        self.ctrl.set_angle(self.tilt_channel, tilt_target)

if __name__ == "__main__":
    sc = ServoController()
    hc = HeadController(sc)
    print("Testing head movement...")
    hc.look_neutral()
    time.sleep(1)
    hc.look_up(25)
    time.sleep(2)
    hc.look_neutral()
    print("Done.")
