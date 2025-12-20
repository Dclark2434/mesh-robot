import math
import copy
import time
import json
from mesh_common.logging import get_logger
from mesh_client.servo_controller import ServoController

logger = get_logger("mesh_locomotion")

class LocomotionController:
    def __init__(self, servo_ctrl: ServoController):
        self.servo = servo_ctrl
        self.body_height = -25
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
        
        # Geometry constants
        self.l1 = 33
        self.l2 = 90
        self.l3 = 110

        self.calibrate()
        self.set_leg_angles()

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
            
            b = math.asin(round(w, 2)) - math.acos(round(v, 2))
            c = math.pi - math.acos(round(u, 2))
            
            return round(math.degrees(a)), round(math.degrees(b)), round(math.degrees(c))
        except Exception as e:
            logger.error(f"IK Error: {e} for {x},{y},{z}")
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
        # Leg 1
        logger.info(f"[IK] Leg 1 Angles: {final_angles[0]}")
        self.servo.set_angle(15, final_angles[0][0])
        self.servo.set_angle(14, final_angles[0][1])
        self.servo.set_angle(13, final_angles[0][2])
        # Leg 2
        self.servo.set_angle(12, final_angles[1][0])
        self.servo.set_angle(11, final_angles[1][1])
        self.servo.set_angle(10, final_angles[1][2])
        # Leg 3
        self.servo.set_angle(9, final_angles[2][0])
        self.servo.set_angle(8, final_angles[2][1])
        self.servo.set_angle(31, final_angles[2][2])
        # Leg 6
        self.servo.set_angle(16, final_angles[5][0])
        self.servo.set_angle(17, final_angles[5][1])
        self.servo.set_angle(18, final_angles[5][2])
        # Leg 5
        self.servo.set_angle(19, final_angles[4][0])
        self.servo.set_angle(20, final_angles[4][1])
        self.servo.set_angle(21, final_angles[4][2])
        # Leg 4
        self.servo.set_angle(22, final_angles[3][0])
        self.servo.set_angle(23, final_angles[3][1])
        self.servo.set_angle(27, final_angles[3][2])

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

    def move_forward(self, steps=5):
        logger.info(f"Walking forward {steps} steps...")
        # Gait parameters for moving Forward
        # x=25, y=0, z=0
        # Gait '1' = Ripple, '2' = Tripod? default to tripod logic
        
        # Simulating "run_gait" with hardcoded forward parameters
        # x=25 (forward speed), y=0, angle=0
        x = 0
        y = 25
        angle = 0
        z_step = 30 # Reduced Step height for stability
        f_steps = 32 # Faster cycle for testing
        
        logger.info(f"Starting gait cycle: {steps} steps, Z={z_step}, F={f_steps}")

        # Ensure we start from a clean posture
        self.reset_posture()
        
        # We need to run the cycle 'steps' times
        for s in range(steps):
             logger.info(f"Step {s+1}/{steps}...")
             self.run_one_cycle(x, y, angle, z_step, f_steps)
            
        # Return to neutral
        self.reset_posture()
        logger.info("Gait complete.")

    def reset_posture(self):
         self.body_points = [
            [137.1, 189.4, self.body_height], [225, 0, self.body_height], [137.1, -189.4, self.body_height],
            [-137.1, -189.4, self.body_height], [-225, 0, self.body_height], [-137.1, 189.4, self.body_height]
        ]
         self.transform_coordinates(self.body_points)
         self.set_leg_angles()

    def run_one_cycle(self, x, y, angle, Z, F):
        # Port of 'run_gait' logic for Mode 1 (Ripple)
        # Assuming constants for F (Resolution)
        z = Z / F
        delay = 0.01
        
        points = copy.deepcopy(self.body_points)
        xy = [[0, 0] for _ in range(6)]
        
        # Calculate step offsets
        for i in range(6):
            xy[i][0] = ((points[i][0] * math.cos(angle * math.pi / 180) + points[i][1] * math.sin(angle * math.pi / 180) - points[i][0]) + x) / F
            xy[i][1] = ((-points[i][0] * math.sin(angle * math.pi / 180) + points[i][1] * math.cos(angle * math.pi / 180) - points[i][1]) + y) / F

        logger.info(f"Gait XY Offset: {xy[0]}")

        # Execute Ripple Gait Cycle
        for j in range(F):
            for i in range(3):
                # Leg pair operations
                if j < (F / 8):
                    points[2 * i][0] -= 4 * xy[2 * i][0]
                    points[2 * i][1] -= 4 * xy[2 * i][1]
                    points[2 * i + 1][0] += 8 * xy[2 * i + 1][0]
                    points[2 * i + 1][1] += 8 * xy[2 * i + 1][1]
                    points[2 * i + 1][2] = Z + self.body_height
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

            self.transform_coordinates(points)
            self.set_leg_angles()
            time.sleep(delay)

