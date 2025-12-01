import structlog
import logging
import sys
from typing import Any

def setup_logging(log_level: str = "INFO") -> structlog.BoundLogger:
    """Setup structured logging"""
    
    # Configure structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer()
        ],
        wrapper_class=structlog.BoundLogger,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    
    # Configure standard logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper())
    )
    
    logger = structlog.get_logger()
    logger.info(f"Logging initialized with level: {log_level}")
    return logger

# Create a global logger instance
logger = setup_logging()


# Alias for backward compatibility
get_logger = lambda: logger