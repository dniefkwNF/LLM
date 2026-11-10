"""轻量运行日志：工具调用、对话轮次与会话操作落盘到 .agents/logs/agent.log。"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOGGER_NAME = "yagent"
_CONFIGURED = False


def setup_logging(root: Path, level: int = logging.INFO) -> logging.Logger:
    """初始化文件日志（幂等）。日志写入 <root>/.agents/logs/agent.log，单文件 1MB 轮转。"""
    global _CONFIGURED
    logger = logging.getLogger(_LOGGER_NAME)
    if _CONFIGURED:
        return logger
    logger.setLevel(level)
    try:
        log_dir = Path(root) / ".agents" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / "agent.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logger.addHandler(handler)
    except OSError:
        # 日志不可写时退化为静默，不影响主流程
        logger.addHandler(logging.NullHandler())
    _CONFIGURED = True
    return logger


def get_logger(suffix: str = "") -> logging.Logger:
    return logging.getLogger(f"{_LOGGER_NAME}.{suffix}" if suffix else _LOGGER_NAME)
