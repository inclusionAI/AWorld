#!/usr/bin/env python3
"""
py-spy 性能分析工具
用于对 Python 程序进行耗时分析和性能分析，生成火焰图

使用方法:
1. 附加到正在运行的进程:
   python profile_with_pyspy.py --pid <进程ID> --duration 30 --output profile.svg

2. 启动新进程并分析:
   python profile_with_pyspy.py --command "python your_script.py" --duration 60 --output profile.svg

3. 生成多种格式:
   python profile_with_pyspy.py --pid <进程ID> --formats svg,raw,flamegraph --output-dir profiles/
"""
import argparse
import subprocess
import sys
import os
import time
from pathlib import Path
from typing import Optional, List
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
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


def install_pyspy():
    """安装 py-spy"""
    logger.info("正在安装 py-spy...")
    try:
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'py-spy'], 
                      check=True)
        logger.info("py-spy 安装成功")
    except subprocess.CalledProcessError as e:
        logger.error(f"安装 py-spy 失败: {e}")
        sys.exit(1)


def profile_by_pid(
    pid: int,
    duration: Optional[int] = None,
    output: Optional[str] = None,
    rate: int = 100,
    subprocesses: bool = True,
    native: bool = False,
    formats: List[str] = ['svg']
) -> str:
    """
    附加到正在运行的进程进行性能分析
    
    Args:
        pid: 进程ID
        duration: 采样时长（秒），None 表示持续采样直到进程结束
        output: 输出文件路径（不含扩展名）
        rate: 采样频率（Hz），默认100
        subprocesses: 是否包含子进程
        native: 是否包含原生代码（C扩展）
        formats: 输出格式列表，支持: svg, raw, flamegraph, speedscope
        
    Returns:
        输出文件路径
    """
    if not check_pyspy_installed():
        install_pyspy()
    
    if output is None:
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        output = f"profile_{pid}_{timestamp}"
    
    output_path = Path(output)
    output_dir = output_path.parent
    output_stem = output_path.stem
    
    if not output_dir.exists():
        output_dir.mkdir(parents=True, exist_ok=True)
    
    results = []
    
    for fmt in formats:
        if fmt == 'svg':
            output_file = output_dir / f"{output_stem}.svg"
            cmd = [
                'py-spy', 'record',
                '--pid', str(pid),
                '--rate', str(rate),
                '--output', str(output_file),
            ]
        elif fmt == 'raw':
            output_file = output_dir / f"{output_stem}.txt"
            cmd = [
                'py-spy', 'record',
                '--pid', str(pid),
                '--rate', str(rate),
                '--output', str(output_file),
                '--format', 'raw',
            ]
        elif fmt == 'flamegraph':
            output_file = output_dir / f"{output_stem}.txt"
            cmd = [
                'py-spy', 'record',
                '--pid', str(pid),
                '--rate', str(rate),
                '--output', str(output_file),
                '--format', 'flamegraph',
            ]
        elif fmt == 'speedscope':
            output_file = output_dir / f"{output_stem}.speedscope.json"
            cmd = [
                'py-spy', 'record',
                '--pid', str(pid),
                '--rate', str(rate),
                '--output', str(output_file),
                '--format', 'speedscope',
            ]
        else:
            logger.warning(f"不支持的格式: {fmt}，跳过")
            continue
        
        if duration:
            cmd.extend(['--duration', str(duration)])
        
        if subprocesses:
            cmd.append('--subprocesses')
        
        if native:
            cmd.append('--native')
        
        logger.info(f"开始分析进程 {pid}，格式: {fmt}，输出: {output_file}")
        logger.info(f"命令: {' '.join(cmd)}")
        
        try:
            # 注意: 在 macOS/Linux 上可能需要 sudo 权限
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            logger.info(f"分析完成: {output_file}")
            results.append(str(output_file))
        except subprocess.CalledProcessError as e:
            logger.error(f"分析失败: {e}")
            logger.error(f"错误输出: {e.stderr}")
            if "Permission denied" in str(e.stderr):
                logger.error("提示: 可能需要 sudo 权限，尝试: sudo python profile_with_pyspy.py ...")
        except KeyboardInterrupt:
            logger.info("分析被用户中断")
            if output_file.exists():
                results.append(str(output_file))
            break
    
    return results[0] if results else None


def profile_by_command(
    command: str,
    duration: Optional[int] = None,
    output: Optional[str] = None,
    rate: int = 100,
    subprocesses: bool = True,
    native: bool = False,
    formats: List[str] = ['svg']
) -> str:
    """
    启动新进程并进行分析
    
    Args:
        command: 要执行的命令（如 "python script.py"）
        duration: 采样时长（秒），None 表示持续采样直到进程结束
        output: 输出文件路径（不含扩展名）
        rate: 采样频率（Hz），默认100
        subprocesses: 是否包含子进程
        native: 是否包含原生代码（C扩展）
        formats: 输出格式列表
        
    Returns:
        输出文件路径
    """
    if not check_pyspy_installed():
        install_pyspy()
    
    if output is None:
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        output = f"profile_{timestamp}"
    
    output_path = Path(output)
    output_dir = output_path.parent
    output_stem = output_path.stem
    
    if not output_dir.exists():
        output_dir.mkdir(parents=True, exist_ok=True)
    
    results = []
    
    for fmt in formats:
        if fmt == 'svg':
            output_file = output_dir / f"{output_stem}.svg"
            cmd = [
                'py-spy', 'record',
                '--rate', str(rate),
                '--output', str(output_file),
            ]
        elif fmt == 'raw':
            output_file = output_dir / f"{output_stem}.txt"
            cmd = [
                'py-spy', 'record',
                '--rate', str(rate),
                '--output', str(output_file),
                '--format', 'raw',
            ]
        elif fmt == 'flamegraph':
            output_file = output_dir / f"{output_stem}.txt"
            cmd = [
                'py-spy', 'record',
                '--rate', str(rate),
                '--output', str(output_file),
                '--format', 'flamegraph',
            ]
        elif fmt == 'speedscope':
            output_file = output_dir / f"{output_stem}.speedscope.json"
            cmd = [
                'py-spy', 'record',
                '--rate', str(rate),
                '--output', str(output_file),
                '--format', 'speedscope',
            ]
        else:
            logger.warning(f"不支持的格式: {fmt}，跳过")
            continue
        
        if duration:
            cmd.extend(['--duration', str(duration)])
        
        if subprocesses:
            cmd.append('--subprocesses')
        
        if native:
            cmd.append('--native')
        
        # 添加要执行的命令
        cmd.extend(['--'] + command.split())
        
        logger.info(f"启动进程并分析，格式: {fmt}，输出: {output_file}")
        logger.info(f"命令: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            logger.info(f"分析完成: {output_file}")
            results.append(str(output_file))
        except subprocess.CalledProcessError as e:
            logger.error(f"分析失败: {e}")
            logger.error(f"错误输出: {e.stderr}")
            if "Permission denied" in str(e.stderr):
                logger.error("提示: 可能需要 sudo 权限，尝试: sudo python profile_with_pyspy.py ...")
        except KeyboardInterrupt:
            logger.info("分析被用户中断")
            if output_file.exists():
                results.append(str(output_file))
            break
    
    return results[0] if results else None


def top_mode(pid: int, duration: Optional[int] = None, interval: int = 1):
    """
    实时显示性能统计（类似 top 命令）
    
    Args:
        pid: 进程ID
        duration: 运行时长（秒），None 表示持续运行
        interval: 刷新间隔（秒）
    """
    if not check_pyspy_installed():
        install_pyspy()
    
    cmd = ['py-spy', 'top', '--pid', str(pid)]
    
    if duration:
        cmd.extend(['--duration', str(duration)])
    
    cmd.extend(['--interval', str(interval)])
    
    logger.info(f"开始实时监控进程 {pid}")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"监控失败: {e}")
        if "Permission denied" in str(e.stderr):
            logger.error("提示: 可能需要 sudo 权限")


def dump_mode(pid: int, output: Optional[str] = None):
    """
    转储当前调用栈
    
    Args:
        pid: 进程ID
        output: 输出文件路径
    """
    if not check_pyspy_installed():
        install_pyspy()
    
    if output is None:
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        output = f"dump_{pid}_{timestamp}.txt"
    
    cmd = ['py-spy', 'dump', '--pid', str(pid)]
    
    if output:
        logger.info(f"转储调用栈到: {output}")
        with open(output, 'w') as f:
            subprocess.run(cmd, stdout=f, check=True, text=True)
    else:
        subprocess.run(cmd, check=True)
    
    logger.info(f"转储完成: {output}")


def main():
    parser = argparse.ArgumentParser(
        description='使用 py-spy 进行 Python 性能分析',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 附加到进程并生成 SVG 火焰图
  python profile_with_pyspy.py --pid 12345 --duration 30 --output profile.svg
  
  # 启动新进程并分析
  python profile_with_pyspy.py --command "python rollout_run.py" --duration 60
  
  # 生成多种格式
  python profile_with_pyspy.py --pid 12345 --formats svg,flamegraph,speedscope --output-dir profiles/
  
  # 实时监控（类似 top）
  python profile_with_pyspy.py --pid 12345 --mode top
  
  # 转储调用栈
  python profile_with_pyspy.py --pid 12345 --mode dump
        """
    )
    
    parser.add_argument('--pid', type=int, help='要分析的进程ID')
    parser.add_argument('--command', type=str, help='要执行的命令（启动新进程）')
    parser.add_argument('--duration', type=int, help='采样时长（秒）')
    parser.add_argument('--output', type=str, help='输出文件路径（不含扩展名）')
    parser.add_argument('--output-dir', type=str, default='.', help='输出目录')
    parser.add_argument('--rate', type=int, default=100, help='采样频率（Hz），默认100')
    parser.add_argument('--formats', type=str, default='svg', 
                       help='输出格式，逗号分隔: svg,raw,flamegraph,speedscope (默认: svg)')
    parser.add_argument('--no-subprocesses', action='store_true', 
                       help='不包含子进程')
    parser.add_argument('--native', action='store_true', 
                       help='包含原生代码（C扩展）')
    parser.add_argument('--mode', type=str, choices=['record', 'top', 'dump'], 
                       default='record', help='运行模式（默认: record）')
    parser.add_argument('--interval', type=int, default=1, 
                       help='top 模式的刷新间隔（秒）')
    
    args = parser.parse_args()
    
    if args.mode == 'top':
        if not args.pid:
            logger.error("top 模式需要指定 --pid")
            sys.exit(1)
        top_mode(args.pid, args.duration, args.interval)
    elif args.mode == 'dump':
        if not args.pid:
            logger.error("dump 模式需要指定 --pid")
            sys.exit(1)
        dump_mode(args.pid, args.output)
    else:  # record mode
        formats = [f.strip() for f in args.formats.split(',')]
        
        if args.pid:
            output_path = profile_by_pid(
                pid=args.pid,
                duration=args.duration,
                output=args.output,
                rate=args.rate,
                subprocesses=not args.no_subprocesses,
                native=args.native,
                formats=formats
            )
            if output_path:
                logger.info(f"分析结果已保存: {output_path}")
        elif args.command:
            output_path = profile_by_command(
                command=args.command,
                duration=args.duration,
                output=args.output,
                rate=args.rate,
                subprocesses=not args.no_subprocesses,
                native=args.native,
                formats=formats
            )
            if output_path:
                logger.info(f"分析结果已保存: {output_path}")
        else:
            logger.error("必须指定 --pid 或 --command")
            parser.print_help()
            sys.exit(1)


if __name__ == '__main__':
    main()

