# Robot Physical Measurements & Coordinate System

This document correlates the software values with the physical reality of the M.E.S.H. robot (Freenove Big Hexapod Kit).

## 1. Coordinate System
The Inverse Kinematics (IK) engine uses a Cartesian coordinate system relative to the **Hip Joint** (Coxa) of each leg.

*   **X / Y**: Horizontal plane. Larger absolute values = Leg extended further from body.
*   **Z-Axis**: Vertical height.
    *   **0**: Level with the Servo/Hip axis.
    *   **Positive (+)**: UP (Above the hip).
    *   **Negative (-)**: DOWN (Below the hip / Towards the ground).

> **Important**: Increasing the Z value *lifts* the leg. Decreasing Z (more negative) *lowers* the leg (raising the body).

## 2. Stance Heights
The `body_height` variable in `locomotion.py` determines the default Z-target for the feet.

| Stance Mode | Value (mm) | Description |
| :--- | :--- | :--- |
| **High Stance** | `-75` | Legs fully extended. Body is ~7.5cm off the ground. Used during "Spider Strike" boot. |
| **Standard** | `-45` | Factory default. 4.5cm clearance. Balanced stability. |
| **Low / Crouch** | `-35` | Lower center of gravity. Used as the previous neutral. |
| **Settle / Squat** | `-25` | **Current Default**. 2.5cm clearance. "Heavy" look. |

## 3. Boot Animation Parameters
The "MechWarrior" batched boot sequence uses specific override values to create its cinematic effect.

*   **Tuck Scale (`0.5`)**: Multiplier for X/Y. Pulls legs in to 50% extension. Creates a "Vertical/Crown" storage pose.
*   **Strike Lift (`+80mm`)**: The legs lift +80mm *above* their base trajectory during deployment.
    *   Target Z: `-75` (High Stance) + `80` (Lift) = `+5mm` (Foot is effectively above the hip joint during the arc!).
*   **Settling Drop**: The anim transitions from `-75` (High) to `-25` (Low), creating a visible drop.

## 4. Leg Groups
*   **Outers**: Legs 0, 2, 3, 5 (Front-Right, Rear-Right, Rear-Left, Front-Left).
*   **Mids**: Legs 1, 4 (Mid-Right, Mid-Left).

## 5. Servo Ranges (Approximate)
*   **Tibia/Femur Servos**: 0-180 degrees.
*   **Collision Risk**: Z values > +30mm (Too high, might hit chassis/face) or Z values < -90mm (Too low, servo limit).
