from __future__ import annotations

import asyncio
import copy
import sys
import time
from collections import deque
from itertools import islice
from typing import Deque, Dict, List, Optional, Tuple

import fastapi
from pydantic import BaseModel

from gradio.dataclasses import PredictBody
from gradio.utils import Request, run_coro_in_background, set_task_name


class Estimation(BaseModel):
    msg: Optional[str] = "estimation"
    rank: Optional[int] = None
    queue_size: int
    avg_event_process_time: Optional[float]
    avg_event_concurrent_process_time: Optional[float]
    rank_eta: Optional[int] = None
    queue_eta: int


class Event:
    def __init__(self, websocket: fastapi.WebSocket, fn_index: int | None = None):
        self.websocket = websocket
        self.data: PredictBody | None = None
        self.lost_connection_time: float | None = None
        self.fn_index: int | None = fn_index
        self.session_hash: str = "foo"
        self.token: str | None = None

    async def disconnect(self, code=1000):
        await self.websocket.close(code=code)


class Queue:
    def __init__(
        self,
        live_updates: bool,
        concurrency_count: int,
        data_gathering_start: int,
        update_intervals: int,
        max_size: Optional[int],
        blocks_dependencies: List,
    ):
        self.event_queue: Deque[Event] = deque()
        self.events_pending_reconnection = []
        self.stopped = False
        self.max_thread_count = concurrency_count
        self.data_gathering_start = data_gathering_start
        self.update_intervals = update_intervals
        self.active_jobs: List[None | List[Event]] = [None] * concurrency_count
        self.delete_lock = asyncio.Lock()
        self.server_path = None
        self.duration_history_total = 0
        self.duration_history_count = 0
        self.avg_process_time = None
        self.avg_concurrent_process_time = None
        self.queue_duration = 1
        self.live_updates = live_updates
        self.sleep_when_free = 0.05
        self.max_size = max_size
        self.blocks_dependencies = blocks_dependencies
        self.access_token = ""

    async def start(self):
        run_coro_in_background(self.start_processing)
        if not self.live_updates:
            run_coro_in_background(self.notify_clients)

    def close(self):
        self.stopped = True

    def resume(self):
        self.stopped = False

    def set_url(self, url: str):
        self.server_path = url

    def set_access_token(self, token: str):
        self.access_token = token

    def get_active_worker_count(self) -> int:
        count = 0
        for worker in self.active_jobs:
            if worker is not None:
                count += 1
        return count

    def get_events_in_batch(self) -> Tuple[List[Event] | None, bool]:
        if not (self.event_queue):
            return None, False

        first_event = self.event_queue.popleft()
        events = [first_event]

        event_fn_index = first_event.fn_index
        batch = self.blocks_dependencies[event_fn_index]["batch"]

        if batch:
            batch_size = self.blocks_dependencies[event_fn_index]["max_batch_size"]
            rest_of_batch = [
                event for event in self.event_queue if event.fn_index == event_fn_index
            ][: batch_size - 1]
            events.extend(rest_of_batch)
            [self.event_queue.remove(event) for event in rest_of_batch]

        return events, batch

    async def start_processing(self) -> None:
        while not self.stopped:
            if not self.event_queue:
                await asyncio.sleep(self.sleep_when_free)
                continue

            if not (None in self.active_jobs):
                await asyncio.sleep(self.sleep_when_free)
                continue
            # Using mutex to avoid editing a list in use
            async with self.delete_lock:
                events, batch = self.get_events_in_batch()

            if events:
                self.active_jobs[self.active_jobs.index(None)] = events
                task = run_coro_in_background(self.process_events, events, batch)
                run_coro_in_background(self.broadcast_live_estimations)
                set_task_name(task, events[0].session_hash, events[0].fn_index, batch)

    def push(self, event: Event) -> int | None:
        """
        Add event to queue, or return None if Queue is full
        Parameters:
            event: Event to add to Queue
        Returns:
            rank of submitted Event
        """
        queue_len = len(self.event_queue)
        if self.max_size is not None and queue_len >= self.max_size:
            return None
        self.event_queue.append(event)
        return queue_len

    async def clean_event(self, event: Event) -> None:
        if event in self.event_queue:
            async with self.delete_lock:
                self.event_queue.remove(event)

    async def broadcast_live_estimations(self) -> None:
        """
        Runs 2 functions sequentially instead of concurrently. Otherwise dced clients are tried to get deleted twice.
        """
        if self.live_updates:
            await self.broadcast_estimations()

    async def gather_data_for_first_ranks(self) -> None:
        """
        Gather data for the first x events.
        """
        # Send all messages concurrently
        await asyncio.gather(
            *[
                self.gather_event_data(event)
                for event in islice(self.event_queue, self.data_gathering_start)
            ]
        )

    async def gather_event_data(self, event: Event) -> bool:
        """
        Gather data for the event

        Parameters:
            event:
        """
        if not event.data:
            client_awake = await self.send_message(event, {"msg": "send_data"})
            if not client_awake:
                return False
            event.data = await self.get_message(event)
        return True

    async def notify_clients(self) -> None:
        """
        Notify clients about events statuses in the queue periodically.
        """
        while not self.stopped:
            await asyncio.sleep(self.update_intervals)
            if self.event_queue:
                await self.broadcast_estimations()

    async def broadcast_estimations(self) -> None:
        estimation = self.get_estimation()
        # Send all messages concurrently
        await asyncio.gather(
            *[
                self.send_estimation(event, estimation, rank)
                for rank, event in enumerate(self.event_queue)
            ]
        )

    async def send_estimation(
        self, event: Event, estimation: Estimation, rank: int
    ) -> Estimation:
        """
        Send estimation about ETA to the client.

        Parameters:
            event:
            estimation:
            rank:
        """
        estimation.rank = rank

        if self.avg_concurrent_process_time is not None:
            estimation.rank_eta = (
                estimation.rank * self.avg_concurrent_process_time
                + self.avg_process_time
            )
            if None not in self.active_jobs:
                # Add estimated amount of time for a thread to get empty
                estimation.rank_eta += self.avg_concurrent_process_time
        client_awake = await self.send_message(event, estimation.dict())
        if not client_awake:
            await self.clean_event(event)
        return estimation

    def update_estimation(self, duration: float) -> None:
        """
        Update estimation by last x element's average duration.

        Parameters:
            duration:
        """
        self.duration_history_total += duration
        self.duration_history_count += 1
        self.avg_process_time = (
            self.duration_history_total / self.duration_history_count
        )
        self.avg_concurrent_process_time = self.avg_process_time / min(
            self.max_thread_count, self.duration_history_count
        )
        self.queue_duration = self.avg_concurrent_process_time * len(self.event_queue)

    def get_estimation(self) -> Estimation:
        return Estimation(
            queue_size=len(self.event_queue),
            avg_event_process_time=self.avg_process_time,
            avg_event_concurrent_process_time=self.avg_concurrent_process_time,
            queue_eta=self.queue_duration,
        )

    async def call_prediction(self, events: List[Event], batch: bool):
        data = events[0].data
        token = events[0].token
        if batch:
            data.data = list(zip(*[event.data.data for event in events if event.data]))
            data.batched = True
        response = await Request(
            method=Request.Method.POST,
            url=f"{self.server_path}api/predict",
            json=dict(data),
            headers={"Authorization": f"Bearer {self.access_token}"},
            cookies={"access-token": token} if token is not None else None,
        )
        return response

    async def process_events(self, events: List[Event], batch: bool) -> None:
        awake_events: List[Event] = []
        try:
            for event in events:
                client_awake = await self.gather_event_data(event)
                if client_awake:
                    client_awake = await self.send_message(
                        event, {"msg": "process_starts"}
                    )
                if client_awake:
                    awake_events.append(event)
            if not (awake_events):
                return
            begin_time = time.time()
            response = await self.call_prediction(awake_events, batch)
            if response.has_exception:
                for event in awake_events:
                    await self.send_message(
                        event,
                        {
                            "msg": "process_completed",
                            "output": {"error": str(response.exception)},
                            "success": False,
                        },
                    )
            elif response.json.get("is_generating", False):
                while response.json.get("is_generating", False):
                    # Python 3.7 doesn't have named tasks.
                    # In order to determine if a task was cancelled, we
                    # ping the websocket to see if it was closed mid-iteration.
                    if sys.version_info < (3, 8):
                        is_alive = await self.send_message(event, {"msg": "alive?"})
                        if not is_alive:
                            return
                    old_response = response
                    for event in awake_events:
                        await self.send_message(
                            event,
                            {
                                "msg": "process_generating",
                                "output": old_response.json,
                                "success": old_response.status == 200,
                            },
                        )
                    response = await self.call_prediction(awake_events, batch)
                for event in awake_events:
                    await self.send_message(
                        event,
                        {
                            "msg": "process_completed",
                            "output": old_response.json,
                            "success": old_response.status == 200,
                        },
                    )
            else:
                output = copy.deepcopy(response.json)
                for e, event in enumerate(awake_events):
                    if batch and "data" in output:
                        output["data"] = list(zip(*response.json.get("data")))[e]
                    await self.send_message(
                        event,
                        {
                            "msg": "process_completed",
                            "output": output,
                            "success": response.status == 200,
                        },
                    )
            end_time = time.time()
            if response.status == 200:
                self.update_estimation(end_time - begin_time)
        finally:
            for event in awake_events:
                try:
                    await event.disconnect()
                except Exception:
                    pass
            self.active_jobs[self.active_jobs.index(events)] = None
            for event in awake_events:
                await self.clean_event(event)
                # Always reset the state of the iterator
                # If the job finished successfully, this has no effect
                # If the job is cancelled, this will enable future runs
                # to start "from scratch"
                await self.reset_iterators(event.session_hash, event.fn_index)

    async def send_message(self, event, data: Dict) -> bool:
        try:
            await event.websocket.send_json(data=data)
            return True
        except:
            await self.clean_event(event)
            return False

    async def get_message(self, event) -> Optional[PredictBody]:
        try:
            data = await event.websocket.receive_json()
            return PredictBody(**data)
        except:
            await self.clean_event(event)
            return None

    async def reset_iterators(self, session_hash: str, fn_index: int):
        await Request(
            method=Request.Method.POST,
            url=f"{self.server_path}reset",
            json={
                "session_hash": session_hash,
                "fn_index": fn_index,
            },
        )
