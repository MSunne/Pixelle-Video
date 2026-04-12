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
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
from loguru import logger

from api.tasks.models import Task, TaskStatus, TaskType, TaskProgress
from api.config import api_config


class TaskManager:
    """
    Task manager for handling async video generation tasks
    
    Features:
    - In-memory storage (can be replaced with Redis later)
    - Task lifecycle management
    - Progress tracking
    - Auto cleanup of old tasks
    """
    
    def __init__(self):
        self._tasks: Dict[str, Task] = {}
        self._task_futures: Dict[str, asyncio.Task] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
    
    async def start(self):
        """Start task manager and cleanup scheduler"""
        if self._running:
            logger.warning("Task manager already running")
            return
        
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("✅ Task manager started")
    
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
        request_params: Optional[dict] = None
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
        )
        
        self._tasks[task_id] = task
        logger.info(f"Created task {task_id} ({task_type})")
        return task
    
    async def execute_task(
        self,
        task_id: str,
        coro_func: Callable,
        *args,
        **kwargs
    ):
        """
        Execute task asynchronously
        
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
        
        # Create async task
        async def _execute():
            try:
                task.status = TaskStatus.RUNNING
                task.started_at = datetime.now()
                logger.info(f"Task {task_id} started")
                
                # Execute the actual work
                result = await coro_func(*args, **kwargs)
                
                # Update task with result
                task.status = TaskStatus.COMPLETED
                task.result = result
                task.completed_at = datetime.now()
                logger.info(f"Task {task_id} completed")
                
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                task.status = TaskStatus.FAILED
                task.error = f"{type(e).__name__}: {e}\n\n{tb}"
                task.completed_at = datetime.now()
                logger.error(f"Task {task_id} failed: {e}\n{tb}")
        
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
        if future and not future.done():
            future.cancel()
        
        # Update task status
        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.now()
        logger.info(f"Cancelled task {task_id}")
        return True
    
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
            from pixelle_video.utils.os_util import cleanup_task_dir, get_output_path
            from pathlib import Path
            
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

