"""Interruption must be distinguishable from a turn simply ending.

Both leave the robot idle, but only one means "throw away the audio you have
not played yet". Conflating them truncates the last words of every reply.
"""

from mesh_common.protocol import (
    InterruptMessage,
    MessageType,
    Status,
    StatusMessage,
    decode,
    encode,
)


def test_interrupt_is_its_own_message_type():
    payload = decode(encode(InterruptMessage()))
    assert payload["type"] == MessageType.INTERRUPT.value


def test_end_of_turn_is_not_an_interrupt():
    payload = decode(encode(StatusMessage(Status.IDLE)))
    assert payload["type"] == MessageType.STATUS.value
    assert payload["type"] != MessageType.INTERRUPT.value
