
import time
import logging
from mesh_client.servo_controller import ServoController
from mesh_client.locomotion import LocomotionController
from mesh_client.animation_controller import AnimationController

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_anim")

def main():
    logger.info("Initializing Hardware...")
    try:
        sc = ServoController()
        loco = LocomotionController(sc)
        anim = AnimationController(loco)
        logger.info("Hardware Init Success.")
    except Exception as e:
        logger.error(f"Init Failed: {e}")
        return

    logger.info("--- TEST 1: Direct Servo Wiggle (Head) ---")
    try:
        sc.set_angle(3, 80) # Head Pan Left? Check your channel
        time.sleep(0.5)
        sc.set_angle(3, 100) # Head Pan Right?
        time.sleep(0.5)
        logger.info("Head moved?")
    except Exception as e:
        logger.error(f"Servo Test Failed: {e}")

    logger.info("--- TEST 2: Locomotion Flat Reset ---")
    try:
        loco.reset_posture_flat()
        time.sleep(1)
        logger.info("Robot should be FLAT.")
    except Exception as e:
        logger.error(f"Flat Reset Failed: {e}")

    logger.info("--- TEST 3: Animation Laugh ---")
    try:
        # Manually trigger laugh
        anim.laugh()
        logger.info("Laugh sequence finished.")
    except Exception as e:
        logger.error(f"Laugh Failed: {e}")

    logger.info("--- TEST 4: Animation Bow ---")
    try:
        anim.bow()
        logger.info("Bow sequence finished.")
    except Exception as e:
        logger.error(f"Bow Failed: {e}")

    logger.info("Tests Complete. Creating relaxed state...")
    sc.relax()

if __name__ == "__main__":
    main()
