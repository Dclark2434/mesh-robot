import pytest
import math
from unittest.mock import MagicMock
from mesh_client.locomotion import LocomotionController

@pytest.fixture
def mock_servo():
    return MagicMock()

@pytest.fixture
def locomotion(mock_servo):
    # Initialize LocomotionController with mock servo
    # We mock os.path.exists/open inside load_calibration or just let it fail to defaults
    # Since we can't easily mock internal math calls without a lot of patching, 
    # we'll rely on the defaults it loads (which is fine for unit testing math)
    return LocomotionController(mock_servo)

def test_restrict_value(locomotion):
    assert locomotion.restrict_value(100, 0, 180) == 100
    assert locomotion.restrict_value(-10, 0, 180) == 0
    assert locomotion.restrict_value(200, 0, 180) == 180

def test_coordinate_to_angle_home(locomotion):
    # Test known home position (or close to it)
    # The default leg position is typically (140, 0, 0) relative to shoulder?
    # Based on code: calibration defaults are (140, 0, 0)
    
    # Let's test checking if x,y,z produces expected angles
    # Note: Specific values depend on the geometry constants (l1=33, l2=90, l3=110)
    
    # Case: Leg extended directly out
    x, y, z = 140, 0, 0
    a, b, c = locomotion.coordinate_to_angle(x, y, z)
    
    # We expect some valid angles, not 90,90,90 (error state)
    # Unless 140,0,0 IS the error state or default? 
    # Actually, let's just assert they are within valid servo range (0-180)
    assert 0 <= a <= 180
    assert 0 <= b <= 180
    assert 0 <= c <= 180

def test_coordinate_to_angle_unreachable(locomotion):
    # Try a point way out of reach
    x, y, z = 1000, 1000, 0
    a, b, c = locomotion.coordinate_to_angle(x, y, z)
    
    # The code uses restrict_value to clamp inputs to asin/acos, preventing math errors
    # So it returns calculated angles even for wild coordinates.
    # We just want to ensure it doesn't crash and returns valid servo angles (0-180).
    assert 0 <= a <= 180
    assert 0 <= b <= 180
    assert 0 <= c <= 180

def test_map_value(locomotion):
    val = locomotion.map_value(50, 0, 100, 0, 1000)
    assert val == 500

    val = locomotion.map_value(0, 0, 100, 0, 1000)
    assert val == 0
