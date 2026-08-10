"""Durable seven-day account deletion worker for Himikama."""

from __future__ import annotations

import asyncio
import logging
import uuid

from api.auth import delete_firebase_auth_user_if_exists
from api.config import config
from api.firebase import (
    ClaimedAccountDeletion,
    claim_due_account_deletion,
    complete_account_deletion,
    erase_user_assessment_data,
    release_account_deletion,
)


logger = logging.getLogger(__name__)


class AccountDeletionWorker:
    """Erase due accounts without retaining legal narratives in a job queue."""

    def __init__(self, *, worker_id: str | None = None) -> None:
        self.worker_id = worker_id or f"account-deletion-{uuid.uuid4()}"
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run(),
            name="himikama-account-deletion-worker",
        )

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        self._task = None
        if task is not None:
            await task

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                claim = await claim_due_account_deletion(
                    worker_id=self.worker_id
                )
                if claim is None:
                    await self._wait_for_next_scan()
                    continue
                await self._process_claim(claim)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(
                    "Account deletion worker loop failed error_type=%s",
                    type(exc).__name__,
                )
                await self._wait_for_next_scan()

    async def _wait_for_next_scan(self) -> None:
        try:
            await asyncio.wait_for(
                self._stop_event.wait(),
                timeout=config.account_deletion_worker_poll_seconds,
            )
        except TimeoutError:
            pass

    async def _process_claim(self, claim: ClaimedAccountDeletion) -> None:
        try:
            # Erase Firestore legal records before Auth. If a later step fails,
            # retries are safe and never restore already-erased content.
            await erase_user_assessment_data(uid=claim.owner_uid)
            await delete_firebase_auth_user_if_exists(claim.owner_uid)
            completed = await complete_account_deletion(claim=claim)
            if completed:
                logger.info(
                    "Completed scheduled account deletion owner_uid=%s",
                    claim.owner_uid,
                )
            else:
                logger.warning(
                    "Discarded stale account deletion claim owner_uid=%s",
                    claim.owner_uid,
                )
        except asyncio.CancelledError:
            await release_account_deletion(claim=claim)
            raise
        except Exception as exc:
            logger.exception(
                "Scheduled account deletion failed owner_uid=%s error_type=%s",
                claim.owner_uid,
                type(exc).__name__,
            )
            await release_account_deletion(claim=claim)
