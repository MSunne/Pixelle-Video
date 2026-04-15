# Copyright (C) 2025 AIDC-AI
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Task Manager

In-memory task management for video generation jobs.
"""

import asyncio
import contextvars
import uuid
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from loguru import logger

from api.config import api_config
from api.tasks.models import DownstreamTask, Task, TaskProgress, TaskStatus, TaskType

_current_task_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "pixelle_current_task_id",
    default=None,
)


class TaskManager:
    """
    Task manager for handling async video generation tasks
    
    Features:
    - In-memory storage (can be replaced with Redis later)
    - Task lifecycle management
    - Progress tracking
    - Concurrency control via asyncio.Semaphore
    - Automatic retry with exponential backoff
    - Task timeout protection
    - Auto cleanup of old tasks
    """
    
    # Error messages / exception substrings that indicate a transient failure
    # worth retrying (e.g. RunningHub rate limits, network hiccups).
    _RETRYABLE_PATTERNS = (
        "timeout",
        "Timeout",
        "ConnectionError",
        "ConnectError",
        "RemoteDisconnected",
        "ServerDisconnected",
        "502",
        "503",
        "504",
    )
    
    def __init__(self):
        self._tasks: Dict[str, Task] = {}
        self._task_futures: Dict[str, asyncio.Task] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._task_type_semaphores: Dict[TaskType, asyncio.Semaphore] = {}
    
    async def start(self):
        """Start task manager and cleanup scheduler"""
        if self._running:
            logger.warning("Task manager already running")
            return
        
        self._running = True
        self._semaphore = asyncio.Semaphore(api_config.max_concurrent_tasks)
        self._task_type_semaphores = {
            TaskType.DIGITAL_HUMAN_VIDEO: asyncio.Semaphore(
                api_config.digital_human_max_concurrent_tasks
            )
        }
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info(f"✅ Task manager started (max_concurrent={api_config.max_concurrent_tasks}, "
                    f"digital_human_max_concurrent={api_config.digital_human_max_concurrent_tasks}, "
                    f"digital_human_max_active={api_config.digital_human_max_active_tasks}, "
                    f"timeout={api_config.task_timeout}s, max_retries={api_config.task_max_retries})")
    
    async def stop(self):
        """Stop task manager and cancel all tasks"""
        self._running = False
        
        # Cancel cleanup task
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        # Cancel all running tasks
        for task_id, future in self._task_futures.items():
            if not future.done():
                future.cancel()
                logger.info(f"Cancelled task: {task_id}")
        
        self._tasks.clear()
        self._task_futures.clear()
        logger.info("✅ Task manager stopped")
    
    def create_task(
        self,
        task_type: TaskType,
        request_params: Optional[dict] = None,
        request_fingerprint: Optional[str] = None,
    ) -> Task:
        """
        Create a new task
        
        Args:
            task_type: Type of task
            request_params: Original request parameters
            
        Returns:
            Created task
        """
        task_id = str(uuid.uuid4())
        task = Task(
            task_id=task_id,
            task_type=task_type,
            status=TaskStatus.PENDING,
            request_params=request_params,
            request_fingerprint=request_fingerprint,
        )
        
        self._tasks[task_id] = task
        logger.info(f"Created task {task_id} ({task_type})")
        return task
    
    def _is_retryable(self, error: Exception) -> bool:
        """Check if an error is worth retrying"""
        retryable_attr = getattr(error, "retryable", None)
        if retryable_attr is not None:
            return bool(retryable_attr)
        error_str = str(error)
        return any(pattern in error_str for pattern in self._RETRYABLE_PATTERNS)
    
    async def execute_task(
        self,
        task_id: str,
        coro_func: Callable,
        *args,
        **kwargs
    ):
        """
        Execute task asynchronously with concurrency control, retry, and timeout.
        
        Args:
            task_id: Task ID
            coro_func: Async function to execute
            *args: Positional arguments
            **kwargs: Keyword arguments
        """
        task = self._tasks.get(task_id)
        if not task:
            logger.error(f"Task {task_id} not found")
            return
        
        max_retries = api_config.task_max_retries
        timeout = api_config.task_timeout
        
        # Create async task
        async def _execute():
            type_semaphore = self._task_type_semaphores.get(task.task_type)
            # Wait for a concurrency slot
            if self._semaphore:
                logger.debug(f"Task {task_id} waiting for concurrency slot...")
                await self._semaphore.acquire()
            if type_semaphore:
                logger.debug(f"Task {task_id} waiting for {task.task_type} slot...")
                await type_semaphore.acquire()

            token = _current_task_id_var.set(task_id)
            
            try:
                task.status = TaskStatus.RUNNING
                task.started_at = datetime.now()
                task.progress = TaskProgress(
                    current=0, total=100, percentage=0.0, message="任务已启动"
                )
                logger.info(f"Task {task_id} started")
                
                # Execute with retry and timeout
                last_error = None
                for attempt in range(max_retries + 1):
                    if task.cancel_requested:
                        raise asyncio.CancelledError()
                    try:
                        result = await asyncio.wait_for(
                            coro_func(*args, **kwargs),
                            timeout=timeout
                        )
                        
                        # Success
                        task.status = TaskStatus.COMPLETED
                        task.result = result
                        task.completed_at = datetime.now()
                        task.progress = TaskProgress(
                            current=100, total=100, percentage=100.0, message="已完成"
                        )
                        logger.info(f"Task {task_id} completed")
                        return
                        
                    except asyncio.TimeoutError:
                        last_error = TimeoutError(
                            f"Task exceeded {timeout}s timeout"
                        )
                        logger.error(f"Task {task_id} timed out after {timeout}s")
                        break  # Don't retry timeouts
                        
                    except asyncio.CancelledError:
                        task.status = TaskStatus.CANCELLED
                        task.cancel_requested = True
                        task.completed_at = datetime.now()
                        logger.info(f"Task {task_id} cancelled")
                        return
                        
                    except Exception as e:
                        last_error = e
                        if attempt < max_retries and self._is_retryable(e):
                            wait_time = 2 ** attempt * 5  # 5s, 10s
                            logger.warning(
                                f"Task {task_id} attempt {attempt+1}/{max_retries+1} failed "
                                f"(retryable), waiting {wait_time}s: {e}"
                            )
                            task.progress = TaskProgress(
                                current=0, total=100, percentage=0.0,
                                message=f"第{attempt+1}次重试中，等待{wait_time}秒..."
                            )
                            await asyncio.sleep(wait_time)
                        else:
                            break  # Non-retryable or exhausted retries
                
                # All retries exhausted
                import traceback
                tb = traceback.format_exc()
                task.status = TaskStatus.FAILED
                task.error = f"{type(last_error).__name__}: {last_error}\n\n{tb}"
                task.completed_at = datetime.now()
                task.progress = TaskProgress(
                    current=0, total=100, percentage=0.0,
                    message=f"任务失败: {last_error}"
                )
                logger.error(f"Task {task_id} failed: {last_error}")
                
            finally:
                # Release concurrency slot
                if self._semaphore:
                    self._semaphore.release()
                if type_semaphore:
                    type_semaphore.release()
                _current_task_id_var.reset(token)
        
        # Start execution
        future = asyncio.create_task(_execute())
        self._task_futures[task_id] = future
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID"""
        return self._tasks.get(task_id)
    
    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        limit: int = 100
    ) -> List[Task]:
        """
        List tasks with optional filtering
        
        Args:
            status: Filter by status
            limit: Maximum number of tasks to return
            
        Returns:
            List of tasks
        """
        tasks = list(self._tasks.values())
        
        if status:
            tasks = [t for t in tasks if t.status == status]
        
        # Sort by created_at descending
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        
        return tasks[:limit]
    
    def update_progress(
        self,
        task_id: str,
        current: int,
        total: int,
        message: str = ""
    ):
        """
        Update task progress
        
        Args:
            task_id: Task ID
            current: Current progress
            total: Total steps
            message: Progress message
        """
        task = self._tasks.get(task_id)
        if not task:
            return
        
        percentage = (current / total * 100) if total > 0 else 0
        task.progress = TaskProgress(
            current=current,
            total=total,
            percentage=percentage,
            message=message
        )
    
    def cancel_task(self, task_id: str) -> bool:
        """
        Cancel a running task
        
        Args:
            task_id: Task ID
            
        Returns:
            True if cancelled, False otherwise
        """
        task = self._tasks.get(task_id)
        if not task:
            return False
        
        # Cancel future if running
        future = self._task_futures.get(task_id)
        task.cancel_requested = True
        if future and not future.done():
            future.cancel()
        
        # Update task status
        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.now()
        logger.info(f"Cancelled task {task_id}")
        return True

    def find_active_task_by_fingerprint(
        self,
        task_type: TaskType,
        request_fingerprint: str,
    ) -> Optional[Task]:
        """Find an active task matching the same normalized request fingerprint."""
        for task in self._tasks.values():
            if task.task_type != task_type:
                continue
            if task.request_fingerprint != request_fingerprint:
                continue
            if task.cancel_requested:
                continue
            if task.status in {TaskStatus.PENDING, TaskStatus.RUNNING}:
                return task
        return None

    def count_active_tasks(self, task_type: Optional[TaskType] = None) -> int:
        """Count pending/running tasks, optionally filtered by task type."""
        return sum(
            1
            for task in self._tasks.values()
            if task.status in {TaskStatus.PENDING, TaskStatus.RUNNING}
            and not task.cancel_requested
            and (task_type is None or task.task_type == task_type)
        )

    def get_current_task_id(self) -> Optional[str]:
        """Get the currently executing local task ID from context."""
        return _current_task_id_var.get()

    def get_current_task(self) -> Optional[Task]:
        """Get the currently executing local task object from context."""
        current_task_id = self.get_current_task_id()
        if not current_task_id:
            return None
        return self.get_task(current_task_id)

    def is_task_cancel_requested(self, task_id: Optional[str] = None) -> bool:
        """Check whether the task has been marked for cancellation."""
        resolved_task_id = task_id or self.get_current_task_id()
        if not resolved_task_id:
            return False
        task = self._tasks.get(resolved_task_id)
        return bool(task and task.cancel_requested)

    def record_downstream_task(
        self,
        *,
        step: str,
        workflow_id: Optional[str] = None,
        downstream_task_id: Optional[str] = None,
        status: str,
        message: Optional[str] = None,
        provider: str = "runninghub",
        task_id: Optional[str] = None,
    ) -> Optional[DownstreamTask]:
        """Create or update downstream execution trace for the current task."""
        resolved_task_id = task_id or self.get_current_task_id()
        if not resolved_task_id:
            return None
        task = self._tasks.get(resolved_task_id)
        if not task:
            return None

        existing = None
        if downstream_task_id:
            existing = next(
                (
                    item
                    for item in task.downstream_tasks
                    if item.downstream_task_id == downstream_task_id
                ),
                None,
            )

        if existing is None and downstream_task_id is None and workflow_id:
            existing = next(
                (
                    item
                    for item in reversed(task.downstream_tasks)
                    if item.step == step
                    and item.workflow_id == workflow_id
                    and item.downstream_task_id is None
                ),
                None,
            )

        if existing:
            existing.status = status
            existing.message = message
            existing.updated_at = datetime.now()
            if workflow_id:
                existing.workflow_id = workflow_id
            if downstream_task_id:
                existing.downstream_task_id = downstream_task_id
            return existing

        attempt = 1 + sum(
            1
            for item in task.downstream_tasks
            if item.step == step and item.workflow_id == workflow_id
        )
        entry = DownstreamTask(
            provider=provider,
            step=step,
            workflow_id=workflow_id,
            downstream_task_id=downstream_task_id,
            attempt=attempt,
            status=status,
            message=message,
        )
        task.downstream_tasks.append(entry)
        return entry
    
    async def _cleanup_loop(self):
        """Periodically clean up old completed tasks"""
        while self._running:
            try:
                await asyncio.sleep(api_config.task_cleanup_interval)
                self._cleanup_old_tasks()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")
    
    def _cleanup_old_tasks(self):
        """Remove old completed/failed tasks and their local files"""
        cutoff_time = datetime.now() - timedelta(seconds=api_config.task_retention_time)
        
        tasks_to_remove = []
        for task_id, task in self._tasks.items():
            if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                if task.completed_at and task.completed_at < cutoff_time:
                    tasks_to_remove.append(task_id)
        
        for task_id in tasks_to_remove:
            # Safety net: clean up local task directory if it still exists
            self._cleanup_task_files(self._tasks[task_id])
            
            del self._tasks[task_id]
            if task_id in self._task_futures:
                del self._task_futures[task_id]
        
        if tasks_to_remove:
            logger.info(f"Cleaned up {len(tasks_to_remove)} old tasks")
    
    @staticmethod
    def _cleanup_task_files(task: Task):
        """
        Safety net: remove any leftover local files for a task.
        
        Scans the output directory for matching task directories based on
        the video URL path segments or request parameters.
        """
        try:
            from pathlib import Path

            from pixelle_video.utils.os_util import cleanup_task_dir, get_output_path
            
            cleaned = False
            
            # Strategy 1: Extract task directory name from video_url path
            # Works for both local URLs (http://host/api/files/20260411_143427_e5c4/final.mp4)
            # and relative paths (/api/files/20260411_143427_e5c4/final.mp4)
            if task.result and isinstance(task.result, dict):
                video_url = task.result.get("video_url", "")
                if video_url:
                    # Extract path segments to find task directory name
                    # Pattern: .../api/files/{task_id}/final.mp4 or .../output/{task_id}/final.mp4
                    import re
                    match = re.search(r'(\d{8}_\d{6}_[a-f0-9]{4})', video_url)
                    if match:
                        task_dir_name = match.group(1)
                        task_dir_path = Path(get_output_path(task_dir_name))
                        if task_dir_path.is_dir():
                            cleanup_task_dir(str(task_dir_path))
                            cleaned = True
            
            # Strategy 2: Check request params for task_dir hints
            if not cleaned and task.request_params:
                task_dir = task.request_params.get("task_dir")
                if task_dir:
                    cleanup_task_dir(str(task_dir))
                    
        except Exception as e:
            logger.debug(f"Task file cleanup skipped for {task.task_id}: {e}")



# Global task manager instance
task_manager = TaskManager()
