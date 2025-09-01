import json
import os
import shutil
import tempfile
import time
from datetime import datetime
from typing import AsyncGenerator, Optional, List, Dict, Any
import zipfile

import numpy as np
import oss2

from aworld.metrics import MetricContext
from aworld.metrics.metric import MetricType
from aworld.metrics.template import MetricTemplate
from aworld.utils.common import get_local_ip
from fastapi import APIRouter, Query, Response, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse

import logging
import traceback
from asyncio import Queue
import asyncio

from aworld.models.model_response import ModelResponse
from pydantic import BaseModel, Field, PrivateAttr

from aworldspace.db.db import AworldTaskDB, SqliteTaskDB, PostgresTaskDB
from aworldspace.models.exception import TaskTerminatedException
from aworldspace.utils.job import generate_openai_chat_completion, call_pipeline
from aworldspace.utils.log import task_logger
from base import AworldTask, AworldTaskResult, OpenAIChatCompletionForm, OpenAIChatMessage, AworldTaskForm


from config import ROOT_DIR

__STOP_TASK__ = object()


TASK_EXECUTOR_COUNTER = MetricTemplate(
    type=MetricType.COUNTER,
    name="TASK_EXECUTOR_COUNTER",
    description="TASK_EXECUTOR_COUNTER",
    unit="1"
)

class AworldTaskExecutor(BaseModel):
    """
    task executor
    - load task from db and execute task in a loop
    - use semaphore to limit concurrent tasks
    """
    _task_db: AworldTaskDB = PrivateAttr()
    _tasks: Queue = PrivateAttr()
    max_concurrent: int = Field(default=int(os.environ.get("AWORLD_MAX_CONCURRENT_TASKS", 2)), description="max concurrent tasks")

    def __init__(self, task_db: AworldTaskDB):
        super().__init__()
        self._task_db = task_db
        self._tasks = Queue()
        self._semaphore = asyncio.BoundedSemaphore(self.max_concurrent)

    async def start(self):
        """
        execute task in a loop
        """
        await asyncio.sleep(5)
        logging.info(f"🚀[task executor] start, max concurrent is {self.max_concurrent}")
        while True:
            # load task if queue is empty and semaphore is not full
            if self._tasks.empty():
                await self.load_task()
            task = await self._tasks.get()
            if not task:
                logging.info("task is none")
                continue
            if task == __STOP_TASK__:
                logging.info("✅[task executor] stop, all tasks finished")
                break
            # acquire semaphore
            await self._semaphore.acquire()
            asyncio.create_task(self._run_task_and_release_semaphore(task))


    async def stop(self):
        logging.info("🛑 task executor stop, wait for all tasks to finish")
        await self._tasks.put(__STOP_TASK__)

    async def _run_task_and_release_semaphore(self, task: AworldTask):
        """
        execute task and release semaphore when done
        """
        start_time = time.time()
        logging.info(f"🚀[task executor] execute task#{task.task_id} start, lock acquired")
        try:
            await self.execute_task(task)
        finally:
            # release semaphore
            self._semaphore.release()
            logging.info(f"✅[task executor] execute task#{task.task_id} finished, use time {time.time() - start_time:.2f}s")

    async def load_task(self):
        interval = int(os.environ.get("AWORLD_TASK_LOAD_INTERVAL", 10))
        while True:
            # calculate the number of tasks to load
            need_load = self._semaphore._value
            if need_load <= 0:
                logging.info(f"🔍[task executor] runner is busy, wait {interval}s and retry")
                await asyncio.sleep(interval)
                continue

            try:
                # only get one
                tasks = await self._task_db.acquire_and_mark_tasks(
                    status="INIT",
                    new_status="RUNNING",
                    nums=1
                )
                
                logging.info(f"🔍[task executor] atomically acquired {len(tasks)} tasks from db (need {need_load})")

                if not tasks:
                    logging.info(f"🔍[task executor] no task to load, wait {interval}s and retry")
                    await asyncio.sleep(interval)
                    continue

                # add task to queue
                for task in tasks:
                    await self._tasks.put(task)
                    logging.info(f"✅[task executor] task#{task.task_id} queued for execution")

                # sleep for reload
                await asyncio.sleep(np.random.randint(1, 5))
                return True
            except Exception as e:
                logging.error(f"❌[task executor] failed to load tasks: {e}")
                await asyncio.sleep(interval)
                continue

    async def execute_task(self, task: AworldTask):
        """
        execute task
        """
        try:
            result = await self._execute_task(task)
            task.mark_success()
            await self._task_db.update_task(task)
            await self._task_db.save_task_result(result)
            logging.info(f"🔍[task executor] task#{task.task_id} execute success")
            MetricContext.count(TASK_EXECUTOR_COUNTER, 1,
                                {"agent_name": task.agent_id, "user_id": task.user_id, "pod_id": get_local_ip(), "success": "1"})
            task_logger.log_task_submission(task, "execute_finished", task_result=result)
        except Exception as err:
            if isinstance(err, TaskTerminatedException):
                logging.warning(f"task#{task.task_id} is terminated for others process, so not operate")
            task.mark_failed()
            await self._task_db.update_task(task)

            # add error task_result
            data = {
                "task_result": f"execute failed, err is {err} \n traceback is {traceback.format_exc()}",
                "md_file": "",
                "replays_file": f"trace_data/{datetime.now().strftime('%Y%m%d')}/{get_local_ip()}/replays/task_replay_{task.task_id}.json"
            }
            result = AworldTaskResult(task=task, server_host=get_local_ip(), data=data)
            await self._task_db.save_task_result(result)

            logging.error(f"🔍[task executor] task#{task.task_id} execute failed, err is {err} \n traceback is {traceback.format_exc()}")
            task_logger.log_task_submission(task, "execute_failed", details=f"err is {err}")
            MetricContext.count(TASK_EXECUTOR_COUNTER, 1,
                                {"agent_name": task.agent_id, "user_id": task.user_id, "pod_id": get_local_ip(), "success": "0"})

    async def _execute_task(self, task: AworldTask):

        # build params
        messages = [
            OpenAIChatMessage(role="user", content=task.agent_input)
        ]
        # call_llm_model
        form_data = OpenAIChatCompletionForm(
            model=task.agent_id,
            messages=messages,
            stream=True,
            user={
                "user_id": task.user_id,
                "session_id": task.session_id,
                "task_id": task.task_id,
                "aworld_task": task.model_dump_json()
            }
        )
        data = await generate_openai_chat_completion(form_data)
        task_result = {}
        task.node_id = get_local_ip()
        items = []
        md_file = ""
        if data.body_iterator:
            if isinstance(data.body_iterator, AsyncGenerator):

                async for item_content in data.body_iterator:
                    async def parse_item(_item_content) -> Optional[ModelResponse]:
                        if item_content == "data: [DONE]":
                            return None
                        return ModelResponse.from_openai_stream_chunk(json.loads(item_content.replace("data:", "")))

                    # if isinstance(item, ModelResponse)
                    item = await parse_item(item_content)
                    items.append(item)
                    if not item:
                        continue

                    if item.content:
                        md_file = task_logger.log_task_result(task, item)
                        logging.info(f"task#{task.task_id} response data chunk is: {item}"[:500])

                    if item.raw_response and item.raw_response and isinstance(item.raw_response, dict) and item.raw_response.get('task_output_meta'):
                        task_result = item.raw_response.get('task_output_meta')

        data = {
            "task_result": task_result,
            "md_file": md_file,
            "replays_file": f"trace_data/{datetime.now().strftime('%Y%m%d')}/{get_local_ip()}/replays/task_replay_{task.task_id}.json"
        }
        result = AworldTaskResult(task=task, server_host=get_local_ip(), data=data)
        return result


class AworldTaskManager(BaseModel):
    _task_db: AworldTaskDB = PrivateAttr()
    _task_executor: AworldTaskExecutor = PrivateAttr()

    def __init__(self, task_db: AworldTaskDB):
        super().__init__()
        self._task_db = task_db
        self._task_executor = AworldTaskExecutor(task_db=self._task_db)
    
    async def start_task_executor(self):
        asyncio.create_task(self._task_executor.start())

    async def stop_task_executor(self):
        self._task_executor.tasks.put_nowait(None)

    async def submit_task(self, task: AworldTask):
        # save to db
        await self._task_db.insert_task(task)
        # log it
        task_logger.log_task_submission(task, status="init")

        return AworldTaskResult(task = task)

    async def check_task_is_terminated(self, task_id: str):
        task = await self._task_db.query_task_by_id(task_id)
        if not task:
            return False
        logging.info(f"check_task_is_terminated {task_id} status is {task.status}")
        return task.status in ["CANCEL", "FAILED", "SUCCESS"]

    async def cancel_tasks(self, task_ids: List[str]):
        """
        Cancel multiple tasks by task_ids.
        Only tasks in INIT/RUNNING status can be cancelled.

        Args:
            task_ids (List[str]): List of task IDs to cancel

        Returns:
            Dict: A dictionary containing successful and failed operations
        """
        results = {
            "success": [],
            "failed": []
        }

        for task_id in task_ids:
            try:
                task = await self.cancel_task(task_id)
                results["success"].append({
                    "task_id": task_id,
                    "status": task.status
                })
            except Exception as e:
                results["failed"].append({
                    "task_id": task_id,
                    "error": str(e)
                })
                logging.error(f"❌ Failed to cancel task {task_id}: {e}")

        logging.info(f"✅ Batch cancel tasks completed. Success: {len(results['success'])}, Failed: {len(results['failed'])}")
        return results

    async def cancel_task(self, task_id: str):
        """
        Cancel a task by task_id.
        Only tasks in INIT/RUNNING status can be cancelled.

        Args:
            task_id (str): The ID of the task to cancel

        Raises:
            ValueError: If task not found or task status is not INIT/RUNNING
        """
        # 1. check task status is INIT/RUNNING
        task = await self._task_db.query_task_by_id(task_id)
        if not task:
            raise ValueError(f"❌ Task {task_id} not found")

        if task.status not in ["INIT", "RUNNING"]:
            raise ValueError(f"❌ Cannot cancel task {task_id}, current status is {task.status}, only INIT/RUNNING tasks can be cancelled")

        # 2. update task_status = CANCEL
        task.status = "CANCEL"
        await self._task_db.update_task(task)
        logging.info(f"✅ Successfully cancelled task {task_id}")
        return task

    async def reset_tasks(self, task_ids: List[str]):
        """
        Reset multiple tasks by task_ids.
        Only tasks in CANCEL/FAILED status can be reset.

        Args:
            task_ids (List[str]): List of task IDs to reset

        Returns:
            Dict: A dictionary containing successful and failed operations
        """
        results = {
            "success": [],
            "failed": []
        }

        for task_id in task_ids:
            try:
                task = await self.reset_task(task_id)
                results["success"].append({
                    "task_id": task_id,
                    "status": task.status
                })
            except Exception as e:
                results["failed"].append({
                    "task_id": task_id,
                    "error": str(e)
                })
                logging.error(f"❌ Failed to reset task {task_id}: {e}")

        logging.info(f"✅ Batch reset tasks completed. Success: {len(results['success'])}, Failed: {len(results['failed'])}")
        return results

    async def reset_task(self, task_id: str):
        """
        Reset a task by task_id.
        Only tasks in CANCEL/FAILED status can be reset.

        Args:
            task_id (str): The ID of the task to reset

        Raises:
            ValueError: If task not found or task status is not CANCEL/FAILED
        """
        # 1. check task status is CANCEL/FAILED
        task = await self._task_db.query_task_by_id(task_id)
        if not task:
            raise ValueError(f"❌ Task {task_id} not found")

        if task.status not in ["CANCEL", "FAILED"]:
            raise ValueError(f"❌ Cannot reset task {task_id}, current status is {task.status}, only CANCEL/FAILED tasks can be reset")

        # 2. update task_status = INIT
        task.status = "INIT"
        await self._task_db.update_task(task)
        logging.info(f"✅ Successfully reset task {task_id}")
        return task

    async def get_task_result(self, task_id: str) -> Optional[AworldTaskResult]:
        task = await self._task_db.query_task_by_id(task_id)
        if task:
            task_result = await self._task_db.query_latest_task_result_by_id(task_id)
            if task_result:
                task_result.task = task
                return task_result
            return AworldTaskResult(task=task)

    async def get_batch_task_results(self, task_ids: List[str]) -> List[dict]:
        """
        Batch retrieve task results, returns dictionary format
        Each dict contains: task (required) and task_result (may be None)
        """
        results = []
        for task_id in task_ids:
            task = await self._task_db.query_task_by_id(task_id)

            if task:
                task_result = await self._task_db.query_latest_task_result_by_id(task_id)
                
                result_dict = {
                    "task": task,
                    "task_result": task_result  # May be None
                }
                results.append(result_dict)
        return results

    async def query_and_download_task_results(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        task_id: Optional[str] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        status: Optional[str] = None,
        page_size: int = 100
    ) -> List[dict]:
        """
        Query tasks and get results, support time range and task_id filtering
        """
        all_results = []
        page_num = 1

        logging.info(f"🚀 [TASK_DOWNLOAD] query_and_download_task_results start, start_time: {start_time}, end_time: {end_time}, task_id: {task_id}, user_id: {user_id}, agent_id: {agent_id}, status: {status}, page_size: {page_size}")
        
        while True:
            # Build query filter conditions
            filter_dict = {}
            if start_time:
                filter_dict['start_time'] = start_time
            if end_time:
                filter_dict['end_time'] = end_time
            if task_id:
                filter_dict['task_id'] = task_id
            if user_id:
                filter_dict['user_id'] = user_id
            if agent_id:
                filter_dict['agent_id'] = agent_id
            if status:
                filter_dict['status'] = status

            # Page query tasks
            page_start_time = time.time()
            page_result = await self._task_db.page_query_tasks(
                filter=filter_dict, 
                page_size=page_size, 
                page_num=page_num
            )
            logging.info(f"🚀 [TASK_DOWNLOAD] query_and_download_task_results page_result: use_time: {time.time() - page_start_time:.2f}s")
            
            if not page_result['items']:
                break
                
            tasks = page_result['items']
            
            # Batch query task results
            task_ids = [task.task_id for task in tasks]

            task_start_time = time.time()
            task_results = await self._task_db.query_latest_task_results_by_ids(task_ids)
            logging.info(f"🚀 [TASK_DOWNLOAD] query_and_download_task_results use_time: {time.time() - task_start_time:.2f}s")
            
            # Build results using batch queried data
            for task in tasks:
                task_result = task_results.get(task.task_id)
                result_data = {
                    "task_id": task.task_id,
                    "agent_id": task.agent_id,
                    "status": task.status,
                    "created_at": task.created_at.isoformat() if task.created_at else None,
                    "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                    "user_id": task.user_id,
                    "session_id": task.session_id,
                    "node_id": task.node_id,
                    "client_id": task.client_id,
                    "task_data": task.model_dump(mode='json'),
                    "has_result": task_result is not None,
                    "server_host": task_result.server_host if task_result else None,
                    "result_data": task_result.data if task_result else None,
                }
                all_results.append(result_data)
            
            if len(page_result['items']) < page_size:
                break

            logging.info(f"🚀 [TASK_DOWNLOAD] query_and_download_task_results page_num: {page_num}, page_size: {page_size}")
                
            page_num += 1
        
        return all_results


########################################################################################
###########################   API
########################################################################################

router = APIRouter()

task_db_path = os.environ.get("AWORLD_TASK_DB_PATH", f"sqlite:///{ROOT_DIR}/db/aworld.db")

if task_db_path.startswith("sqlite://"):
    task_db = SqliteTaskDB(db_path = task_db_path)
elif task_db_path.startswith("mysql://"):
    task_db = None  # todo: add mysql task db
elif task_db_path.startswith("postgresql://") or task_db_path.startswith("postgresql+"):
    task_db = PostgresTaskDB(db_url=task_db_path)
else:
    raise ValueError("❌ task_db_path is not a valid sqlite, mysql or postgresql path")

task_manager = AworldTaskManager(task_db)

@router.post("/submit_task")
async def submit_task(form_data: AworldTaskForm) -> Optional[AworldTaskResult]:

    logging.info(f"🚀 submit task#{form_data.task.task_id} start")
    if not form_data.task:
        raise ValueError("task is empty")

    try:
        task_result = await task_manager.submit_task(form_data.task)
        logging.info(f"✅ submit task#{form_data.task.task_id} success")
        return task_result
    except Exception as err:
        traceback.print_exc()
        logging.error(f"❌ submit task#{form_data.task.task_id} failed, err is {err}")
        raise ValueError("❌ submit task failed, please see logs for details")


@router.get("/task_result")
async def get_task_result(task_id) -> Optional[AworldTaskResult]:
    if not task_id:
        raise ValueError("❌ task_id is empty")

    logging.info(f"🚀 get task result#{task_id} start")
    try:
        task_result = await task_manager.get_task_result(task_id)
        logging.info(f"✅ get task result#{task_id} success, task result is {task_result}")
        return task_result
    except Exception as err:
        traceback.print_exc()
        logging.error(f"❌ get task result#{task_id} failed, err is {err}")
        raise ValueError("❌ get task result failed, please see logs for details")


OSS_PREFIX = 'aworld'

async def download_replay_file(replay_file_url: str, local_dir: str) -> Optional[str]:
    """
    Download a replay file from OSS to local directory.
    
    Args:
        replay_file_url (str): The relative path of the replay file in OSS
        local_dir (str): The local directory to save the downloaded file
        
    Returns:
        Optional[str]: The local path of the downloaded file if successful, None otherwise
    """
    oss_url = os.path.join(OSS_PREFIX, replay_file_url)

    try:
        auth = oss2.Auth(os.environ["OSS_AK_ID"], os.environ["OSS_AK_SECRET"])
        bucket_name = os.environ["OSS_BUCKET"]
        endpoint = os.environ["OSS_BUCKET_URL"]
        bucket = oss2.Bucket(auth, endpoint, bucket_name)

        os.makedirs(local_dir, exist_ok=True)
        filename = os.path.basename(replay_file_url)
        local_path = os.path.join(local_dir, filename)

        await asyncio.to_thread(bucket.get_object_to_file, oss_url, local_path)
        logging.info(f"✅ Successfully downloaded {oss_url} to {local_path}")
        return local_path
    except KeyError as e:
        logging.error(f"❌ Missing OSS configuration: {e}")
        raise ValueError("OSS configuration is incomplete")
    except Exception as e:
        logging.error(f"❌ Failed to download {oss_url}: {e}")
        return None

async def download_task_replay(task_result: AworldTaskResult) -> List[str]:
    """
    Download all replay files for a specific task.
    
    Args:
        task_id (str): The ID of the task
        
    Returns:
        List[str]: List of local paths to the downloaded replay files
    """
    try:
        # Create a directory for task replays
        local_dir = os.path.join('task_replays', task_result.task.task_id)
        os.makedirs(local_dir, exist_ok=True)

        downloaded_files = []
        task_replays_file = task_result.task_replays_file
        if not task_replays_file:
            return downloaded_files
        local_path = await download_replay_file(task_replays_file, local_dir)
        if local_path:
            downloaded_files.append(local_path)
                
        logging.info(f"✅ Downloaded {len(downloaded_files)} replay files for task {task_result.task.task_id}")
        return downloaded_files
    except Exception as e:
        logging.error(f"❌ Failed to download task replays for {task_result.task.task_id}: {e}")
        raise ValueError(f"Failed to download task replays: {str(e)}")

async def create_replay_zip(task_id: str, replay_files: List[str]) -> Optional[str]:
    """
    Create a zip file containing all replay files.
    
    Args:
        task_id (str): The ID of the task
        replay_files (List[str]): List of replay file paths
        
    Returns:
        Optional[str]: Path to the created zip file if successful, None otherwise
    """
    if not replay_files:
        return None
        
    try:
        # Create a temporary zip file
        zip_path = os.path.join(tempfile.gettempdir(), f"task_{task_id}_replays.zip")
        
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in replay_files:
                if os.path.exists(file_path):
                    # Add file to zip with its basename as archive name
                    zipf.write(file_path, os.path.basename(file_path))
                    
        logging.info(f"✅ Created replay zip file at {zip_path}")
        return zip_path
    except Exception as e:
        logging.error(f"❌ Failed to create zip file for task {task_id}: {e}")
        return None

class TaskReplayRequest(BaseModel):
    """
    Request model for task replay download
    """
    task_id_list: List[str]

@router.post("/task_replays")
async def get_task_replays(request: TaskReplayRequest):
    """
    API endpoint to download task replays as a zip file for multiple tasks.
    
    Args:
        request (TaskReplayRequest): Request body containing list of task IDs
        
    Returns:
        FileResponse: Zip file containing all replay files
        or
        JSONResponse: Error response if download fails
    """
    task_id_list = request.task_id_list
    if not task_id_list:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": "❌ task_id_list is empty"}
        )

    logging.info(f"🚀 Downloading replays for tasks: {task_id_list}")
    try:
        all_downloaded_files = []
        failed_tasks = []
        
        # Download replays for each task
        for task_id in task_id_list:
            try:
                task_result = await task_manager.get_task_result(task_id)
                if not task_result:
                    failed_tasks.append({"task_id": task_id, "reason": "Task not found or not completed"})
                    continue

                # Download replay files for this task
                downloaded_files = await download_task_replay(task_result)
                if downloaded_files:
                    all_downloaded_files.extend(downloaded_files)
                else:
                    failed_tasks.append({"task_id": task_id, "reason": "No replay files found"})
                    
            except Exception as e:
                failed_tasks.append({"task_id": task_id, "reason": str(e)})
                logging.error(f"❌ Failed to download replays for task {task_id}: {e}")
                
        if not all_downloaded_files:
            return JSONResponse(
                status_code=404,
                content={
                    "status": "error", 
                    "error": "⚠️ No replay files found for any tasks",
                    "failed_tasks": failed_tasks
                }
            )
            
        # Create zip file with all replays
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_filename = f"task_replays_{timestamp}.zip"
        zip_path = os.path.join(tempfile.gettempdir(), zip_filename)
        
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in all_downloaded_files:
                if os.path.exists(file_path):
                    # Add file to zip with task_id prefix to avoid name conflicts
                    task_id = os.path.basename(os.path.dirname(file_path))
                    archive_name = f"{task_id}/{os.path.basename(file_path)}"
                    zipf.write(file_path, archive_name)
                    
        logging.info(f"✅ Created replay zip file at {zip_path} with {len(all_downloaded_files)} files")
        
        # Clean up downloaded files after sending response
        def cleanup_files():
            try:
                # Remove individual replay files and their directories
                task_dirs = set(os.path.dirname(f) for f in all_downloaded_files)
                for dir_path in task_dirs:
                    if os.path.exists(dir_path):
                        shutil.rmtree(dir_path)
                # Remove the zip file
                if os.path.exists(zip_path):
                    os.remove(zip_path)
            except Exception as e:
                logging.error(f"❌ Failed to cleanup files: {e}")
        
        return FileResponse(
            path=zip_path,
            filename=zip_filename,
            media_type="application/zip",
            background=cleanup_files
        )
        
    except Exception as err:
        traceback.print_exc()
        logging.error(f"❌ Failed to download replays: {err}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(err)}
        )

class BatchTaskRequest(BaseModel):
    """
    Request model for task replay download
    """
    task_ids: List[str]


@router.post("/cancel_tasks")
async def cancel_tasks(request: BatchTaskRequest):
    if not request or not request.task_ids or len(request.task_ids) == 0:
        raise ValueError("❌ task_ids is empty")

    logging.info(f"🚀 cancel_tasks start, task_ids: {request.task_ids}")
    try:
        result = await task_manager.cancel_tasks(request.task_ids)
        logging.info(f"✅ cancel_tasks success, found {len(request.task_ids)} results")
        return result
    except Exception as err:
        logging.error(f"❌ cancel_tasks failed, err is {err}, traceback is {traceback.format_exc()}")
        raise ValueError("❌ cancel_tasks failed, please see logs for details")


@router.post("/reset_tasks")
async def reset_tasks(request: BatchTaskRequest):
    if not request or not request.task_ids or len(request.task_ids) == 0:
        raise ValueError("❌ task_ids is empty")

    logging.info(f"🚀 reset_tasks start, task_ids: {request.task_ids}")
    try:
        result = await task_manager.reset_tasks(request.task_ids)
        logging.info(f"✅ reset_tasks success, found {len(request.task_ids)} results")
        return result
    except Exception as err:
        logging.error(f"❌ reset_tasks failed, err is {err}, traceback is {traceback.format_exc()}")
        raise ValueError("❌ reset_tasks failed, please see logs for details")

@router.post("/get_batch_task_results")
async def get_batch_task_results(task_ids: List[str]) -> List[dict]:
    if not task_ids or len(task_ids) == 0:
        raise ValueError("❌ task_ids is empty")

    logging.info(f"🚀 get batch task results start, task_ids: {task_ids}")
    try:
        batch_results = await task_manager.get_batch_task_results(task_ids)
        logging.info(f"✅ get batch task results success, found {len(batch_results)} results")
        return batch_results
    except Exception as err:
        traceback.print_exc()
        logging.error(f"❌ get batch task results failed, err is {err}")
        raise ValueError("❌ get batch task results failed, please see logs for details")

@router.get("/download_task_results")
async def download_task_results(
    start_time: Optional[str] = Query(None, description="Start time, format: YYYY-MM-DD HH:MM:SS"),
    end_time: Optional[str] = Query(None, description="End time, format: YYYY-MM-DD HH:MM:SS"),
    task_id: Optional[str] = Query(None, description="Task ID"),
    page_size: int = Query(100, description="Page size, ge=1, le=1000"),
    user_id: Optional[str] = Query(None, description="User ID"),
    agent_id: Optional[str] = Query(None, description="Agent ID"),
    status: Optional[str] = Query(None, description="Status")
) -> StreamingResponse:
    """
    Download task results, generate jsonl format file
    Query parameters support: time range (based on creation time), task_id
    """
    logging.info(f"🚀 download task results start, start_time: {start_time}, end_time: {end_time}, task_id: {task_id}")
    
    try:
        start_datetime = None
        end_datetime = None
        
        if start_time:
            try:
                start_datetime = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                raise ValueError("❌ start_time格式错误，请使用 YYYY-MM-DD HH:MM:SS 格式")
                
        if end_time:
            try:
                end_datetime = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                raise ValueError("❌ end_time格式错误，请使用 YYYY-MM-DD HH:MM:SS 格式")
        
        results = await task_manager.query_and_download_task_results(
            start_time=start_datetime,
            end_time=end_datetime,
            task_id=task_id,
            page_size=page_size,
            user_id=user_id,
            agent_id=agent_id,
            status=status
        )
        
        if not results:
            logging.info("📄 no task results found")

            def generate_empty():
                yield ""
            
            return StreamingResponse(
                generate_empty(),
                media_type="application/jsonl",
                headers={"Content-Disposition": "attachment; filename=task_results_empty.jsonl"}
            )
        
        # Generate jsonl content
        def generate_jsonl():
            for result in results:
                yield json.dumps(result, ensure_ascii=False) + "\n"
        
        # Generate file name
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"task_results_{timestamp}.jsonl"
        
        logging.info(f"✅ download task results success, total: {len(results)} results")
        
        return StreamingResponse(
            generate_jsonl(),
            media_type="application/jsonl",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except Exception as err:
        traceback.print_exc()
        logging.error(f"❌ download task results failed, err is {err}")
        raise ValueError(f"❌ download task results failed: {str(err)}")