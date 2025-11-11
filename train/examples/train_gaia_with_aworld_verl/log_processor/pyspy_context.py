#!/usr/bin/env python3
"""
py-spy 上下文管理器
用于在代码块执行前后自动开启和关闭 py-spy 性能分析
"""
import os
import subprocess
import sys
import signal
import time
import logging
from pathlib import Path
from typing import Optional, List
from contextlib import contextmanager

logger = logging.getLogger(__name__)


def check_pyspy_installed() -> bool:
    """检查 py-spy 是否已安装"""
    try:
        result = subprocess.run(['py-spy', '--version'], 
                              capture_output=True, 
                              text=True, 
                              timeout=5)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


class PySpyProfiler:
    """
    py-spy 性能分析上下文管理器
    
    使用示例:
        with PySpyProfiler(output="logs/flame_graphs/profile", formats=['svg']):
            # 你的代码
            await run_task()
    """
    
    def __init__(
        self,
        output: Optional[str] = None,
        rate: int = 100,
        subprocesses: bool = True,
        native: bool = False,
        formats: List[str] = ['svg'],
        enable: Optional[bool] = None,
        pid: Optional[int] = None
    ):
        """
        Args:
            output: 输出文件路径（不含扩展名），如果为None则自动生成
            rate: 采样频率（Hz），默认100
            subprocesses: 是否包含子进程
            native: 是否包含原生代码（C扩展）
            formats: 输出格式列表，支持: svg, raw, flamegraph, speedscope
            enable: 是否启用分析（如果为None，则从环境变量 ENABLE_PYSPY 读取）
            pid: 要分析的进程ID，None表示使用当前进程
        """
        # 确定是否启用：如果 enable 为 None，则从环境变量读取；否则使用传入的值
        if enable is None:
            self.enable = os.getenv('ENABLE_PYSPY', 'False') == 'True'
        else:
            self.enable = enable
        
        if not self.enable:
            logger.info("py-spy 分析已禁用")
            return
            
        if not check_pyspy_installed():
            logger.warning("py-spy 未安装，跳过性能分析。安装命令: pip install py-spy")
            self.enable = False
            return
        
        self.pid = pid if pid is not None else os.getpid()
        self.rate = rate
        self.subprocesses = subprocesses
        self.native = native
        self.formats = formats if isinstance(formats, list) else [formats]
        
        # 生成输出路径
        if output is None:
            from datetime import datetime
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output = f"logs/flame_graphs/profile_{timestamp}"
        
        self.output_path = Path(output)
        self.output_dir = self.output_path.parent
        self.output_stem = self.output_path.stem
        
        # 确保输出目录存在
        if not self.output_dir.exists():
            self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.processes = []  # 存储 py-spy 进程
        
    def __enter__(self):
        if not self.enable:
            return self
        
        logger.info(f"开始 py-spy 性能分析，进程ID: {self.pid}")
        
        # 为每种格式启动一个 py-spy 进程
        for fmt in self.formats:
            output_file = self._get_output_file(fmt)
            cmd = self._build_command(fmt, output_file)
            
            try:
                # 启动 py-spy 进程（后台运行）
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setsid if hasattr(os, 'setsid') else None
                )
                
                # 等待一小段时间检查进程是否立即失败
                time.sleep(0.1)
                
                # 检查进程是否还在运行
                if process.poll() is not None:
                    # 进程已经结束，读取错误信息
                    _, stderr = process.communicate()
                    error_msg = stderr.decode('utf-8', errors='ignore') if stderr else "未知错误"
                    logger.error(f"py-spy ({fmt}) 进程启动后立即退出，退出码: {process.returncode}")
                    logger.error(f"错误信息: {error_msg}")
                    if "Permission denied" in error_msg or "permission" in error_msg.lower():
                        logger.error("提示: 可能需要 sudo 权限")
                    continue
                
                self.processes.append((process, fmt, output_file))
                logger.info(f"已启动 py-spy ({fmt})，输出文件: {output_file}")
            except Exception as e:
                logger.error(f"启动 py-spy ({fmt}) 失败: {e}")
                if "Permission denied" in str(e):
                    logger.error("提示: 可能需要 sudo 权限")
        
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self.enable or not self.processes:
            return False
        
        logger.info("停止 py-spy 性能分析...")
        
        # 停止所有 py-spy 进程
        for process, fmt, output_file in self.processes:
            try:
                # 检查进程是否还在运行
                if process.poll() is not None:
                    # 进程已经结束，读取错误信息
                    try:
                        _, stderr = process.communicate(timeout=1)
                        if stderr:
                            error_msg = stderr.decode('utf-8', errors='ignore')
                            if error_msg.strip():
                                logger.warning(f"py-spy ({fmt}) 进程已结束，退出码: {process.returncode}")
                                logger.debug(f"py-spy ({fmt}) 错误信息: {error_msg}")
                    except subprocess.TimeoutExpired:
                        pass
                    
                    if output_file.exists():
                        logger.info(f"py-spy 分析结果已保存: {output_file}")
                    else:
                        logger.warning(f"py-spy 输出文件不存在: {output_file}")
                        logger.debug(f"预期文件路径: {output_file.absolute()}")
                    continue
                
                # 发送 SIGINT 信号停止 py-spy
                if hasattr(os, 'killpg'):
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGINT)
                    except ProcessLookupError:
                        # 进程组不存在，可能进程已经结束
                        logger.debug(f"py-spy ({fmt}) 进程组不存在，进程可能已结束")
                        continue
                else:
                    process.send_signal(signal.SIGINT)
                
                # 等待进程结束（最多等待5秒）
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning(f"py-spy ({fmt}) 进程未在5秒内结束，强制终止")
                    if process.poll() is None:  # 再次检查进程是否还在运行
                        process.kill()
                        process.wait()
                
                if output_file.exists():
                    logger.info(f"py-spy 分析结果已保存: {output_file}")
                else:
                    logger.warning(f"py-spy 输出文件不存在: {output_file}")
                    
            except ProcessLookupError:
                # 进程不存在
                logger.debug(f"py-spy ({fmt}) 进程不存在，可能已经结束")
            except Exception as e:
                logger.error(f"停止 py-spy ({fmt}) 时出错: {e}")
        
        self.processes.clear()
        return False  # 不抑制异常
    
    def _get_output_file(self, fmt: str) -> Path:
        """根据格式获取输出文件路径"""
        if fmt == 'svg':
            return self.output_dir / f"{self.output_stem}.svg"
        elif fmt == 'raw':
            return self.output_dir / f"{self.output_stem}.txt"
        elif fmt == 'flamegraph':
            return self.output_dir / f"{self.output_stem}_flamegraph.txt"
        elif fmt == 'speedscope':
            return self.output_dir / f"{self.output_stem}.speedscope.json"
        else:
            return self.output_dir / f"{self.output_stem}.{fmt}"
    
    def _build_command(self, fmt: str, output_file: Path) -> List[str]:
        """构建 py-spy 命令"""
        cmd = ['py-spy', 'record']
        
        # 添加格式选项
        if fmt != 'svg':  # svg 是默认格式
            cmd.extend(['--format', fmt])
        
        # 添加其他选项
        cmd.extend([
            '--pid', str(self.pid),
            '--rate', str(self.rate),
            '--output', str(output_file),
        ])
        
        if self.subprocesses:
            cmd.append('--subprocesses')
        
        if self.native:
            cmd.append('--native')
        
        return cmd


@contextmanager
def pyspy_profile(
    output: Optional[str] = None,
    rate: int = 100,
    subprocesses: bool = True,
    native: bool = False,
    formats: List[str] = ['svg'],
    enable: Optional[bool] = None,
    pid: Optional[int] = None
):
    """
    便捷的上下文管理器函数
    
    使用示例:
        from log_processor.pyspy_context import pyspy_profile
        
        async def batch_run():
            with pyspy_profile(output="logs/flame_graphs/profile_batch"):
                result = await EvaluateRunner(...).run()
    """
    profiler = PySpyProfiler(
        output=output,
        rate=rate,
        subprocesses=subprocesses,
        native=native,
        formats=formats,
        enable=enable,
        pid=pid
    )
    with profiler:
        yield profiler

