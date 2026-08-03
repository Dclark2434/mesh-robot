import pytest
from unittest.mock import MagicMock

from mesh_client.servo_controller import (
    ANGLE_DEADBAND_DEG,
    PULSE_MAX_US,
    PULSE_MIN_US,
    ServoController,
    angle_to_pwm_count,
    map_value,
)


@pytest.fixture
def servo_controller():
    # Off a Pi, the controller falls back to DummyServo boards, which the
    # tests below replace with mocks to observe the writes.
    return ServoController()


def test_map_value_range():
    assert map_value(0, 0, 180, 500, 2500) == 500
    assert map_value(180, 0, 180, 500, 2500) == 2500
    assert map_value(90, 0, 180, 500, 2500) == 1500


def test_pwm_count_spans_the_safe_pulse_range():
    # The range is deliberately narrower than the servos' nominal 500-2500:
    # torque falls away at the extremes.
    assert angle_to_pwm_count(0) == int(PULSE_MIN_US / 20000 * 4095)
    assert angle_to_pwm_count(180) == int(PULSE_MAX_US / 20000 * 4095)
    assert angle_to_pwm_count(0) < angle_to_pwm_count(90) < angle_to_pwm_count(180)


def test_angle_clamping(servo_controller):
    mock_board = MagicMock()
    servo_controller.pwm41 = mock_board  # channels 0-15

    servo_controller.set_angle(0, 200)  # beyond travel; clamps to 180

    mock_board.set_pwm.assert_called_with(0, 0, angle_to_pwm_count(180))


def test_negative_angle_clamps_to_zero(servo_controller):
    mock_board = MagicMock()
    servo_controller.pwm41 = mock_board

    servo_controller.set_angle(0, -40)

    mock_board.set_pwm.assert_called_with(0, 0, angle_to_pwm_count(0))


def test_channel_routing(servo_controller):
    mock_41 = MagicMock()
    mock_40 = MagicMock()
    servo_controller.pwm41 = mock_41
    servo_controller.pwm40 = mock_40

    # Channel 5 lives on the first board.
    servo_controller.set_angle(5, 90)
    mock_41.set_pwm.assert_called()
    mock_40.set_pwm.assert_not_called()

    mock_41.reset_mock()
    mock_40.reset_mock()

    # Channel 20 lives on the second board, as its channel 4.
    servo_controller.set_angle(20, 90)
    mock_40.set_pwm.assert_called_with(4, 0, angle_to_pwm_count(90))
    mock_41.set_pwm.assert_not_called()


def test_sub_deadband_moves_are_not_written(servo_controller):
    # During a gait cycle the I2C bus is the bottleneck, so tiny changes are
    # dropped rather than spent.
    mock_board = MagicMock()
    servo_controller.pwm41 = mock_board

    servo_controller.set_angle(3, 90)
    mock_board.reset_mock()

    servo_controller.set_angle(3, 90 + ANGLE_DEADBAND_DEG / 2)
    mock_board.set_pwm.assert_not_called()

    servo_controller.set_angle(3, 90 + ANGLE_DEADBAND_DEG * 4)
    mock_board.set_pwm.assert_called()
