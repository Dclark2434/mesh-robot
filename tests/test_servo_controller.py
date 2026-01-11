import pytest
from unittest.mock import MagicMock
from mesh_client.servo_controller import ServoController, map_value

@pytest.fixture
def servo_controller():
    # Mocking RobotHardware to prevent platform detection logic from running wild
    # Actually ServoController inherits RobotHardware, whose init does platform checks
    # For unit testing we might want to just test the helper functions or mock sys.platform 
    # but map_value is a standalone function in servo_controller.py, so we can test that easily.
    return ServoController()

def test_map_value_range():
    # 0-180 -> 500-2500
    assert map_value(0, 0, 180, 500, 2500) == 500
    assert map_value(180, 0, 180, 500, 2500) == 2500
    assert map_value(90, 0, 180, 500, 2500) == 1500

def test_angle_clamping(servo_controller):
    # We need to spy on set_pwm or mock the internal pwm boards
    # servo_controller.pwm41 and pwm40 are likely DummyServo in test env
    
    # Inject a mock board
    mock_board = MagicMock()
    servo_controller.pwm41 = mock_board # Channels 0-15
    
    # Test clamping
    servo_controller.set_angle(0, 200) # Should clamp to 180
    
    # Verify the calculation passed to set_pwm
    # 180 -> 2500us
    # 2500us -> 512 (approx) in 0-4095 scale?  
    # No, logic is: map_value(duty_cycle, 0, 20000, 0, 4095)
    # 2500 / 20000 * 4096 = 512
    
    expected_off = int(2500 / 20000 * 4095)
    
    mock_board.set_pwm.assert_called_with(0, 0, expected_off)

def test_channel_routing(servo_controller):
    mock_41 = MagicMock()
    mock_40 = MagicMock()
    servo_controller.pwm41 = mock_41
    servo_controller.pwm40 = mock_40
    
    # Channel 5 -> pwm41
    servo_controller.set_angle(5, 90)
    mock_41.set_pwm.assert_called()
    mock_40.set_pwm.assert_not_called()
    
    mock_41.reset_mock()
    mock_40.reset_mock()
    
    # Channel 20 -> pwm40 (channel 4 on board)
    servo_controller.set_angle(20, 90)
    mock_40.set_pwm.assert_called_with(4, 0, pytest.approx(307, abs=1)) # 1500us -> ~307
    mock_41.set_pwm.assert_not_called()
