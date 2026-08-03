"""Tests for getting captured frames into the orientation and colour LiveKit wants.

Channel order is the one to care about. LiveKit's RGB24 buffer means literally
red, green, blue in memory, and picamera2 names its formats in the opposite
order to the bytes they produce. Get it backwards and nothing crashes; the
robot just calmly tells you your blue mug is red.
"""

import numpy as np

from mesh_client.camera import prepare_frame


def rgb_pixel(r: int, g: int, b: int, height: int = 2, width: int = 3) -> np.ndarray:
    """Build a solid-colour frame.

    Args:
        r: Red channel value.
        g: Green channel value.
        b: Blue channel value.
        height: Frame height.
        width: Frame width.

    Returns:
        An HxWx3 array of the given colour.
    """
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :] = (r, g, b)
    return frame


def test_frame_passes_through_untouched_by_default():
    frame = rgb_pixel(10, 20, 30)
    assert np.array_equal(prepare_frame(frame, 0, False), frame)


def test_swap_exchanges_red_and_blue():
    out = prepare_frame(rgb_pixel(255, 0, 0), 0, True)
    assert tuple(out[0, 0]) == (0, 0, 255)


def test_swap_leaves_green_alone():
    out = prepare_frame(rgb_pixel(0, 128, 0), 0, True)
    assert tuple(out[0, 0]) == (0, 128, 0)


def test_swapping_twice_is_a_no_op():
    frame = rgb_pixel(10, 20, 30)
    assert np.array_equal(prepare_frame(prepare_frame(frame, 0, True), 0, True), frame)


def test_quarter_turns_swap_the_dimensions():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    assert prepare_frame(frame, 90, False).shape[:2] == (640, 480)
    assert prepare_frame(frame, 270, False).shape[:2] == (640, 480)


def test_half_turn_keeps_the_dimensions():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    assert prepare_frame(frame, 180, False).shape[:2] == (480, 640)


def test_rotation_actually_moves_pixels():
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    frame[0, 0] = (255, 255, 255)  # top-left
    # A quarter turn counter-clockwise sends the top-left corner to bottom-left.
    assert tuple(prepare_frame(frame, 90, False)[1, 0]) == (255, 255, 255)


def test_a_full_turn_changes_nothing():
    frame = rgb_pixel(1, 2, 3)
    assert np.array_equal(prepare_frame(frame, 360, False), frame)


def test_output_is_contiguous():
    # Both rotation and channel reversal produce views; tobytes() on a
    # non-contiguous view would serialize the wrong bytes.
    frame = np.zeros((4, 6, 3), dtype=np.uint8)
    for rotation in (0, 90, 180, 270):
        for swap in (False, True):
            assert prepare_frame(frame, rotation, swap).flags["C_CONTIGUOUS"]


def test_byte_length_survives_every_transform():
    frame = np.zeros((4, 6, 3), dtype=np.uint8)
    for rotation in (0, 90, 180, 270):
        assert len(prepare_frame(frame, rotation, True).tobytes()) == 4 * 6 * 3
