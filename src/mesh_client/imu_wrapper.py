import sys
import os
import time
from mesh_common.logging import get_logger

logger = get_logger("mesh_imu")

# Dynamic import of Freenove IMU code
# Located at: REPO_ROOT/freenove_code/Code/Server/imu.py
# We are at:  REPO_ROOT/src/mesh_client/imu_wrapper.py

try:
    # Build absolute path to 'Server' directory
    current_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
    freenove_server_dir = os.path.join(repo_root, "freenove_code", "Code", "Server")
    
    if os.path.exists(freenove_server_dir):
        sys.path.append(freenove_server_dir)
        # Import the class 'IMU' from 'imu.py'
        from imu import IMU 
        IMU_AVAILABLE = True
    else:
        logger.warning(f"Freenove IMU directory not found at: {freenove_server_dir}")
        IMU_AVAILABLE = False

except ImportError as e:
    logger.error(f"Failed to import Freenove IMU Module: {e}")
    # Likely missing 'smbus' or other dependency inside imu.py
    import traceback
    logger.error(traceback.format_exc())
    IMU_AVAILABLE = False
except Exception as e:
    logger.error(f"Unexpected IMU setup error: {e}")
    import traceback
    logger.error(traceback.format_exc())
    IMU_AVAILABLE = False

class IMUWrapper:
    def __init__(self):
        self.sensor = None
        self.mock_mode = not IMU_AVAILABLE
        
        if IMU_AVAILABLE:
            try:
                self.sensor = IMU()
                # Run one update to warm up filters
                self.sensor.update_imu_state()
                # DEBUG UNITS:
                raw_acc = self.sensor.sensor.get_accel_data()
                logger.info(f"IMU Initialized. Raw Sample: {raw_acc}")
                logger.info("IMU Initialized (MPU6050 via Freenove Driver)")
            except Exception as e:
                logger.error(f"IMU Init Failed: {e}. Falling back to Mock.")
                self.mock_mode = True
        else:
            logger.info("IMU Wrapper started in Mock Mode (No Hardware/Driver)")

    def read_orientation(self):
        """
        Returns (roll, pitch, yaw) in degrees.
        """
        if self.mock_mode:
            return 0.0, 0.0, 0.0
            
        try:
            # Freenove 'update_imu_state' returns (roll, pitch, yaw)
            # but usually prints it? checking code...
            # line 128: return self.pitch_angle, self.roll_angle, self.yaw_angle
            # Wait, signature is: return self.pitch_angle, self.roll_angle, self.yaw_angle
            # Ordering in code: return pitch, roll, yaw
            # Ordering in __main__: roll, pitch, yaw = update()
            # Let's trust the return statement inside the class method over usage.
            # line 128: return self.pitch_angle, self.roll_angle, self.yaw_angle
            pitch, roll, yaw = self.sensor.update_imu_state()
            return roll, pitch, yaw
        except Exception as e:
            # logger.warning(f"IMU Read Error: {e}") # Reduce spam
            return 0.0, 0.0, 0.0
            
    def read_accel_raw(self):
        """
        Returns raw Dictionary {'x':..., 'y':..., 'z':...} from sensor.
        Used for slip detection (jerk).
        """
        if self.mock_mode:
            return {'x':0, 'y':0, 'z':0}
        
        try:
            return self.sensor.sensor.get_accel_data()
        except:
            return {'x':0, 'y':0, 'z':0}
