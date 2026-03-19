"""Lium-style collateral tracking with slash events."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from greenference_protocol import CollateralRecord


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CollateralError(RuntimeError):
    def __init__(self, message: str, *, failure_class: str = "collateral_error") -> None:
        super().__init__(message)
        self.failure_class = failure_class


class CollateralManager:
    """Tracks collateral balances and slash events per hotkey. EVM settlement is stubbed."""

    def __init__(self) -> None:
        self._records: dict[str, CollateralRecord] = {}

    def post_collateral(self, hotkey: str, amount_tao: float) -> CollateralRecord:
        record = self._records.get(hotkey) or CollateralRecord(hotkey=hotkey)
        if record.locked:
            raise CollateralError(
                f"collateral is locked for hotkey={hotkey}",
                failure_class="collateral_locked",
            )
        record = record.model_copy(
            update={
                "amount_tao": record.amount_tao + amount_tao,
                "updated_at": _utcnow(),
            }
        )
        self._records[hotkey] = record
        return record

    def slash(self, hotkey: str, reason: str, amount_tao: float) -> CollateralRecord:
        record = self._records.get(hotkey)
        if record is None:
            raise CollateralError(
                f"no collateral found for hotkey={hotkey}",
                failure_class="collateral_not_found",
            )
        slash_event: dict[str, Any] = {
            "reason": reason,
            "amount_tao": amount_tao,
            "slashed_at": _utcnow().isoformat(),
        }
        new_amount = max(0.0, record.amount_tao - amount_tao)
        record = record.model_copy(
            update={
                "amount_tao": new_amount,
                "slash_events": [*record.slash_events, slash_event],
                "updated_at": _utcnow(),
            }
        )
        self._records[hotkey] = record
        return record

    def reclaim(self, hotkey: str) -> CollateralRecord:
        record = self._records.get(hotkey)
        if record is None:
            raise CollateralError(
                f"no collateral found for hotkey={hotkey}",
                failure_class="collateral_not_found",
            )
        if record.locked:
            raise CollateralError(
                f"collateral is locked for hotkey={hotkey}",
                failure_class="collateral_locked",
            )
        record = record.model_copy(
            update={
                "amount_tao": 0.0,
                "updated_at": _utcnow(),
            }
        )
        self._records[hotkey] = record
        return record

    def lock(self, hotkey: str) -> CollateralRecord:
        record = self._records.get(hotkey) or CollateralRecord(hotkey=hotkey)
        record = record.model_copy(update={"locked": True, "updated_at": _utcnow()})
        self._records[hotkey] = record
        return record

    def unlock(self, hotkey: str) -> CollateralRecord:
        record = self._records.get(hotkey) or CollateralRecord(hotkey=hotkey)
        record = record.model_copy(update={"locked": False, "updated_at": _utcnow()})
        self._records[hotkey] = record
        return record

    def get_collateral(self, hotkey: str) -> CollateralRecord | None:
        return self._records.get(hotkey)

    def list_collateral(self) -> list[CollateralRecord]:
        return sorted(self._records.values(), key=lambda r: r.updated_at, reverse=True)

    def state(self) -> dict[str, Any]:
        return {r.hotkey: r.model_dump(mode="json") for r in self._records.values()}

    def load_state(self, state: dict[str, Any]) -> None:
        for hotkey, record_dict in state.items():
            if isinstance(record_dict, dict):
                self._records[hotkey] = CollateralRecord(**record_dict)
