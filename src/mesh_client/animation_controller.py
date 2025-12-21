import time
import math
import copy
from mesh_common.logging import get_logger

logger = get_logger("mesh_anim")


class AnimationController:
    def __init__(self, locomotion, head_controller=None):
        self.loco = locomotion
        self.head = head_controller
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
        Idle Animation: Mantis/Spider Display.
        1. Look UP.
        2. Shift Mid Legs Forward (Support).
        3. Lift Front Legs HIGH & Tuck.
        4. Wiggle "Mandibles" (Anti-phase).
        """
        logger.info("Animation: Palp Wiggle (Mantis Mode)")
        self.is_animating = True
        
        # 0. Head Up (Check logs if this fails)
        if self.head:
            logger.info("Head Up...")
            self.head.look_up(40)
            time.sleep(0.5)
        else:
            logger.warning("Head controller missing!")

        current = copy.deepcopy(self.loco.body_points)
        front_legs = [0, 5]     
        mid_legs = [1, 4]
        back_legs = [2, 3]

        # 1. Posture Up (Establish Support Base)
        steps = 15 # Slower setup
        
        # Support shifts
        shift_mid_fwd = 60 # mm (Move mid legs forward to take weight)
        shift_body_back = 40 # mm (Move other legs back effectively)
        
        # Mantis Lift
        lift_height = 90 # mm (VERY HIGH)
        tuck_in = 50 # mm (Overlap inward)
        
        for _ in range(steps):
             # Mid Legs Move Forward (+Y) and Out (+X) for stability
             for leg in mid_legs:
                 current[leg][1] += (shift_mid_fwd / steps)
                 current[leg][0] += (10 / steps) if leg == 1 else -(10 / steps)
                 
             # Back Legs Move Back (-Y? No, Body Back means support legs relative +Y)
             for leg in back_legs:
                 current[leg][1] += (shift_body_back / steps)

             # Lift & Tuck Front Legs
             for leg in front_legs:
                 current[leg][2] += (lift_height / steps)
                 # Tuck: Leg 0 (Right) -> Left (-X), Leg 5 (Left) -> Right (+X)
                 if leg == 0: current[leg][0] -= (tuck_in / steps)
                 if leg == 5: current[leg][0] += (tuck_in / steps)

             self.loco.transform_coordinates(current)
             self.loco.set_leg_angles()
             time.sleep(0.05)
        
        time.sleep(0.3)

        # 2. Mantis Wave (Anti-Phase Wiggle)
        # "Top joint back and forth" -> Oscillate Y (Forward/Back) and Z (Up/Down) slightly
        cycles = 6
        resolution = 20
        amp_y = 15 # Forward/Back wave
        amp_z = 10 # Up/Down bob
        
        base = [copy.deepcopy(current[0]), copy.deepcopy(current[5])]
        
        for i in range(cycles * resolution):
            angle = (i / resolution) * 2 * math.pi
            
            # Anti-phase oscillation
            # Leg 0
            current[0][1] = base[0][1] + math.sin(angle) * amp_y
            current[0][2] = base[0][2] + math.cos(angle) * amp_z
            
            # Leg 5 (Opposite phase)
            current[5][1] = base[1][1] + math.sin(angle + math.pi) * amp_y
            current[5][2] = base[1][2] + math.cos(angle + math.pi) * amp_z
            
            self.loco.transform_coordinates(current)
            self.loco.set_leg_angles()
            time.sleep(0.04)

        time.sleep(0.5)

        # 3. Return
        self.reset_neutral()
        if self.head:
            self.head.look_neutral()
        self.is_animating = False

    def laugh(self):
        """Rapid pitch changes."""
        logger.info("Animation: Laugh")
        self.is_animating = True
        
        # Pitch Up/Down 3 times
        # INCREASED INTENSITY: 15 -> 35
        
        current = copy.deepcopy(self.loco.body_points)
        front_legs = [0, 5]
        back_legs = [2, 3]
        
        for _ in range(4): # 4 bounces
            # Up
            for i in front_legs: current[i][2] += 35
            for i in back_legs: current[i][2] -= 35
            self.loco.transform_coordinates(current)
            self.loco.set_leg_angles()
            time.sleep(0.08) # Faster
            
            # Down
            for i in front_legs: current[i][2] -= 35
            for i in back_legs: current[i][2] += 35
            self.loco.transform_coordinates(current)
            self.loco.set_leg_angles()
            time.sleep(0.08)
            
        self.reset_neutral()
        self.is_animating = False

    def bow(self):
        """Deep pitch forward."""
        logger.info("Animation: Bow")
        self.is_animating = True
        
        current = copy.deepcopy(self.loco.body_points)
        front_legs = [0, 5]
        
        # Deep Bow
        # INCREASED DEPTH: 3 * 10 = 30 -> 6 * 12 = 72mm
        steps = 12
        for _ in range(steps):
            for i in front_legs:
                current[i][2] -= 6 # Drop front legs drastically
            
            self.loco.transform_coordinates(current)
            self.loco.set_leg_angles()
            time.sleep(0.05)
            
        time.sleep(1.0) # Hold Longer
        
        # Return Slowly
        for _ in range(steps):
            for i in front_legs:
                current[i][2] += 6 # Raise back up
            self.loco.transform_coordinates(current)
            self.loco.set_leg_angles()
            time.sleep(0.05)

        self.reset_neutral()
        self.is_animating = False
