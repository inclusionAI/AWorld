# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import logging
import os
import queue
import threading
from logging.handlers import TimedRotatingFileHandler

amni_prompt_logger = logging.getLogger("amnicontext_prompt")
amni_digest_logger = logging.getLogger("amnicontext_digest")
logger = logging.getLogger("amnicontext")
# 新增异步日志logger
async_logger = logging.getLogger("amnicontext_async")

log_info_dict = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL
}

class AsyncLogHandler(logging.Handler):
    """异步日志处理器，将日志写入队列并在后台线程中处理"""

    def __init__(self, log_file_path: str, level=logging.NOTSET):
        super().__init__(level)
        self.log_file_path = log_file_path
        self.log_queue = queue.Queue()
        self.thread = None
        self.running = False
        self.file_handler = None

    def start(self):
        """启动异步日志处理线程"""
        if self.running:
            return

        self.running = True
        self.thread = threading.Thread(target=self._log_worker, daemon=True)
        self.thread.start()

    def stop(self):
        """停止异步日志处理线程"""
        if not self.running:
            return

        self.running = False
        if self.thread:
            self.thread.join(timeout=5.0)

    def emit(self, record):
        """将日志记录放入队列"""
        try:
            self.log_queue.put_nowait(record)
        except queue.Full:
            # 如果队列满了，丢弃最老的记录
            try:
                self.log_queue.get_nowait()
                self.log_queue.put_nowait(record)
            except queue.Empty:
                pass

    def _log_worker(self):
        """后台线程处理日志写入"""
        try:
            # 确保日志目录存在
            os.makedirs(os.path.dirname(self.log_file_path), exist_ok=True)

            # 创建文件处理器
            self.file_handler = TimedRotatingFileHandler(
                self.log_file_path,
                when='H',
                interval=1,
                backupCount=48
            )
            self.file_handler.setLevel(logging.DEBUG)

            # 设置格式化器
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            self.file_handler.setFormatter(formatter)

            while self.running:
                try:
                    # 从队列获取日志记录，超时1秒
                    record = self.log_queue.get(timeout=1.0)
                    if record:
                        # 写入文件
                        self.file_handler.emit(record)
                        self.log_queue.task_done()
                except queue.Empty:
                    continue
                except Exception as e:
                    print(f"Error in async log worker: {e}")

        except Exception as e:
            print(f"Failed to start async log worker: {e}")
        finally:
            if self.file_handler:
                self.file_handler.close()

def setup_amni_logging(log_dir: str = "./logs") -> None:
    """
    Setup logging configuration for amnicontext prompt logging

    Args:
        log_dir: Directory to store log files, defaults to "./logs"
    """
    # Ensure log directory exists
    os.makedirs(log_dir, exist_ok=True)

    # Set logger level to INFO to allow INFO level messages
    logger.setLevel(log_info_dict[os.getenv("AMNI_LOG_LEVEL", "INFO")])
    amni_prompt_logger.setLevel(log_info_dict[os.getenv("AMNI_PROMPT_LOG_LEVEL", "DEBUG")])
    amni_digest_logger.setLevel(log_info_dict[os.getenv("AMNI_DIGEST_LOG_LEVEL", "INFO")])
    # 设置异步日志logger
    async_logger.setLevel(log_info_dict[os.getenv("AMNI_ASYNC_LOG_LEVEL", "DEBUG")])

    # logger.propagate = False
    amni_prompt_logger.propagate = False
    async_logger.propagate = False

    # Prevent duplicate handlers
    if logger.handlers:
        return

    # Prompt-specific logging handler
    log_path = os.path.join(log_dir, "amnicontext.log")
    file_handler = TimedRotatingFileHandler(log_path, when='H', interval=1, backupCount=48)
    file_handler.setLevel(log_info_dict[os.getenv("AMNI_LOG_LEVEL", "INFO")])

    # Special formatter for prompt logs with emoji
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    prompt_log_path = os.path.join(log_dir, "amnicontext_prompt.log")
    prompt_file_handler = TimedRotatingFileHandler(prompt_log_path, when='H', interval=1, backupCount=48)
    prompt_file_handler.setLevel(log_info_dict[os.getenv("AMNI_PROMPT_LOG_LEVEL", "DEBUG")])
    prompt_formatter = logging.Formatter(
        "%(message)s"
    )
    prompt_file_handler.setFormatter(prompt_formatter)
    amni_prompt_logger.addHandler(prompt_file_handler)
    digest_log_path = os.path.join(log_dir, "amnicontext_digest.log")
    digest_file_handler = TimedRotatingFileHandler(digest_log_path, when='H', interval=1, backupCount=48)
    digest_file_handler.setLevel(log_info_dict[os.getenv("AMNI_DIGEST_LOG_LEVEL", "INFO")])
    digest_formatter = logging.Formatter(
        "%(asctime)s - %(message)s"
    )
    digest_file_handler.setFormatter(digest_formatter)
    amni_digest_logger.addHandler(digest_file_handler)

    # ERROR-specific logging handler
    error_log_path = os.path.join(log_dir, "amnicontext_error.log")
    error_file_handler = TimedRotatingFileHandler(error_log_path, when='H', interval=1, backupCount=48)
    error_file_handler.setLevel(logging.ERROR)
    error_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    error_file_handler.setFormatter(error_formatter)
    logger.addHandler(error_file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_info_dict[os.getenv("AMNI_LOG_LEVEL", "INFO")])
    console_handler.setFormatter(formatter)
    amni_prompt_logger.addHandler(console_handler)
    amni_digest_logger.addHandler(console_handler)

    # langextract and absl logging to separate file
    langextract_logger = logging.getLogger("langextract")
    langextract_logger.setLevel(log_info_dict[os.getenv("LANGEXTRACT_LOG_LEVEL", "INFO")])
    langextract_logger.propagate = False

    # Clear existing handlers for langextract
    langextract_logger.handlers.clear()

    # absl logging (used by langextract internally)
    absl_logger = logging.getLogger("absl")
    absl_logger.setLevel(log_info_dict[os.getenv("LANGEXTRACT_LOG_LEVEL", "INFO")])
    absl_logger.propagate = False

    # Clear existing handlers for absl
    absl_logger.handlers.clear()

    # Create shared file handler for langextract.log
    langextract_log_path = os.path.join(log_dir, "langextract.log")
    langextract_file_handler = TimedRotatingFileHandler(langextract_log_path, when='H', interval=1, backupCount=48)
    langextract_file_handler.setLevel(log_info_dict[os.getenv("LANGEXTRACT_LOG_LEVEL", "INFO")])
    langextract_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    langextract_file_handler.setFormatter(langextract_formatter)

    # Add the same handler to both loggers
    langextract_logger.addHandler(langextract_file_handler)
    absl_logger.addHandler(langextract_file_handler)

    # 配置异步日志处理器
    async_log_path = os.path.join(log_dir, "amnicontext_async.log")
    async_handler = AsyncLogHandler(async_log_path)
    async_handler.setLevel(log_info_dict[os.getenv("AMNI_ASYNC_LOG_LEVEL", "DEBUG")])
    async_logger.addHandler(async_handler)
    async_handler.start()

setup_amni_logging()