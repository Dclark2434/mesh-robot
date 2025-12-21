import time
import math
import copy
from mesh_common.logging import get_logger

logger = get_logger("mesh_anim")

class AnimationController:
    def __init__(self, locomotion):
        self.loco = locomotion
        self.is_animating = False

    def _interpolate(self, start_val, end_val, progress):
        """Standard linear interpolation."""
        return start_val + (end_val - start_val) * progress

    def smooth_move(self, target_offsets, duration=1.0, steps=10):
        """
        Safely moves from current posture to target posture offsets.
        target_offsets: list of [x,y,z] offsets for each of 6 legs.
        duration: time in seconds.
        """
        start_points = copy.deepcopy(self.loco.body_points)
        # Calculate target absolute points based on offsets relative to NEUTRAL
        # Neutral is defined in Loco as standard body_points
        # But wait, Loco stores CURRENT state in body_points.
        # So we drift FROM current TO target? 
        # Safety: We should probably interpolate relative to the CURRENT state.
        
        # Actually, simpler: Let's define animations as sequences of BODY POSTURES.
        # But Loco.body_points are the coord system.
        
        delay = duration / steps
        
        for i in range(steps):
            progress = (i + 1) / steps
            # Create a blended frame?
            # This is complex to do generically for all 6 legs without a target state.
            # Simplified approach: Just execute small incremental moves if we had a move_to(x,y,z).
            # But Loco doesn't have a clean move_to_absolute yet.
            pass
        
    def reset_neutral(self):
        """Returns to the calibrated neutral stance safely."""
        logger.info("Resetting to Neutral...")
        # Neutral defined as:
        # body_height = -25?
        # offsets = 0
        self.loco.reset_posture()

    def palp_wiggle(self):
        """
        Idle Animation: Lifting front legs (0 and 5) and waving them.
        Safety: Very small movements.
        """
        logger.info("Animation: Palp Wiggle")
        self.is_animating = True
        
        # Save current state
        original = copy.deepcopy(self.loco.body_points)
        
        # Lift front legs slightly (Z + 20)
        # Leg 0 (Right Front), Leg 5 (Left Front)? 
        # Based on transform_coordinates in Loco:
        # Leg 0 is roughly 54 deg (Front Right)
        # Leg 5 is roughly 126 deg (Front Left) -> No wait, 126 is Back Left?
        # Let's verify leg mapping.
        # Freenove: 
        # 0: Front Right
        # 1: Mid Right
        # 2: Back Right
        # 3: Back Left
        # 4: Mid Left
        # 5: Front Left
        
        front_legs = [0, 5]
        all_legs = [0, 1, 2, 3, 4, 5]

        # 0. Shift Body Backward (Move feet +Y) for stability
        # Hexapod balance: By shifting body back, CoM moves over the rear 4 legs.
        shift_back_amount = 30 # mm
        
        current = copy.deepcopy(self.loco.body_points)
        for _ in range(5):
             for leg in all_legs:
                 current[leg][1] += (shift_back_amount / 5)
             self.loco.transform_coordinates(current)
             self.loco.set_leg_angles()
             time.sleep(0.05)
        
        # 1. Lift Up & Tuck In (Closer to face)
        # Tuck: Leg 0 (Right) moves -X, Leg 5 (Left) moves +X
        tuct_amount = 30 # mm inward
        
        for _ in range(5):
            # Lift
            for leg in front_legs:
                current[leg][2] += 5 # Lift 5mm
            
            # Tuck
            current[0][0] -= (tuct_amount / 5) # Right leg moves Left
            current[5][0] += (tuct_amount / 5) # Left leg moves Right
            
            self.loco.transform_coordinates(current)
            self.loco.set_leg_angles()
            time.sleep(0.05)
            
        # 2. Wiggle (Forward/Back)
        for _ in range(25): # ~5 seconds
            # Forward
            for leg in front_legs:
                current[leg][1] += 10 # Move Y (Forward)
            self.loco.transform_coordinates(current)
            self.loco.set_leg_angles()
            time.sleep(0.1)
            
            # Backward
            for leg in front_legs:
                current[leg][1] -= 10 # Move Y (Back)
            self.loco.transform_coordinates(current)
            self.loco.set_leg_angles()
            time.sleep(0.1)

        # 3. Return to ground (using reset_posture for safety)
        self.reset_neutral()
        self.is_animating = False

    def laugh(self):
        """Rapid pitch changes."""
        logger.info("Animation: Laugh")
        self.is_animating = True
        
        # Pitch Up/Down 3 times
        # We can implement this by adjusting body_points Z for front vs back legs
        
        current = copy.deepcopy(self.loco.body_points)
        front_legs = [0, 5]
        back_legs = [2, 3]
        
        for _ in range(3):
            # Up
            for i in front_legs: current[i][2] += 15
            for i in back_legs: current[i][2] -= 15
            self.loco.transform_coordinates(current)
            self.loco.set_leg_angles()
            time.sleep(0.1)
            
            # Down
            for i in front_legs: current[i][2] -= 15
            for i in back_legs: current[i][2] += 15
            self.loco.transform_coordinates(current)
            self.loco.set_leg_angles()
            time.sleep(0.1)
            
        self.reset_neutral()
        self.is_animating = False

    def bow(self):
        """Deep pitch forward."""
        logger.info("Animation: Bow")
        self.is_animating = True
        
        current = copy.deepcopy(self.loco.body_points)
        front_legs = [0, 5]
        
        # Slow bow down
        steps = 10
        for _ in range(steps):
            for i in front_legs:
                current[i][2] -= 3 # Drop front legs 30mm total
            
            self.loco.transform_coordinates(current)
            self.loco.set_leg_angles()
            time.sleep(0.05)
            
        time.sleep(0.5) # Hold
        
        # Return
        self.reset_neutral()
        self.is_animating = False
