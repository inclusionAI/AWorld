import os
import time
from aworld.utils.common import get_local_ip
from fastapi import APIRouter

import logging
import traceback
from asyncio import Queue
from typing import AsyncGenerator, Optional
import asyncio

from aworld.models.model_response import ModelResponse
from pydantic import BaseModel, Field, PrivateAttr

from aworldspace.db.db import AworldTaskDB, SqliteTaskDB
from aworldspace.utils.job import generate_openai_chat_completion
from aworldspace.utils.log import task_logger
from base import AworldTask, AworldTaskResult, OpenAIChatCompletionForm, OpenAIChatMessage, AworldTaskForm


from config import ROOT_DIR

__STOP_TASK__ = object()

class AworldTaskExecutor(BaseModel):
    """
    task executor
    - load task from db and execute task in a loop
    - use semaphore to limit concurrent tasks
    """
    _task_db: AworldTaskDB = PrivateAttr()
    _tasks: Queue = PrivateAttr()
    max_concurrent: int = Field(default=os.environ.get("AWORLD_MAX_CONCURRENT_TASKS", 1), description="max concurrent tasks")

    def __init__(self, task_db: AworldTaskDB, max_concurrent: int = 2):
        super().__init__()
        self._task_db = task_db
        self._tasks = Queue()
        self._semaphore = asyncio.BoundedSemaphore(max_concurrent)  # 限制并发

    async def start(self):
        """
        execute task in a loop
        """
        while True:
            if self._tasks.empty():
                await self.load_task()
            task = await self._tasks.get()
            if task == __STOP_TASK__:
                logging.info("✅ task executor stop, all tasks finished")
                break
            asyncio.create_task(self._run_with_semaphore(task))


    async def stop(self):
        logging.info("🛑 task executor stop, wait for all tasks to finish")
        await self._tasks.put(__STOP_TASK__)

    async def _run_with_semaphore(self, task: AworldTask):
        """
        execute task with semaphore
        """
        start_time = time.time()
        logging.info(f"🚀 execute task#{task.task_id} start")   
        async with self._semaphore:
            start_time_in_semaphore = time.time()
            await self.execute_task(task)
        logging.info(f"✅ execute task#{task.task_id} success, wait time is {start_time_in_semaphore - start_time:.2f}s, use time {time.time() - start_time:.2f}s")

    async def load_task(self):
        tasks = await self._task_db.query_tasks_by_status(status="INIT", nums=self.max_concurrent)
        logging.info(f"🔍 load {len(tasks)} tasks from db")
        if not tasks or len(tasks) == 0:
            interval = os.environ.get("AWORLD_TASK_LOAD_INTERVAL", 10)
            logging.info(f"🔍 no task to load, wait {interval}s and retry")
            await asyncio.sleep(interval)
            return await self.load_task()
        for task in tasks:
            await self._tasks.put(task)
        return True

    async def execute_task(self, task: AworldTask):
        """
        execute task
        """
        try:
            result = await self._execute_task(task)
            task.mark_success()
            task_logger.log_task_submission(task, "execute_finished", task_result=result)
        except Exception as err:
            task.mark_failed()
            await self._task_db.update_task(task)
            traceback.print_exc()
            task_logger.log_task_submission(task, "execute_failed", details=f"err is {err}")

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
        items = []
        task_result = {}
        if isinstance(data, AsyncGenerator):
            async for item in data:
                items.append(item)
                if item.raw_response and item.raw_response.model_extra and item.raw_response.model_extra.get('node_id'):
                    if not task.node_id:
                        logging.info(
                            f"submit task#{task.task_id} success. execute pod ip is [{item.raw_response.model_extra.get('node_id')}]")
                        task.node_id = item.raw_response.model_extra.get('node_id')

                if item.content:
                    task_logger.log_task_result(task, item)
                    logging.info(f"task#{task.task_id} response data chunk is: {item}"[:500])

                if item.raw_response and item.raw_response.model_extra and item.raw_response.model_extra.get(
                        'task_output_meta'):
                    task_result = item.raw_response.model_extra.get('task_output_meta')

        elif isinstance(data, ModelResponse):
            if data.raw_response and data.raw_response.model_extra and data.raw_response.model_extra.get('node_id'):
                if not task.node_id:
                    logging.info(
                        f"submit task#{task.task_id} success. execute pod ip is [{data.raw_response.model_extra.get('node_id')}]")
                task.node_id = data.raw_response.model_extra.get('node_id')

            logging.info(f"task#{task.task_id} response data is: {data}")
            task_logger.log_task_result(task, data)
            if data.raw_response and data.raw_response.model_extra and data.raw_response.model_extra.get(
                    'task_output_meta'):
                task_result = data.raw_response.model_extra.get('task_output_meta')

        result = AworldTaskResult(task=task, data=task_result)
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

    async def load_one_unfinished_task(self) -> Optional[AworldTask]:
        tasks = await self._task_db.query_tasks_by_status(status="INIT", nums=1)
        if not tasks or len(tasks) == 0:
            return None

        cur_task = tasks[0]
        cur_task.mark_running()
        await self._task_db.update_task(cur_task)
        # from db load one task by locked and mark task running
        return cur_task

    async def get_task_result(self, task_id: str) -> Optional[AworldTaskResult]:
        task = await self._task_db.query_task_by_id(task_id)
        if task:
            task_result = await self._task_db.query_latest_task_result_by_id(task_id)
            if task_result:
                return task_result
            return AworldTaskResult(task=task)


########################################################################################
###########################   API
########################################################################################

router = APIRouter()

task_db_path = os.environ.get("AWORLD_TASK_DB_PATH", f"sqlite:///{ROOT_DIR}/db/aworld.db")

if task_db_path.startswith("sqlite://"):
    task_db = SqliteTaskDB(db_path = task_db_path)
elif task_db_path.startswith("mysql://"):
    task_db = None  # todo: add mysql task db
else:
    raise ValueError("❌ task_db_path is not a valid sqlite or mysql path")

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