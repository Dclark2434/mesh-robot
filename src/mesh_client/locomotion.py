import math
import copy
import time
import json
from mesh_common.logging import get_logger
from mesh_client.servo_controller import ServoController

logger = get_logger("mesh_locomotion")

class LocomotionController:
    def __init__(self, servo_ctrl: ServoController, imu=None):
        self.servo = servo_ctrl
        self.imu = imu
        self.body_height = -60 # Adjusted to -60 for Tall/Swagger Stance (Prev: -35)
        # Body and Leg geometry (from Freenove control.py)
        # Note: These values are specific to the Freenove Big Hexapod
        self.body_points = [
            [137.1, 189.4, self.body_height], [225, 0, self.body_height], [137.1, -189.4, self.body_height],
            [-137.1, -189.4, self.body_height], [-225, 0, self.body_height], [-137.1, 189.4, self.body_height]
        ]
        # Calibration defaults from point.txt (140, 0, 0)
        self.calibration_leg_positions = [[140, 0, 0] for _ in range(6)]
        self.leg_positions = [[140, 0, 0] for _ in range(6)]
        self.calibration_angles = [[0, 0, 0] for _ in range(6)]
        self.current_angles = [[90, 0, 0] for _ in range(6)]
        
        # Dynamic Gait Parameters (Traction Control)
        self.body_pitch = 0.0 # Lean Forward/Back (Degrees)
        
        # SLIP DETECTION STATE
        self.last_yaw = 0.0
        self.last_imu_time = time.time()
        self.slip_score = 0
        self.az_history = [0] * 10 # For vibration RMS
        
        # TRACTION GOVERNORS (Active Response)
        # Multipliers [0.0 - 1.0] that scale gait parameters when slipping
        self.traction_governors = {
            'stride': 1.0, # Scales XY offsets
            'speed': 1.0,  # Scales wait time (Inverse speed)
            'lift': 1.0    # Scales Z height
        }
        
        # Geometry constants
        self.l1 = 33
        self.l2 = 90
        self.l3 = 110

        self.load_calibration()
        self.calibrate()
        self.set_leg_angles()

    def load_calibration(self):
        """
        Attempts to load calibration data from 'point.txt'.
        Checks current directory first, then user home (where Freenove app might save it).
        """
        import os
        
        # Possible locations for point.txt. 
        # The Freenove app typically saves it in the directory where the script is running.
        cwd = os.getcwd()
        home = os.path.expanduser("~")
        
        candidates = [
            os.path.join(cwd, "point.txt"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "point.txt"), # src/mesh_client/
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "point.txt"), # REPO ROOT (mesh-robot/)
            os.path.join(home, "point.txt"),
            os.path.join(home, "Freenove_Big_Hexapod_Kit/Code/Server/point.txt")
        ]


        found_path = None
        for candidate in candidates:
            if os.path.exists(candidate):
                found_path = candidate
                break

        if found_path:
            logger.info(f"Loading calibration from: {found_path}")
        else:
            # Fallback: Recursive search in the Freenove codebase if it exists nearby
            logger.info("Searching for point.txt in known locations...")
            for root, dirs, files in os.walk(home):
                if "point.txt" in files:
                    found_path = os.path.join(root, "point.txt")
                    # Sanity check: is it the default one (all 140s)?
                    # Proceeding with auto-discovered calibration file.
                    logger.info(f"Auto-discovered calibration file: {found_path}")
                    break
        
        if found_path:
            try:
                with open(found_path, 'r') as f:
                    for i in range(6):
                        line = f.readline()
                        if not line: break
                        try:
                            # Freenove uses tab or space separation
                            parts = line.strip().split() 
                            if len(parts) >= 3:
                                x = float(parts[0])
                                y = float(parts[1])
                                z = float(parts[2])
                                self.calibration_leg_positions[i] = [x, y, z]
                                logger.debug(f"Leg {i+1} cal: {x}, {y}, {z}")
                        except ValueError:
                            logger.warn(f"Invalid calibration data on line {i+1}")
            except Exception as e:
                logger.error(f"Failed to read calibration file: {e}")
        else:
            logger.warning("No calibration file (point.txt) found. Using defaults (140, 0, 0).")

    def restrict_value(self, value, min_value, max_value):
        return max(min_value, min(value, max_value))

    def map_value(self, value, from_low, from_high, to_low, to_high):
        return (to_high - to_low) * (value - from_low) / (from_high - from_low) + to_low

    def coordinate_to_angle(self, x, y, z):
        l1, l2, l3 = self.l1, self.l2, self.l3
        try:
            a = math.pi / 2 - math.atan2(z, y)
            x_3 = 0
            x_4 = l1 * math.sin(a)
            x_5 = l1 * math.cos(a)
            l23 = math.sqrt((z - x_5) ** 2 + (y - x_4) ** 2 + (x - x_3) ** 2)
            
            w = self.restrict_value((x - x_3) / l23, -1, 1)
            v = self.restrict_value((l2 * l2 + l23 * l23 - l3 * l3) / (2 * l2 * l23), -1, 1)
            u = self.restrict_value((l2 ** 2 + l3 ** 2 - l23 ** 2) / (2 * l3 * l2), -1, 1)
            
            # Removed rounding for precision
            b = math.asin(w) - math.acos(v)
            c = math.pi - math.acos(u)
            
            return math.degrees(a), math.degrees(b), math.degrees(c)
        except Exception as e:
            # logger.error(f"IK Error: {e} for {x},{y},{z}") # Suppress error spam in hot loop
            return 90, 90, 90

    def calibrate(self):
        # Calculate offset angles based on calibration positions
        for i in range(6):
            self.calibration_angles[i][0], self.calibration_angles[i][1], self.calibration_angles[i][2] = self.coordinate_to_angle(
                -self.calibration_leg_positions[i][2], self.calibration_leg_positions[i][0], self.calibration_leg_positions[i][1])
        
        # Calculate current angles based on default leg positions
        for i in range(6):
            self.current_angles[i][0], self.current_angles[i][1], self.current_angles[i][2] = self.coordinate_to_angle(
                -self.leg_positions[i][2], self.leg_positions[i][0], self.leg_positions[i][1])
                
        for i in range(6):
            self.calibration_angles[i][0] = self.calibration_angles[i][0] - self.current_angles[i][0]
            self.calibration_angles[i][1] = self.calibration_angles[i][1] - self.current_angles[i][1]
            self.calibration_angles[i][2] = self.calibration_angles[i][2] - self.current_angles[i][2]

    def set_leg_angles(self):
        # Calculate target angles for all legs
        for i in range(6):
            self.current_angles[i][0], self.current_angles[i][1], self.current_angles[i][2] = self.coordinate_to_angle(
                -self.leg_positions[i][2], self.leg_positions[i][0], self.leg_positions[i][1])

        # Apply calibration and corrections
        # Freenove uses specific mapping logic for each leg's 3 servos
        final_angles = copy.deepcopy(self.current_angles)
        
        for i in range(3):
            # Legs 1-3 (Right side?)
            final_angles[i][0] = self.restrict_value(final_angles[i][0] + self.calibration_angles[i][0], 0, 180)
            final_angles[i][1] = self.restrict_value(90 - (final_angles[i][1] + self.calibration_angles[i][1]), 0, 180)
            final_angles[i][2] = self.restrict_value(final_angles[i][2] + self.calibration_angles[i][2], 0, 180)
            
            # Legs 4-6 (Left side?)
            final_angles[i + 3][0] = self.restrict_value(final_angles[i + 3][0] + self.calibration_angles[i + 3][0], 0, 180)
            final_angles[i + 3][1] = self.restrict_value(90 + final_angles[i + 3][1] + self.calibration_angles[i + 3][1], 0, 180)
            final_angles[i + 3][2] = self.restrict_value(180 - (final_angles[i + 3][2] + self.calibration_angles[i + 3][2]), 0, 180)

        # Map to Servo Channels (Based on Freenove layout)
        # TUNE #4: Channel Grouping & Phase Ordering
        # Order: Rear Legs (Drive) -> Front Legs (Steer/Drive) -> Mid Legs (Balance)
        # This ensures the critical push-off happens first in the I2C frame.
        
        # Group 1: Rear Legs (Leg 3 & Leg 4) - The "Engine"
        # Leg 3 (Right Rear)
        self.servo.set_angle(9, final_angles[2][0])
        self.servo.set_angle(8, final_angles[2][1])
        self.servo.set_angle(31, final_angles[2][2])
        # Leg 4 (Left Rear)
        self.servo.set_angle(22, final_angles[3][0])
        self.servo.set_angle(23, final_angles[3][1])
        self.servo.set_angle(27, final_angles[3][2])

        # Group 2: Front Legs (Leg 1 & Leg 6) - The "Steering"
        # Leg 1 (Right Front)
        self.servo.set_angle(15, final_angles[0][0])
        self.servo.set_angle(14, final_angles[0][1])
        self.servo.set_angle(13, final_angles[0][2])
        # Leg 6 (Left Front)
        self.servo.set_angle(16, final_angles[5][0])
        self.servo.set_angle(17, final_angles[5][1])
        self.servo.set_angle(18, final_angles[5][2])

        # Group 3: Middle Legs (Leg 2 & Leg 5) - The "Pivot"
        # Leg 2 (Right Mid)
        self.servo.set_angle(12, final_angles[1][0])
        self.servo.set_angle(11, final_angles[1][1])
        self.servo.set_angle(10, final_angles[1][2])
        # Leg 5 (Left Mid)
        self.servo.set_angle(19, final_angles[4][0])
        self.servo.set_angle(20, final_angles[4][1])
        self.servo.set_angle(21, final_angles[4][2])

    def transform_coordinates(self, points):
        # Hardcoded transform based on leg mounting angles
        # Leg 1 (54 deg)
        self.leg_positions[0][0] = points[0][0] * math.cos(54 * math.pi/180) + points[0][1] * math.sin(54 * math.pi/180) - 94
        self.leg_positions[0][1] = -points[0][0] * math.sin(54 * math.pi/180) + points[0][1] * math.cos(54 * math.pi/180)
        self.leg_positions[0][2] = points[0][2] - 14
        # Leg 2 (0 deg)
        self.leg_positions[1][0] = points[1][0] - 85
        self.leg_positions[1][1] = points[1][1]
        self.leg_positions[1][2] = points[1][2] - 14
        # Leg 3 (-54 deg)
        self.leg_positions[2][0] = points[2][0] * math.cos(-54 * math.pi/180) + points[2][1] * math.sin(-54 * math.pi/180) - 94
        self.leg_positions[2][1] = -points[2][0] * math.sin(-54 * math.pi/180) + points[2][1] * math.cos(-54 * math.pi/180)
        self.leg_positions[2][2] = points[2][2] - 14
        # Leg 4 (-126 deg)
        self.leg_positions[3][0] = points[3][0] * math.cos(-126 * math.pi/180) + points[3][1] * math.sin(-126 * math.pi/180) - 94
        self.leg_positions[3][1] = -points[3][0] * math.sin(-126 * math.pi/180) + points[3][1] * math.cos(-126 * math.pi/180)
        self.leg_positions[3][2] = points[3][2] - 14
        # Leg 5 (180 deg)
        self.leg_positions[4][0] = points[4][0] * math.cos(math.pi) + points[4][1] * math.sin(math.pi) - 85
        self.leg_positions[4][1] = -points[4][0] * math.sin(math.pi) + points[4][1] * math.cos(math.pi)
        self.leg_positions[4][2] = points[4][2] - 14
        # Leg 6 (126 deg)
        self.leg_positions[5][0] = points[5][0] * math.cos(126 * math.pi/180) + points[5][1] * math.sin(126 * math.pi/180) - 94
        self.leg_positions[5][1] = -points[5][0] * math.sin(126 * math.pi/180) + points[5][1] * math.cos(126 * math.pi/180)
        self.leg_positions[5][2] = points[5][2] - 14

    def execute_gait(self, x, y, angle, steps=4, speed=1.0, hip_swing=0.0):
        """Generic gait execution wrapper."""
        # Dynamic Z-Step (User Req: Low Z at high speed for efficiency/traction)
        # Slow (0.5) -> 55mm
        # Fast (1.5) -> 35mm (Raised from 20mm to prevent dragging/skating)
        # Linear Interp
        z_step = max(35, 65 - (speed * 20))
        
        f_steps = 16 # Tuned: 12 was frantic, 16 is Fast/Controlled
        
        logger.info(f"Gait Cycle: x={x}, y={y}, angle={angle}, steps={steps}, speed={speed}, swing={hip_swing}, z_step={z_step:.1f}")
        self.reset_posture()
        for s in range(steps):
             self.run_one_cycle(x, y, angle, z_step, f_steps, speed, hip_swing)
        self.reset_posture()

    def move_forward(self, steps=5, speed=1.0):
        logger.info(f"Walking forward {steps} steps at speed {speed}...")
        # Add basic hip swing to forward walk (Yaw rotation)
        # Stride increased to 50mm for Sprint/Dynamic gait
        self.execute_gait(0, 65, 0, steps, speed=speed, hip_swing=5.0)

    def move_backward(self, steps=5, speed=1.0):
        logger.info(f"Walking backward {steps} steps at speed {speed}...")
        self.execute_gait(0, -50, 0, steps, speed=speed, hip_swing=5.0)

    def turn_left(self, steps=5, speed=1.0):
        logger.info(f"Turning left {steps} steps at speed {speed}...")
        # Adjusted: -10 degrees for Left
        self.execute_gait(0, 0, -10, steps, speed=speed)

    def turn_right(self, steps=5, speed=1.0):
        logger.info(f"Turning right {steps} steps at speed {speed}...")
        # Adjusted: 10 degrees for Right
        self.execute_gait(0, 0, 10, steps, speed=speed)

    def reset_posture_flat(self):
        """
        Forces the robot into the 'Installation/Flat' posture.
        Derived from Freenove 'servo.py' logic.
        """
        logger.info("Resetting posture to FLAT (Installation Mode)...")
        for i in range(32):
            angle = 90
            if i in [10, 13, 31]:
                angle = 10
            elif i in [18, 21, 27]:
                angle = 170
            
            # Direct servo control via the injected servo_controller
            self.servo.set_angle(i, angle)

    def reset_posture(self):
         self.body_points = [
            [137.1, 189.4, self.body_height], [225, 0, self.body_height], [137.1, -189.4, self.body_height],
            [-137.1, -189.4, self.body_height], [-225, 0, self.body_height], [-137.1, 189.4, self.body_height]
        ]
         self.transform_coordinates(self.body_points)
         self.set_leg_angles()

    def detect_slip(self, requested_speed):
        """
        Physics-based slip detection and active response.
        called inside the gait loop.
        """
        # Abort if no IMU or if IMU is in Mock Mode (Data is fake)
        if not self.imu or getattr(self.imu, 'mock_mode', False): return

        # 1. Read Sensors
        accel = self.imu.read_accel_raw() # {'x':, 'y':, 'z':}
        r, p, yaw = self.imu.read_orientation()
        now = time.time()
        dt = now - self.last_imu_time
        if dt <= 0: return # Prevent div zero
        
        # 2. Compute Metrics
        
        # A) Forward Coupling (Ax / Expected)
        # We assume Y is Forward (based on transforms)
        # Expected Accel roughly prop to Speed? 
        # Actually measure if we are accelerating at all when moving.
        # Simple Logic: If Speed > 0.5, we expect |Ay| > 0.1G consistently?
        # User Logic: measured / expected.
        # Let's say expected_accel ~ speed * 0.5G (rough heuristic)
        # Using abs() because gait oscillates +-.
        # FIXED SCALE: Driver returns m/s^2. Convert to Gs.
        measured_ax = accel['y'] / 9.8 
        
        coupling_score = 1.0
        if requested_speed > 0.5:
             # If we are commanding speed, we expect some forward force.
             # If Ay is near zero, we might be wheel-spinning (slipping).
             # But gait is cyclic. Ay goes + and -.
             # Simple metric: Activity Level.
             pass 
             
        # B) Yaw Instability (Side Slip)
        yaw_rate = (yaw - self.last_yaw) / dt
        # Wrap yaw? (0-360 issue). If jump > 180, adjust.
        if abs(yaw - self.last_yaw) > 180:
             yaw_rate = 0 # Ignore wrap-around frame
        
        self.last_yaw = yaw
        self.last_imu_time = now
        
        # C) Vibration (Z-axis RMS)
        # Micro-slip causes chatter
        az = accel['z'] / 9.8
        self.az_history.pop(0)
        self.az_history.append(az)
        avg_az = sum(self.az_history) / len(self.az_history)
        # FIXED MATH: Divide by N before Sqrt for std dev
        vibration = (sum([(x - avg_az)**2 for x in self.az_history]) / len(self.az_history)) ** 0.5
        
        # 3. Calculate Slip Score
        current_slip = 0
        
        # Check thresholds
        YAW_THRESH = 10.0 # deg/sec (tuned high to ignore normal sway)
        VIBE_THRESH = 0.2 # G (Moderate chatter check)
        
        if abs(yaw_rate) > YAW_THRESH:
             current_slip += 1
        
        if vibration > VIBE_THRESH:
             current_slip += 1
             
        # Forward Coupling Check (Experimental)
        # If moving fast but Ay is low?
        # FIXED: Constant velocity = 0 accel. 
        # But legged gait has constant accel/decel cycles.
        # If |Ax| < threshold, it means we are "floating" or sliding smoothly?
        # Let's Log it to debug "Skating"
        if requested_speed > 1.0 and abs(measured_ax) < 0.05:
             current_slip += 1 
             
        if current_slip >= 2:
             self.slip_score += 1
        else:
             self.slip_score = max(0, self.slip_score - 1)
             
        # DEBUG: Print metrics to diagnose "Skating"
        if self.slip_score > 0 or current_slip > 0:
            logger.info(f"SLIP: Score={self.slip_score} Cur={current_slip} | Ax={measured_ax:.3f} YawRate={yaw_rate:.1f} Vibe={vibration:.2f} | Gov: {self.traction_governors['stride']:.2f}")
             
        # 4. Active Response (Traction Control)
        # SENSITIVITY INCREASE: Trigger on 1 check (Instant reaction)
        if self.slip_score >= 1:
             # SLIP DETECTED -> THROTTLE DOWN
             self.traction_governors['stride'] = max(0.5, self.traction_governors['stride'] * 0.85)
             self.traction_governors['speed'] = max(0.5, self.traction_governors['speed'] * 0.8) # Slower gait
             # LOW LIFT CAUSES DRAG. REMOVED.
             # self.traction_governors['lift'] = max(0.3, self.traction_governors['lift'] * 0.7) 
        else:
             # RECOVER (Slowly)
             self.traction_governors['stride'] = min(1.0, self.traction_governors['stride'] * 1.05)
             self.traction_governors['speed'] = min(1.0, self.traction_governors['speed'] * 1.02)
             # self.traction_governors['lift'] = min(1.0, self.traction_governors['lift'] * 1.05)

    def run_one_cycle(self, x, y, angle, Z, F, speed=1.0, swing_amp=0.0):
        # Port of 'run_gait' logic for Mode 1 (Ripple)
        
        # --- TRACTION CONTROL CHECK ---
        # Run every few ticks to save I2C bandwidth
        self.detect_slip(speed)
        
        # Apply Governors
        eff_stride = self.traction_governors['stride']
        eff_speed  = self.traction_governors['speed']
        eff_lift   = self.traction_governors['lift']
        
        # Apply active response to parameters
        eff_x = x * eff_stride
        eff_y = y * eff_stride
        eff_Z = Z * eff_lift
        
        # Assuming constants for F (Resolution)
        z = eff_Z / F
        # Default delay was 0.01. Increase speed by dividing delay.
        # Effective speed reduces delay divisor -> Increases delay -> Slower gait
        delay = 0.01 / max(0.1, speed * eff_speed)
        
        points = copy.deepcopy(self.body_points)
        
        # --- PHYSICS CORRECTION: Apply Body Pitch FIRST ---
        # Apply adjustment to the BASE body points (neutral stance)
        # effectively rotating the "Hips" before any legs move.
        if abs(self.body_pitch) > 0.1:
            pitch_rad = math.radians(self.body_pitch)
            cos_p = math.cos(pitch_rad)
            sin_p = math.sin(pitch_rad)
            for i in range(6):
                y_val = points[i][1]
                z_val = points[i][2]
                points[i][1] = y_val * cos_p - z_val * sin_p
                points[i][2] = y_val * sin_p + z_val * cos_p

        xy = [[0, 0] for _ in range(6)]
        
        # Calculate step offsets (Using Governor-scaled X/Y)
        for i in range(6):
            xy[i][0] = ((points[i][0] * math.cos(angle * math.pi / 180) + points[i][1] * math.sin(angle * math.pi / 180) - points[i][0]) + eff_x) / F
            xy[i][1] = ((-points[i][0] * math.sin(angle * math.pi / 180) + points[i][1] * math.cos(angle * math.pi / 180) - points[i][1]) + eff_y) / F

        # Removing Log from loop
        # logger.info(f"Gait XY Offset: {xy[0]}")

        # Execute Ripple Gait Cycle
        for j in range(F):
            # 1. Update Gait State (Persistent)
            for i in range(3):
                # Leg pair operations
                if j < (F / 8):
                    points[2 * i][0] -= 4 * xy[2 * i][0]
                    points[2 * i][1] -= 4 * xy[2 * i][1]
                    points[2 * i + 1][0] += 8 * xy[2 * i + 1][0]
                    points[2 * i + 1][1] += 8 * xy[2 * i + 1][1]
                    points[2 * i + 1][2] = eff_Z + self.body_height # Use governed Lift
                elif j < (F / 4):
                    points[2 * i][0] -= 4 * xy[2 * i][0]
                    points[2 * i][1] -= 4 * xy[2 * i][1]
                    points[2 * i + 1][2] -= z * 8
                elif j < (3 * F / 8):
                    points[2 * i][2] += z * 8
                    points[2 * i + 1][0] -= 4 * xy[2 * i + 1][0]
                    points[2 * i + 1][1] -= 4 * xy[2 * i + 1][1]
                elif j < (5 * F / 8):
                    points[2 * i][0] += 8 * xy[2 * i][0]
                    points[2 * i][1] += 8 * xy[2 * i][1]
                    points[2 * i + 1][0] -= 4 * xy[2 * i + 1][0]
                    points[2 * i + 1][1] -= 4 * xy[2 * i + 1][1]
                elif j < (3 * F / 4):
                    points[2 * i][2] -= z * 8
                    points[2 * i + 1][0] -= 4 * xy[2 * i + 1][0]
                    points[2 * i + 1][1] -= 4 * xy[2 * i + 1][1]
                elif j < (7 * F / 8):
                    points[2 * i][0] -= 4 * xy[2 * i][0]
                    points[2 * i][1] -= 4 * xy[2 * i][1]
                    points[2 * i + 1][2] += z * 8
                elif j < F:
                    points[2 * i][0] -= 4 * xy[2 * i][0]
                    points[2 * i][1] -= 4 * xy[2 * i][1]
                    points[2 * i + 1][0] += 8 * xy[2 * i + 1][0]
                    points[2 * i + 1][1] += 8 * xy[2 * i + 1][1]

            # 2. Apply Hip Swing (Body Yaw) to Temporary Copy
            current_points = copy.deepcopy(points)
            
            # Hip Swing: Body Yaw (Rotation around Z)
            swing_angle_rad = 0
            if swing_amp != 0:
                swing_phase = (j / F) * 2 * math.pi
                swing_angle_rad = math.radians(math.sin(swing_phase) * swing_amp)
                
            if swing_angle_rad != 0:
                 cos_a = math.cos(swing_angle_rad)
                 sin_a = math.sin(swing_angle_rad)
                 for i in range(6):
                     # Rotate X,Y around 0,0 (Body Center)
                     x_val = current_points[i][0]
                     y_val = current_points[i][1]
                     current_points[i][0] = x_val * cos_a - y_val * sin_a
                     current_points[i][1] = x_val * sin_a + y_val * cos_a

            self.transform_coordinates(current_points)
            self.set_leg_angles()
            time.sleep(delay)

