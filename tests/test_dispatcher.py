import time
import unittest
import sys
import os

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from mesh_client.command_dispatcher import CommandDispatcher

class TestCommandDispatcher(unittest.TestCase):
    def setUp(self):
        self.dispatcher = CommandDispatcher()
        self.execution_log = []

    def tearDown(self):
        self.dispatcher.stop()

    def test_sequential_execution(self):
        """Verify that commands execute sequentially."""
        
        def cmd_slow(param):
            self.execution_log.append(f"start_{param}")
            time.sleep(0.1)
            self.execution_log.append(f"end_{param}")

        self.dispatcher.register("slow", cmd_slow)
        self.dispatcher.start()

        plan = [
            {"action": "slow", "param": "1"},
            {"action": "slow", "param": "2"}
        ]
        
        self.dispatcher.push_plan(plan)
        
        # Wait for execution
        time.sleep(0.3)
        
        expected = ["start_1", "end_1", "start_2", "end_2"]
        self.assertEqual(self.execution_log, expected)

    def test_unknown_command(self):
        """Verify unknown commands are skipped gracefully."""
        self.dispatcher.start()
        self.dispatcher.push_plan([{"action": "mystery", "param": "foo"}])
        time.sleep(0.1)
        # Should not crash

if __name__ == '__main__':
    unittest.main()
