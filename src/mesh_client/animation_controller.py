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
        Idle Animation: Sequential Spider Display.
        1. Head Up 40.
        2. Step Middle Legs forward individually (for stability).
        3. Tuck & Lift Front Legs High.
        4. Oscillate Outer Servos.
        """
        logger.info("Animation: Palp Wiggle (Sequential Mode)")
        self.is_animating = True
        
        # 1. Head Up first
        if self.head:
            self.head.look_up(40)
            time.sleep(0.5)

        current = copy.deepcopy(self.loco.body_points)
        
        # 2. Step Middle Legs Forward (One at a time)
        # Goal: Move mid legs (1 and 4) forward 100mm and slightly out 20mm
        mid_target_y = [100, 100] # Relative
        mid_target_x = [20, -20] # Splay out
        
        mid_legs = [1, 4] # Right, Left
        
        for idx, leg in enumerate(mid_legs):
            # Lift
            steps = 5
            for _ in range(steps):
                current[leg][2] += (40 / steps) # Lift 40mm
                self.loco.transform_coordinates(current)
                self.loco.set_leg_angles()
                time.sleep(0.02)
            
            # Move & Drop
            steps = 5
            for _ in range(steps):
                current[leg][1] += (100 / steps)            # Move Forward 100mm
                current[leg][0] += (mid_target_x[idx] / steps) # Splay
                current[leg][2] -= (40 / steps)             # Drop
                self.loco.transform_coordinates(current)
                self.loco.set_leg_angles()
                time.sleep(0.02)
                
            time.sleep(0.1)

        # 3. Lift & Tuck Front Legs (Safe Max)
        front_legs = [0, 5]
        lift_height = 110 # mm (Max before servo strain?)
        tuck_in = 60 # mm
        
        steps = 15
        for _ in range(steps):
             for leg in front_legs:
                 current[leg][2] += (lift_height / steps)
                 if leg == 0: current[leg][0] -= (tuck_in / steps)
                 if leg == 5: current[leg][0] += (tuck_in / steps)
             
             self.loco.transform_coordinates(current)
             self.loco.set_leg_angles()
             time.sleep(0.03)

        time.sleep(0.2)
        
        # 4. Oscillate Outer Servos (Z-Axis / Tibia)
        # "Moving top joint back and forth"
        cycles = 15
        resolution = 20
        amp = 35 # mm (Big swing)
        
        base_z = [copy.deepcopy(current[0][2]), copy.deepcopy(current[5][2])]
        
        for i in range(cycles * resolution):
            angle = (i / resolution) * 2 * math.pi
            offset = math.sin(angle) * amp
            
            # Anti-phase
            current[0][2] = base_z[0] + offset
            current[5][2] = base_z[1] - offset
            
            self.loco.transform_coordinates(current)
            self.loco.set_leg_angles()
            time.sleep(0.01) # Fast

        # 5. Return
        self.reset_neutral()
        if self.head:
            self.head.look_neutral()
        self.is_animating = False

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
        back_legs = [2, 3] # Add rear support
        
        # Deep Bow
        # Front legs Retract (+Z) -> Body Down
        # Back legs Extend (-Z) -> Body Up
        steps = 15
        depth = 60 # mm
        lift_rear = 40 # mm
        
        for _ in range(steps):
            for i in front_legs:
                current[i][2] += (depth / steps) # Retract (Drop Front)
            
            for i in back_legs:
                current[i][2] -= (lift_rear / steps) # Extend (Raise Rear)
            
            self.loco.transform_coordinates(current)
            self.loco.set_leg_angles()
            time.sleep(0.05)
            
        time.sleep(1.0) # Hold Longer
        
        # Return Slowly
        for _ in range(steps):
            for i in front_legs:
                current[i][2] -= (depth / steps) 
            for i in back_legs:
                current[i][2] += (lift_rear / steps)

            self.loco.transform_coordinates(current)
            self.loco.set_leg_angles()
            time.sleep(0.05)

        self.reset_neutral()
        self.is_animating = False
