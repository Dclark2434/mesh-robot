import logging
import sys
from colorama import init, Fore, Style

# Initialize colorama
init(autoreset=True)

class MeshFormatter(logging.Formatter):
    """Custom colorized formatter for M.E.S.H."""
    
    COLORS = {
        'DEBUG': Fore.CYAN,
        'INFO': Fore.WHITE,
        'WARNING': Fore.YELLOW,
        'ERROR': Fore.RED,
        'CRITICAL': Fore.RED + Style.BRIGHT,
    }

    def format(self, record):
        color = self.COLORS.get(record.levelname, '')
        level_name = f"{color}[{record.levelname}]{Style.RESET_ALL}"
        message = record.getMessage()
        
        # Add custom parsing for specific tags if needed (like the old server colored print)
        if "[LATENCY]" in message: message = f"{Fore.CYAN}{message}{Style.RESET_ALL}"
        if "[TRIGGER]" in message: message = f"{Fore.GREEN}{message}{Style.RESET_ALL}"
        if "[COMMAND]" in message: message = f"{Fore.YELLOW}{message}{Style.RESET_ALL}"
        if "[FEEDBACK]" in message: message = f"{Fore.YELLOW}{message}{Style.RESET_ALL}"
        
        return f"{level_name} {message}"

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(MeshFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
