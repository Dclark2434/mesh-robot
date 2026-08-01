"""Wire protocol between the brain (PC) and the robot (Pi).

Everything the two processes say to each other over the LiveKit data channel is
defined here, once. Previously both sides built dict literals inline, which let
the robot's dispatcher and the LLM's prompt drift apart -- the prompt advertised
``strafe_left``, ``emote`` and ``see``, none of which the client could execute.

The action registry below is the single source of truth. The client builds its
dispatch table from it, and the server generates the prompt's action list from
it, so an action the robot can't perform can't be advertised to the model.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

PROTOCOL_VERSION = 2


class MessageType(str, Enum):
    """Envelope discriminator for data-channel messages."""

    HELLO = "hello"          # robot -> brain, on join: capabilities
    ACTION = "action"        # brain -> robot: perform a physical action
    STATUS = "status"        # brain -> robot: conversational state (drives LEDs)
    INTERRUPT = "interrupt"  # brain -> robot: drop queued speech, you were cut off
    TELEMETRY = "telemetry"  # robot -> brain: battery, IMU, posture


class Status(str, Enum):
    """Conversational state of the brain, mirrored on the robot's LEDs."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"


class Lane(str, Enum):
    """Execution lane for an action.

    Actions in different lanes run concurrently on the robot; actions in the
    same lane run in order. Splitting head gestures from whole-body movement is
    what lets the robot nod while it walks, and stops a 3-second bow from
    delaying a 0.6-second glance queued behind it.
    """

    BODY = "body"    # legs / locomotion / whole-body emotes. Slow, exclusive.
    HEAD = "head"    # pan-tilt only. Fast, safe to run during body motion.
    AUX = "aux"      # LEDs, buzzer. Effectively instant.


class ParamKind(str, Enum):
    """What kind of parameter, if any, an action accepts."""

    NONE = "none"
    STEPS = "steps"  # small positive integer


@dataclass(frozen=True)
class ActionSpec:
    """Description of one physical action the robot can perform.

    Attributes:
        name: Wire name. Lowercase; what the LLM emits inside an ACTION tag.
        lane: Which execution lane the action occupies.
        duration: Rough wall-clock seconds to perform, measured from the
            animation code. Used to decide whether a gesture is still worth
            performing by the time it comes up in the queue.
        param: Parameter kind accepted.
        speech_safe: Whether this is short enough to fire mid-sentence as an
            expressive gesture. Long locomotion is not.
        describe: One-line hint for the LLM's prompt.
    """

    name: str
    lane: Lane
    duration: float
    param: ParamKind = ParamKind.NONE
    speech_safe: bool = True
    describe: str = ""


# Durations are measured from the animation implementations in mesh_client, not
# guessed: e.g. hand_wave runs 10 lift steps at 40ms plus 3 wave cycles of 20
# moves at 20ms ~= 1.6s. They only need to be right to within a few hundred ms.
ACTIONS: dict[str, ActionSpec] = {
    spec.name: spec
    for spec in (
        # -- head: fast, expressive, safe to interleave with speech -----------
        ActionSpec("look_left", Lane.HEAD, 0.4, describe="glance left"),
        ActionSpec("look_right", Lane.HEAD, 0.4, describe="glance right"),
        ActionSpec("look_up", Lane.HEAD, 0.4, describe="look up / thoughtful"),
        ActionSpec("look_down", Lane.HEAD, 0.4, describe="look down / dejected"),
        ActionSpec("look_center", Lane.HEAD, 0.4, describe="return gaze to the user"),
        ActionSpec("nod", Lane.HEAD, 1.8, describe="nod yes"),
        ActionSpec("shake", Lane.HEAD, 1.8, describe="shake head no"),
        ActionSpec("smh", Lane.HEAD, 2.8, describe="look down and shake head, disappointed"),
        ActionSpec("roll_eyes", Lane.HEAD, 1.1, describe="roll eyes, exasperated"),
        # -- body: expressive but slower -------------------------------------
        ActionSpec("wave", Lane.BODY, 1.6, describe="wave a front leg hello"),
        ActionSpec("tap", Lane.BODY, 1.1, describe="tap a foot impatiently"),
        ActionSpec("tippy_tap", Lane.BODY, 2.4, describe="excited little foot shuffle"),
        ActionSpec("laugh", Lane.BODY, 1.3, describe="bounce the body, laughing"),
        ActionSpec("bow", Lane.BODY, 3.5, speech_safe=False, describe="deep, slow bow"),
        ActionSpec("wiggle", Lane.BODY, 16.0, speech_safe=False, describe="full show-off wiggle display"),
        # -- body: locomotion. Never mid-sentence. ---------------------------
        ActionSpec("walk_forward", Lane.BODY, 4.0, ParamKind.STEPS, False, "walk forward N steps"),
        ActionSpec("move_backward", Lane.BODY, 4.0, ParamKind.STEPS, False, "back up N steps"),
        ActionSpec("turn_left", Lane.BODY, 4.0, ParamKind.STEPS, False, "turn left; 9 steps ~ 180 degrees"),
        ActionSpec("turn_right", Lane.BODY, 4.0, ParamKind.STEPS, False, "turn right; 9 steps ~ 180 degrees"),
        # -- body: posture ----------------------------------------------------
        ActionSpec("relax", Lane.BODY, 1.0, speech_safe=False, describe="power down servos and rest"),
        ActionSpec("reset", Lane.BODY, 1.5, speech_safe=False, describe="lie flat, safe to pick up"),
        # -- aux: instant ------------------------------------------------------
        ActionSpec("led_flash", Lane.AUX, 0.5, describe="flash the cue light"),
        ActionSpec("buzzer_beep", Lane.AUX, 0.2, describe="short beep"),
        ActionSpec("buzzer_warn", Lane.AUX, 0.5, describe="two warning beeps"),
        ActionSpec("buzzer_alarm", Lane.AUX, 1.1, describe="alarm siren"),
    )
}

#: Aliases the model reaches for anyway. Mapping them beats losing the gesture.
ACTION_ALIASES: dict[str, str] = {
    "walk": "walk_forward",
    "move_forward": "walk_forward",
    "forward": "walk_forward",
    "backward": "move_backward",
    "back_up": "move_backward",
    "strafe_left": "turn_left",
    "strafe_right": "turn_right",
    "spin": "turn_right",
    "yes": "nod",
    "no": "shake",
    "nod_yes": "nod",
    "shake_no": "shake",
    "eye_roll": "roll_eyes",
    "look_at_me": "look_center",
    "center": "look_center",
    "stand_by": "relax",
    "lay_flat": "reset",
    "beep": "buzzer_beep",
    "flash": "led_flash",
}


def resolve_action(name: str) -> ActionSpec | None:
    """Look up an action by wire name, tolerating known aliases.

    Args:
        name: Action name as emitted by the LLM. Case and surrounding
            whitespace are ignored.

    Returns:
        The matching spec, or None if the robot has no such action.
    """
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    key = ACTION_ALIASES.get(key, key)
    return ACTIONS.get(key)


# --------------------------------------------------------------------------
# Envelopes
# --------------------------------------------------------------------------


@dataclass
class ActionMessage:
    """Instruction to perform one physical action.

    Attributes:
        action: Canonical action name (already resolved through aliases).
        param: Optional parameter, meaning defined by the action's ParamKind.
        expires_in: Seconds after ``sent_at`` beyond which the gesture is no
            longer worth performing. A wave that comes up 8 seconds late reads
            as a malfunction, not a wave. None means never expires.
        sent_at: Unix timestamp set by the brain at send time.
    """

    action: str
    param: str | None = None
    expires_in: float | None = None
    sent_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain JSON-compatible dict."""
        return {
            "v": PROTOCOL_VERSION,
            "type": MessageType.ACTION.value,
            "action": self.action,
            "param": self.param,
            "expires_in": self.expires_in,
            "sent_at": self.sent_at,
        }


@dataclass
class StatusMessage:
    """Conversational state update, mirrored on the robot's LEDs."""

    status: Status

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain JSON-compatible dict."""
        return {
            "v": PROTOCOL_VERSION,
            "type": MessageType.STATUS.value,
            "status": self.status.value,
        }


@dataclass
class InterruptMessage:
    """The user cut the robot off; drop whatever speech is still queued.

    Distinct from a status change because a status of "idle" also arrives at
    the *normal* end of a turn, when the robot still has queued audio that
    should be allowed to finish.
    """

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain JSON-compatible dict."""
        return {"v": PROTOCOL_VERSION, "type": MessageType.INTERRUPT.value}


@dataclass
class TelemetryMessage:
    """Robot health report, sent periodically from the Pi."""

    battery_volts: float | None = None
    battery_percent: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain JSON-compatible dict."""
        return {
            "v": PROTOCOL_VERSION,
            "type": MessageType.TELEMETRY.value,
            "battery_volts": self.battery_volts,
            "battery_percent": self.battery_percent,
        }


def encode(
    message: ActionMessage | StatusMessage | InterruptMessage | TelemetryMessage,
) -> str:
    """Encode a message for the data channel.

    Args:
        message: Any protocol message dataclass.

    Returns:
        Compact JSON string.
    """
    return json.dumps(message.to_dict(), separators=(",", ":"))


def decode(raw: str | bytes) -> dict[str, Any]:
    """Decode a data-channel payload into a dict.

    Args:
        raw: JSON bytes or string as received.

    Returns:
        The decoded mapping, or an empty dict if the payload is unusable.
        Callers dispatch on the ``type`` key.
    """
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def prompt_action_reference() -> str:
    """Render the action list for injection into the system prompt.

    Generated from ACTIONS so the model is never told about a gesture the
    robot cannot actually perform.

    Returns:
        A newline-delimited reference block.
    """
    lines: list[str] = []
    for group, title in (
        (Lane.HEAD, "Head gestures (fast, use these often while talking)"),
        (Lane.BODY, "Body gestures"),
        (Lane.AUX, "Lights and sound"),
    ):
        specs = [s for s in ACTIONS.values() if s.lane is group]
        if not specs:
            continue
        lines.append(f"{title}:")
        for spec in specs:
            arg = ":N" if spec.param is ParamKind.STEPS else ""
            note = "" if spec.speech_safe else "  (finish your sentence first)"
            lines.append(f"  [ACTION: {spec.name}{arg}] - {spec.describe}{note}")
        lines.append("")
    return "\n".join(lines).rstrip()
