"""Lease-based durable worker for persisted Himikama analysis jobs."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

from api.config import config
from api.firebase import (
    ClaimedAnalysisJob,
    claim_next_analysis_job,
    complete_claimed_analysis_job,
    fail_claimed_analysis_job,
    get_attempt_intake,
    heartbeat_analysis_job,
    release_analysis_job,
)
from chain.runner import run_full_chain


logger = logging.getLogger(__name__)


class AnalysisWorker:
    """Process one durable Firestore job at a time with a renewable lease."""

    def __init__(self, *, worker_id: str | None = None) -> None:
        self.worker_id = worker_id or f"worker-{uuid.uuid4()}"
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run_loop(),
            name="himikama-analysis-worker",
        )
        logger.info("Durable analysis worker started")

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        logger.info("Durable analysis worker stopped")

    async def _wait_for_next_poll(self) -> None:
        try:
            await asyncio.wait_for(
                self._stop_event.wait(),
                timeout=config.analysis_worker_poll_seconds,
            )
        except TimeoutError:
            pass

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                claim = await claim_next_analysis_job(worker_id=self.worker_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Durable job scan failed error_type=%s",
                    type(exc).__name__,
                )
                await self._wait_for_next_poll()
                continue

            if claim is None:
                await self._wait_for_next_poll()
                continue

            await self._process_claim(claim)

    async def _heartbeat_loop(self, claim: ClaimedAnalysisJob) -> None:
        while True:
            await asyncio.sleep(config.analysis_job_heartbeat_seconds)
            try:
                refreshed = await heartbeat_analysis_job(claim=claim)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Analysis lease heartbeat failed attempt_id=%s "
                    "error_type=%s",
                    claim.attempt_id,
                    type(exc).__name__,
                )
                continue
            if not refreshed:
                logger.warning(
                    "Analysis lease is no longer current attempt_id=%s",
                    claim.attempt_id,
                )
                return

    async def _process_claim(self, claim: ClaimedAnalysisJob) -> None:
        logger.info(
            "Claimed durable analysis job attempt_id=%s generation=%s",
            claim.attempt_id,
            claim.generation,
        )
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(claim),
            name=f"analysis-heartbeat-{claim.attempt_id}",
        )
        try:
            intake = await get_attempt_intake(
                user_id=claim.owner_uid,
                attempt_id=claim.attempt_id,
            )
            result = await run_full_chain(intake)
            result_status = str(result.get("status") or "")
            if result_status == "failed":
                saved = await fail_claimed_analysis_job(
                    claim=claim,
                    error_code="analysis_failed",
                )
            else:
                saved = await complete_claimed_analysis_job(
                    claim=claim,
                    result=result,
                )
            if not saved:
                logger.warning(
                    "Discarded stale worker result attempt_id=%s generation=%s",
                    claim.attempt_id,
                    claim.generation,
                )
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await release_analysis_job(claim=claim)
            raise
        except Exception as exc:
            logger.exception(
                "Durable analysis failed attempt_id=%s error_type=%s",
                claim.attempt_id,
                type(exc).__name__,
            )
            try:
                saved = await fail_claimed_analysis_job(
                    claim=claim,
                    error_code="analysis_failed",
                )
                if not saved:
                    logger.warning(
                        "Failed result was stale attempt_id=%s generation=%s",
                        claim.attempt_id,
                        claim.generation,
                    )
            except Exception as persistence_exc:
                logger.error(
                    "Could not persist failed analysis attempt_id=%s "
                    "error_type=%s",
                    claim.attempt_id,
                    type(persistence_exc).__name__,
                )
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
