"""Helpers for tracked RunningHub execution with Pixelle task awareness."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from comfykit.comfyui.models import ExecuteResult
from comfykit.comfyui.workflow_parser import WorkflowParser
from loguru import logger

from api.tasks import task_manager


class DownstreamWorkflowError(RuntimeError):
    """Non-retryable downstream workflow failure."""

    retryable = False


class RetryableDownstreamCreateError(RuntimeError):
    """Retryable downstream create failure before a task ID exists."""

    retryable = True


def _is_connection_like_error(message: str) -> bool:
    return any(
        token in message
        for token in (
            "ConnectionError",
            "ConnectError",
            "RemoteDisconnected",
            "ServerDisconnected",
            "timed out",
            "timeout",
            "502",
            "503",
            "504",
        )
    )


def _is_cancel_message(message: str) -> bool:
    lowered = message.lower()
    return any(token in lowered for token in ("cancel", "cancelled", "canceled", "已取消", "取消"))


async def execute_runninghub_workflow(
    *,
    kit,
    workflow_id: str,
    params: dict[str, Any],
    step: str,
    max_wait_time: Optional[int] = None,
) -> ExecuteResult:
    """Create and monitor a single RunningHub task with local task tracing."""
    executor = kit._get_runninghub_executor()
    wait_timeout = executor.timeout if max_wait_time is None else max_wait_time

    workflow_json = await executor.client.get_workflow_json(workflow_id)
    workflow_json, seed_changes = executor._randomize_seed_in_workflow(workflow_json)

    parser = WorkflowParser()
    metadata = parser.parse_workflow(workflow_json, f"workflow_{workflow_id}")
    if not metadata:
        raise DownstreamWorkflowError(f"Failed to parse RunningHub workflow metadata: {workflow_id}")

    metadata.workflow_id = workflow_id
    metadata.is_runninghub = True

    node_info_list = await executor._convert_params_to_node_info_list(
        metadata,
        params or {},
        seed_changes,
    )
    output_id_2_var = executor._extract_output_nodes(metadata)

    try:
        task_data = await executor.client.create_task(workflow_id, node_info_list or None)
    except Exception as exc:
        message = str(exc)
        task_manager.record_downstream_task(
            step=step,
            workflow_id=workflow_id,
            downstream_task_id=None,
            status="queue_full" if "TASK_QUEUE_MAXED" in message else "create_failed",
            message=message,
        )
        if "TASK_QUEUE_MAXED" in message:
            raise DownstreamWorkflowError("RunningHub queue is busy. Please retry later.") from exc
        if _is_connection_like_error(message):
            raise RetryableDownstreamCreateError(message) from exc
        raise DownstreamWorkflowError(f"RunningHub task creation failed: {message}") from exc

    downstream_task_id = task_data.get("taskId")
    if not downstream_task_id:
        raise DownstreamWorkflowError(
            f"RunningHub task creation returned no task ID for workflow {workflow_id}"
        )

    task_manager.record_downstream_task(
        step=step,
        workflow_id=workflow_id,
        downstream_task_id=downstream_task_id,
        status="created",
    )

    start_time = time.time()
    check_interval = 2

    while True:
        if task_manager.is_task_cancel_requested():
            task_manager.record_downstream_task(
                step=step,
                workflow_id=workflow_id,
                downstream_task_id=downstream_task_id,
                status="cancelled",
                message="Cancelled by Pixelle task manager",
            )
            raise asyncio.CancelledError()

        elapsed = time.time() - start_time
        if wait_timeout is not None and elapsed >= wait_timeout:
            task_manager.record_downstream_task(
                step=step,
                workflow_id=workflow_id,
                downstream_task_id=downstream_task_id,
                status="timeout",
                message=f"Timed out after {wait_timeout}s",
            )
            raise DownstreamWorkflowError(
                f"RunningHub task {downstream_task_id} timed out after {wait_timeout} seconds"
            )

        try:
            status_info = await executor.client.query_task_status(downstream_task_id)
        except Exception as exc:
            message = str(exc)
            if task_manager.is_task_cancel_requested():
                task_manager.record_downstream_task(
                    step=step,
                    workflow_id=workflow_id,
                    downstream_task_id=downstream_task_id,
                    status="cancelled",
                    message="Cancelled by Pixelle task manager",
                )
                raise asyncio.CancelledError()
            if "APIKEY_TASK_NOT_FOUND" in message:
                task_manager.record_downstream_task(
                    step=step,
                    workflow_id=workflow_id,
                    downstream_task_id=downstream_task_id,
                    status="cancelled",
                    message=message,
                )
                raise DownstreamWorkflowError(
                    f"RunningHub task {downstream_task_id} is no longer available downstream."
                ) from exc
            logger.warning(
                "RunningHub task status query failed for {} ({}): {}",
                step,
                downstream_task_id,
                message,
            )
            await asyncio.sleep(check_interval)
            continue

        task_status = status_info.get("status", "")
        status_message = status_info.get("msg", "") or ""

        if task_status == "SUCCESS":
            task_manager.record_downstream_task(
                step=step,
                workflow_id=workflow_id,
                downstream_task_id=downstream_task_id,
                status="completed",
            )
            result_data = await executor.client.query_task_result(downstream_task_id)
            result = await executor._process_task_result(downstream_task_id, result_data, output_id_2_var)
            result.prompt_id = downstream_task_id
            return result

        if task_status == "FAILED" or _is_cancel_message(status_message):
            derived_status = "cancelled" if _is_cancel_message(status_message) else "failed"
            task_manager.record_downstream_task(
                step=step,
                workflow_id=workflow_id,
                downstream_task_id=downstream_task_id,
                status=derived_status,
                message=status_message or task_status,
            )
            if derived_status == "cancelled":
                raise DownstreamWorkflowError(
                    f"RunningHub task {downstream_task_id} was cancelled downstream."
                )
            raise DownstreamWorkflowError(
                f"RunningHub task {downstream_task_id} failed: {status_message or 'Unknown error'}"
            )

        task_manager.record_downstream_task(
            step=step,
            workflow_id=workflow_id,
            downstream_task_id=downstream_task_id,
            status=task_status.lower() if task_status else "running",
            message=status_message or None,
        )
        await asyncio.sleep(check_interval)
