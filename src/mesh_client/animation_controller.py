import time
import math
import copy
import threading
from mesh_common.logging import get_logger

logger = get_logger("mesh_anim")


class AnimationController:
    def __init__(self, locomotion, head_controller=None):
        self.loco = locomotion
        self.head = head_controller
        self.is_animating = False
        self.anim_lock = threading.Lock()

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
        See walkthrough.md for sequence details.
        """
        # Non-blocking acquire: If busy, SKIP the wiggle to avoid collision/spazzing.
        if not self.anim_lock.acquire(blocking=False):
            logger.warning("Animation System Busy: Skipping Wiggle.")
            return

        logger.info("Animation: Palp Wiggle (Sequential Mode)")
        self.is_animating = True
        
        try:
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
            amp = 60 # mm (Big swing, increased from 35)
            
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

            # 5. Safe Return Sequence (Prevents Head Slap)
            # Reverse Step 3: Lower and Untuck partially BEFORE moving head
            logger.info("Safe Return: Lowering legs before head...")

            # DEFENSIVE: Re-assert Head Up in case it drifted or was reset
            if self.head:
                self.head.look_up(40)
                time.sleep(0.2)
            
            current[0][2] = base_z[0] # Stop wiggling (reset to lifted state)
            current[5][2] = base_z[1]
            
            for _ in range(steps):
                 for leg in front_legs:
                     current[leg][2] -= (lift_height / steps)
                     if leg == 0: current[leg][0] += (tuck_in / steps)
                     if leg == 5: current[leg][0] -= (tuck_in / steps)
                 
                 self.loco.transform_coordinates(current)
                 self.loco.set_leg_angles()
                 time.sleep(0.03)

            time.sleep(0.2)

            # NOW Safe to move head
            if self.head:
                self.head.look_neutral()
                # Finally Reset Stance (Clean up offsets)
                self.reset_neutral()
            
        finally:
            self.is_animating = False
            self.anim_lock.release()

        time.sleep(0.5)

        # 3. Return
        self.reset_neutral()
        if self.head:
            self.head.look_neutral()
        self.is_animating = False

    def slow_boot_stand(self):
        """
        MechWarrior Style Boot: Legs extend one by one.
        Assumes robot starts FLAT.
        """
        if not self.anim_lock.acquire(blocking=False):
             logger.warning("Animation Busy: Skipping Boot Stand.")
             return
             
        logger.info("Animation: Slow Boot Stand")
    def assume_tucked_pose(self):
        """Immediately snaps to the 'Tucked/Flat' storage posture."""
        with self.lock:
            flat_z_offset = 60 
            tuck_scale = 0.7   # Retraction scale to ensure clearance during boot
            
            # Reset calculating base
            self.loco.reset_posture()
            neutral_points = copy.deepcopy(self.loco.body_points)
            
            # Apply offsets
            current = copy.deepcopy(neutral_points)
            for i in range(6):
                current[i][2] += flat_z_offset      
                current[i][0] *= tuck_scale         
                current[i][1] *= tuck_scale         
            
            # Snap (Fast)
            self.loco.transform_coordinates(current)
            self.loco.set_leg_angles()
            return current, neutral_points # Return state for main loop

    def slow_boot_stand(self):
        """
        Animating the MechWarrior Startup sequences.
        Requires assume_tucked_pose() to be called beforehand for smooth transition.
        """
        logger.info("Animation: Slow Boot Stand")
        self.is_animating = True
        try:
            # Re-calculate state (idempotent if already tucked)
            current, neutral_points = self.assume_tucked_pose()
            time.sleep(0.5) # Stabilize after snap if it wasn't already done
            
            # 2. Sequential Expansion
            # User Order: Fronts (Faces up) -> Rears -> Mids
            # Front-Right(0), Front-Left(5), Rear-Right(2), Rear-Left(3), Mid-Right(1), Mid-Left(4)
            boot_order = [0, 5, 2, 3, 1, 4] 
            
            steps = 30 # Slower, smoother
            lift_height = 40 # Arc height in mm to avoid dragging
            
            for leg in boot_order:
                start_x, start_y, start_z = current[leg]
                target_x, target_y, target_z = neutral_points[leg]
                
                # ARC MOVEMENT
                for s in range(steps):
                   progress = (s + 1) / steps
                   
                   # Linear Base
                   current_x = start_x + (target_x - start_x) * progress
                   current_y = start_y + (target_y - start_y) * progress
                   base_z = start_z + (target_z - start_z) * progress
                   
                   # Arc interpolation: Sin wave (0 at start, 1 at mid, 0 at end)
                   arc = math.sin(progress * math.pi) * lift_height
                   
                   # Apply lift by subtracting arc value from Z (Moving closer to mounting point)
                   current_z = base_z - arc 

                   current[leg][0] = current_x
                   current[leg][1] = current_y
                   current[leg][2] = current_z
                   
                   self.loco.transform_coordinates(current)
                   self.loco.set_leg_angles()
                   time.sleep(0.04)
                
                # IMPACT / HEAVY SETTLE
                # Simulate chassis weight compressing the suspension upon landing
                
                # 1. LAND
                # Current loop ends at target (neutral).
                
                # 2. COMPRESS (Gravity takes over, leg 'buckles' slightly under weight)
                # Z increases = Leg moves UP relative to body / Body drops
                compression_depth = 5 # mm
                
                current[leg][2] = target_z + compression_depth
                self.loco.transform_coordinates(current)
                self.loco.set_leg_angles()
                time.sleep(0.05) # Fast 'Thud'
                
                # 3. REBOUND (Hydraulics stabilize)
                # Return to neutral Z
                current[leg][2] = target_z
                self.loco.transform_coordinates(current)
                self.loco.set_leg_angles()
                time.sleep(0.15) # Smooth settle

        except Exception as e:
            logger.error(f"Boot Anim Error: {e}")
        finally:
            self.is_animating = False

    def laugh(self):
        """Rapid pitch changes."""
        if not self.anim_lock.acquire(blocking=False):
            logger.warning("Animation System Busy: Skipping Laugh.")
            return

        logger.info("Animation: Laugh")
        self.is_animating = True
        try:
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
        finally:
            self.is_animating = False
            self.anim_lock.release()

    def bow(self):
        """Deep pitch forward."""
        if not self.anim_lock.acquire(blocking=False):
            logger.warning("Animation System Busy: Skipping Bow.")
            return

        logger.info("Animation: Bow")
        self.is_animating = True
        try:
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
        finally:
            self.is_animating = False
            self.anim_lock.release()
