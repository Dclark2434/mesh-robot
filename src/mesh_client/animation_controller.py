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
        with self.anim_lock:
            flat_z_offset = 60 
            tuck_scale = 0.5   # Tight vertical "Crown" tuck (Safer clearance)
            
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
            
            # 2. Fluid Overlapping Expansion
            # User Order: Fronts -> Rears -> Mids
            boot_order = [0, 5, 2, 3, 1, 4] 
            
            # ANIMATION PARAMETERS
            frames_per_leg = 40     # Total frames for one leg (Lift, Hover, Strike, Recoil)
            stagger = 20            # Start next leg halfway through (Overlap)
            strike_height = 80      # High menace lift
            compression_depth = 5   # Recoil depth
            delay_per_frame = 0.04  # Speed control
            
            # Calculate Total Animation Frames
            # Last leg starts at index 5 * stagger
            total_frames = (len(boot_order) - 1) * stagger + frames_per_leg + 10 # Buffer
            
            # Snapshots for interpolation
            leg_starts = {}
            for leg in boot_order:
                # Store tuple: (start_x, start_y, start_z)
                leg_starts[leg] = (current[leg][0], current[leg][1], current[leg][2])

            # MAIN ANIMATION LOOP
            for f in range(total_frames):
                if not self.is_animating: break
                
                legs_moved = False
                
                for i, leg in enumerate(boot_order):
                    start_frame = i * stagger
                    local_f = f - start_frame
                    
                    # If this leg is active
                    if 0 <= local_f < frames_per_leg:
                        legs_moved = True
                        progress = local_f / (frames_per_leg - 10) # Normalize 0-1 for Movement phase (30 frames)
                        recoil_phase = False
                        
                        if progress > 1.0:
                            # Recoil Phase (Frames 30-40)
                            recoil_phase = True
                            recoil_prog = (local_f - 30) / 10.0
                        
                        start_x, start_y, start_z = leg_starts[leg]
                        target_x, target_y, target_z = neutral_points[leg]
                        
                        if not recoil_phase:
                            # MOVEMENT PHASE (0-30 frames)
                            # Clip progress just in case
                            p = min(1.0, max(0.0, progress))
                            
                            # X/Y Linear
                            curr_x = start_x + (target_x - start_x) * p
                            curr_y = start_y + (target_y - start_y) * p
                            
                            # Z Trajectory (Spider Strike)
                            base_z = start_z + (target_z - start_z) * p
                            
                            z_offset = 0
                            if p < 0.2: # Rapid Rise
                                z_offset = strike_height * (p / 0.2) # POSITIVE = UP
                            elif p < 0.7: # Menacing Hover
                                z_offset = strike_height
                            else: # Strike Down (0.7 -> 1.0)
                                drop_p = (p - 0.7) / 0.3
                                z_offset = strike_height * (1.0 - drop_p)
                            
                            # Hover Override Logic (Ignore dragging start Z)
                            # We want absolute height during hover
                            if 0.2 < p < 0.8:
                                # Target Z is ground (-35). We want Ground + 80 = +45 (High)
                                curr_z = target_z + z_offset
                            else:
                                curr_z = base_z + z_offset
                                
                            # Final frame of movement: Set exact target to be sure
                            if local_f == 29:
                                curr_x, curr_y, curr_z = target_x, target_y, target_z
                                
                        else:
                            # RECOIL PHASE (30-40 frames)
                            # Compress then Rebound
                            # 0.0 -> 0.3: Compress
                            # 0.3 -> 1.0: Rebound
                            curr_x, curr_y = target_x, target_y
                            
                            if recoil_prog < 0.3:
                                # Compressing (Down/Z+)
                                factor = recoil_prog / 0.3
                                curr_z = target_z + (compression_depth * factor)
                            else:
                                # Rebounding (Up/Z-)
                                factor = (recoil_prog - 0.3) / 0.7
                                curr_z = target_z + (compression_depth * (1.0 - factor))
                        
                        # Apply
                        current[leg][0] = curr_x
                        current[leg][1] = curr_y
                        current[leg][2] = curr_z
                
                # Update Hardware once per frame
                if legs_moved:
                    self.loco.transform_coordinates(current)
                    self.loco.set_leg_angles()
                
                time.sleep(delay_per_frame)
                
            # Final Ensure
            self.reset_neutral()

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
