import threading
import time
import queue
from mesh_common.logging import get_logger

logger = get_logger("command_dispatcher")

class CommandDispatcher:
    """
    Safely executes a queue of commands by mapping string keys to registered functions.
    Ensures actions are executed sequentially (blocking) or in parallel based on configuration.
    """
    def __init__(self):
        self._registry = {}
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._worker_thread = None
        self._is_busy = False # customizable flag for external queries

    def register(self, name, func):
        """Register a command name to a callable function."""
        if not callable(func):
            logger.error(f"Cannot register non-callable for '{name}'")
            return
        self._registry[name] = func
        logger.debug(f"Registered command: {name}")

    def push_plan(self, plan_list):
        """
        Push a list of actions to the queue.
        plan_list: [{"action": "walk", "param": 3}, ...]
        """
        if not isinstance(plan_list, list):
            logger.warning(f"Invalid plan format (expected list): {plan_list}")
            return

        logger.info(f"Queueing Plan ({len(plan_list)} steps)")
        for step in plan_list:
            self._queue.put(step)
        
        # Ensure worker is running
        if self._worker_thread is None or not self._worker_thread.is_alive():
            self.start()

    def start(self):
        """Start the background worker thread."""
        if self._worker_thread and self._worker_thread.is_alive():
            return
        
        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self._worker_thread.start()
        logger.info("Dispatcher worker started.")

    def stop(self):
        """Stop the worker thread."""
        self._stop_event.set()
        if self._worker_thread:
            self._worker_thread.join(timeout=2.0)
            self._worker_thread = None

    def _process_queue(self):
        while not self._stop_event.is_set():
            try:
                # Wait for command
                cmd = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            action = cmd.get("action")
            param = cmd.get("param")
            
            if action in self._registry:
                func = self._registry[action]
                self._is_busy = True
                try:
                    logger.info(f"[EXEC] {action} (Param: {param})")
                    if param:
                        # Inspect if function takes args? 
                        # For simplicity, we assume registered functions handle their own params 
                        # OR we try to pass if not None.
                        # Ideally, we standardize: all registered funcs take (param) or we wrap them.
                        # Let's try passing param if it's not None/null, else call empty.
                        try:
                            func(param) 
                        except TypeError: 
                            # If func doesn't accept args, try without
                            func()
                    else:
                         try:
                            func()
                         except TypeError:
                             # Func might expect an arg (e.g. param=None)
                             func(None)
                             
                    # SIMULATED BLOCKING
                    # Real blocking depends on the function itself being synchronous.
                    # If the function triggers a thread and returns immediately, this queue 
                    # will execute the NEXT item immediately. 
                    # For `locomotion`, our functions are blocking (time.sleep), so this defaults to sequential.
                    
                except Exception as e:
                    logger.error(f"[EXEC FAIL] {action}: {e}")
                finally:
                    self._is_busy = False
                    self._queue.task_done()
            else:
                logger.warning(f"[UNKNOWN] Action '{action}' not in registry. Skipping.")
                self._queue.task_done()
