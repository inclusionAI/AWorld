# Asyncio Monitor

A powerful monitoring tool for Python's asyncio event loops that helps you track, analyze, and debug asynchronous tasks in real-time.

## Features
- **Task Monitoring**: Tracks all asyncio tasks, their states, and execution times
- **Slow Task Detection**: Identifies and logs tasks that exceed configurable execution time thresholds
- **Blocking Location Detection**: Pinpoints synchronous code that might be blocking your asyncio event loop
- **Pending Task Analysis**: Identifies and reports common reasons for pending tasks
- **Real-time Reporting**: Generates periodic reports with detailed statistics and stack traces
- **Context Manager Support**: Can be used with Python's `with` statement for automatic cleanup
- **Customizable Configuration**: Fine-tune monitoring behavior with various parameters

## Usage

### Basic Usage with Context Manager (Recommended)

```python
import asyncio
from aworld.trace.asyncio_monitor.base import AsyncioMonitor

async def main():
    # Create and start the monitor using context manager
    with AsyncioMonitor(detect_duration_second=2) as monitor:
        # Your async code here
        await some_async_operation()
    # Monitor automatically stops when exiting the context

asyncio.run(main())
```

### Manual Start/Stop

```python
import asyncio
from aworld.trace.asyncio_monitor.base import AsyncioMonitor

async def main():
    # Create the monitor
    monitor = AsyncioMonitor(slow_task_ms=500)
    
    # Start monitoring
    monitor.start()
    
    try:
        # Your async code here
        await some_async_operation()
    finally:
        # Stop monitoring
        monitor.stop()

asyncio.run(main())
```
### Configuration Options

When creating an `AsyncioMonitor` instance, you can customize its behavior with these parameters:

- `loop`: The event loop to monitor (defaults to current event loop)
- `hot_location_top_n`: Number of top locations to report (default: 5)
- `detect_duration_second`: Interval between reports in seconds (default: 5)
- `shot_file_name`: Whether to show just filenames (True) or full paths (False) (default: True)
- `report_table_width`: Width of the report tables (default: 100)
- `slow_task_ms`: Threshold in milliseconds for slow task detection (default: 1000)