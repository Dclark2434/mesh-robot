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
            
            # 2. BATCHED DEPLOYMENT (Outers -> Mids -> Settle)
            # Groups
            group_outers = [0, 5, 2, 3] # Front/Rear
            group_mids = [1, 4]         # Middle
            
            # Parameters
            steps = 40
            strike_height = 80          # Lift
            high_stance_z = -65         # Exaggerated Tall Stance 
            neutral_stance_z = -30      # Lower Settle (Squat)
            
            # Helper to animate a group
            def animate_group(legs, target_z_base):
                # Snapshot starts
                leg_starts = {}
                for leg in legs:
                    leg_starts[leg] = (current[leg][0], current[leg][1], current[leg][2])
                
                # Loop
                for s in range(steps):
                    if not self.is_animating: break
                    progress = (s + 1) / steps
                    
                    for leg in legs:
                        sx, sy, sz = leg_starts[leg]
                        tx, ty, _ = neutral_points[leg] # Use neutral X/Y
                        tz_base = target_z_base         # Use Override Z
                        
                        # Interpolate X/Y
                        cx = sx + (tx - sx) * progress
                        cy = sy + (ty - sy) * progress
                        
                        # Z Trajectory (Spider Strike)
                        base_z_interp = sz + (tz_base - sz) * progress
                        
                        z_offset = 0
                        if progress < 0.2: # Rapid Rise
                            z_offset = strike_height * (progress / 0.2)
                        elif progress < 0.7: # Menacing Hover
                            z_offset = strike_height
                        else: # Strike Down
                            drop_p = (progress - 0.7) / 0.3
                            z_offset = strike_height * (1.0 - drop_p)
                        
                        # Hover Override (Absolute Height relative to target ground)
                        if 0.2 < progress < 0.8:
                            cz = tz_base + z_offset
                        else:
                            cz = base_z_interp + z_offset
                        
                        current[leg][0] = cx
                        current[leg][1] = cy
                        current[leg][2] = cz
                    
                    self.loco.transform_coordinates(current)
                    self.loco.set_leg_angles()
                    time.sleep(0.04)
                
                # Impact/Recoil Group
                for i in range(5): # Compressor
                    comp_depth = 5 * (i+1)/5
                    for leg in legs:
                        current[leg][2] = target_z_base + comp_depth
                    self.loco.transform_coordinates(current)
                    self.loco.set_leg_angles()
                    time.sleep(0.02)
                for i in range(10): # Rebound
                    d = 5 * (1.0 - (i+1)/10)
                    for leg in legs:
                        current[leg][2] = target_z_base + d
                    self.loco.transform_coordinates(current)
                    self.loco.set_leg_angles()
                    time.sleep(0.02)

            # EXECUTE PHASES
            
            # Phase 1: Outers to High Stance
            animate_group(group_outers, high_stance_z)
            time.sleep(0.2)
            
            # Phase 2: Mids to High Stance
            animate_group(group_mids, high_stance_z)
            time.sleep(0.5)
            
            # Phase 3: Global Settle (High -> Neutral)
            logger.info("Settle: Dropping to Neutral Stance")
            for s in range(20):
                progress = (s + 1) / 20
                for leg in range(6):
                    # Interp from High to Neutral
                    # But wait, current is at High + 0 (recoiled).
                    # Actually just linear interp current Z to neutral Z
                    start_z_settle = high_stance_z
                    target_z_settle = neutral_stance_z
                    
                    cz = start_z_settle + (target_z_settle - start_z_settle) * progress
                    current[leg][2] = cz
                    
                self.loco.transform_coordinates(current)
                self.loco.set_leg_angles()
                time.sleep(0.05)

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
