# import sys
# from multiprocessing import Process

# from aworld.logs.util import logger

# from .audio import mcptranscribe
# from .image import mcpocr, mcpreasoning
# from .utils import run_mcp_server
# from .video import mcpanalyze, mcpextractsubtitles, mcpsummarize

__author__ = "qingw-dev"
__version__ = "1.0.0"

processes = []
# processes.append(
#     Process(
#         target=run_mcp_server,
#         args=("Image Server", [mcpocr, mcpreasoning], 1111),
#     )
# )
# processes.append(
#     Process(
#         target=run_mcp_server,
#         args=("Audio Server", [mcptranscribe], 2222),
#     )
# )
# processes.append(
#     Process(
#         target=run_mcp_server,
#         args=("Video Server", [mcpanalyze, mcpextractsubtitles, mcpsummarize], 3333),
#     )
# )

# Start server in a process
# for process in processes:
#     process.start()

# try:
#     for process in processes:
#         process.join()
# except KeyboardInterrupt:
#     logger.info("Received keyboard interrupt, shutting down all servers...")
#     for process in processes:
#         if process.is_alive():
#             process.terminate()
#             process.join()
#     sys.exit(0)
