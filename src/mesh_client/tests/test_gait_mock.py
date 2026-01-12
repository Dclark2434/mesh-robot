import time
import sys
import unittest
from unittest.mock import MagicMock

# Mock imports to run without hardware deps
sys.modules['mesh_client.servo_controller'] = MagicMock()
from mesh_client.locomotion import LocomotionController

class MockServoController:
    def set_angle(self, channel, angle):
        pass

class TestGaitSpeed(unittest.TestCase):
    def setUp(self):
        self.servo = MockServoController()
        # Suppress logging for clean output
        import logging
    def setUp(self):
        self.servo = MockServoController()
        # Suppress logging for clean output
        import logging
        logging.getLogger("mesh_locomotion").setLevel(logging.CRITICAL)
        
        # Patch load_calibration to prevent file system scan/hangs
        p = unittest.mock.patch.object(LocomotionController, 'load_calibration')
        self.addCleanup(p.stop)
        p.start()
        
        self.loco = LocomotionController(self.servo)

    def test_speed_increase(self):
        """Verify that speed factor reduces execution time"""
        print("\nTesting Speed 1.0 vs Speed 2.0...")
        
        # Speed 1.0
        start = time.time()
        self.loco.execute_gait(0, 25, 0, steps=1, speed=1.0)
        duration_1 = time.time() - start
        print(f"Speed 1.0 Duration: {duration_1:.4f}s")

        # Speed 2.0
        start = time.time()
        self.loco.execute_gait(0, 25, 0, steps=1, speed=2.0)
        duration_2 = time.time() - start
        print(f"Speed 2.0 Duration: {duration_2:.4f}s")
        
        # Expect speed 2.0 to be roughly half the time (twice as fast)
        # Allow some overhead margin
        self.assertLess(duration_2, duration_1 * 0.7)
        print("Speed sanity check passed.")

    def test_hip_swing_logic(self):
        """Verify hip swing parameter runs without error"""
        print("\nTesting Hip Swing execution...")
        try:
            self.loco.execute_gait(0, 25, 0, steps=1, speed=5.0, hip_swing=15.0)
            print("Hip swing execution successful.")
        except Exception as e:
            self.fail(f"Hip swing raised exception: {e}")

if __name__ == '__main__':
    unittest.main()
