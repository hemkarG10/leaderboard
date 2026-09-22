"""In-memory leaderboard for a single game.

Ordering key: (-score, achieved_seq, user_id).
Submit semantics: any *different* score replaces the stored score; an equal
score is a no-op (rank and tie-break stay stable).
"""

from __future__ import annotations

from dataclasses import dataclass

from sortedcontainers import SortedList


@dataclass(frozen=True, slots=True)
class Entry:
    user_id: str
    score: int
    achieved_seq: int


@dataclass(frozen=True, slots=True)
class SubmitResult:
    entry: Entry
    rank: int
    improved: bool  # True iff new score is strictly greater than the old one
    created: bool
    replaced: bool  # True iff the stored score changed (up or down)


@dataclass(frozen=True, slots=True)
class RankedEntry:
    rank: int
    user_id: str
    score: int


class Leaderboard:
    """Per-game board: dict for O(1) user lookup + SortedList for O(log n) rank."""

    def __init__(self) -> None:
        self.by_user: dict[str, Entry] = {}
        # Ascending sort on (-score, seq, user_id) ⇒ highest score first.
        self.ranked: SortedList[tuple[int, int, str]] = SortedList()

    def __len__(self) -> int:
        return len(self.by_user)

    def _key(self, entry: Entry) -> tuple[int, int, str]:
        return (-entry.score, entry.achieved_seq, entry.user_id)

    def submit(self, user_id: str, score: int, seq: int) -> SubmitResult:
        """Insert or replace a user's score. `seq` is assigned by infra."""
        existing = self.by_user.get(user_id)

        # Equal score: no-op — keep seq so tie-break order stays stable.
        if existing is not None and score == existing.score:
            rank = self.ranked.index(self._key(existing)) + 1
            return SubmitResult(
                entry=existing,
                rank=rank,
                improved=False,
                created=False,
                replaced=False,
            )

        new_entry = Entry(user_id=user_id, score=score, achieved_seq=seq)
        new_key = self._key(new_entry)

        # Invariant: compute new key first, then remove+add with nothing that
        # can raise between them, so by_user and ranked never diverge.
        if existing is not None:
            self.ranked.remove(self._key(existing))
        self.ranked.add(new_key)
        self.by_user[user_id] = new_entry

        rank = self.ranked.index(new_key) + 1
        improved = existing is None or score > existing.score
        return SubmitResult(
            entry=new_entry,
            rank=rank,
            improved=improved,
            created=existing is None,
            replaced=True,
        )

    def top(self, limit: int, offset: int = 0) -> list[RankedEntry]:
        if limit < 0 or offset < 0:
            raise ValueError("limit and offset must be non-negative")
        slice_ = self.ranked[offset : offset + limit]
        return [
            RankedEntry(rank=offset + i + 1, user_id=uid, score=-neg_score)
            for i, (neg_score, _seq, uid) in enumerate(slice_)
        ]

    def rank_of(self, user_id: str) -> RankedEntry | None:
        entry = self.by_user.get(user_id)
        if entry is None:
            return None
        rank = self.ranked.index(self._key(entry)) + 1
        return RankedEntry(rank=rank, user_id=user_id, score=entry.score)

    def around(
        self, user_id: str, window: int
    ) -> tuple[RankedEntry, list[RankedEntry], list[RankedEntry]] | None:
        """Return (user, above, below). window is how many neighbours each side.

        Canonical "surroundings" uses window=1 (one above, one below).
        """
        if window < 0:
            raise ValueError("window must be non-negative")
        entry = self.by_user.get(user_id)
        if entry is None:
            return None

        idx = self.ranked.index(self._key(entry))
        total = len(self.ranked)
        user = RankedEntry(rank=idx + 1, user_id=user_id, score=entry.score)

        above_start = max(0, idx - window)
        above = [
            RankedEntry(rank=i + 1, user_id=uid, score=-neg)
            for i, (neg, _s, uid) in enumerate(
                self.ranked[above_start:idx], start=above_start
            )
        ]
        below_end = min(total, idx + 1 + window)
        below = [
            RankedEntry(rank=i + 1, user_id=uid, score=-neg)
            for i, (neg, _s, uid) in enumerate(
                self.ranked[idx + 1 : below_end], start=idx + 1
            )
        ]
        return user, above, below

    def invariant_holds(self) -> bool:
        """Debug/test helper: dict and sorted list agree."""
        if len(self.by_user) != len(self.ranked):
            return False
        for uid, entry in self.by_user.items():
            if self._key(entry) not in self.ranked:
                return False
            if entry.user_id != uid:
                return False
        return True
