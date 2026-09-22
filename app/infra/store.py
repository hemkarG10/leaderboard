"""Process-local store: game_id → Leaderboard + monotonic sequence.

All mutations are synchronous with no await. On a single asyncio event loop
(one uvicorn worker) each read-modify-write runs as a unit. If this code
ever gains an await or a thread-pool handoff, add a per-game asyncio.Lock
and never hold it across network I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.leaderboard import Leaderboard, RankedEntry, SubmitResult
from app.errors import CapacityExceeded, GameNotFound, UserNotFound


@dataclass(frozen=True, slots=True)
class AroundResult:
    user: RankedEntry
    above: list[RankedEntry]
    below: list[RankedEntry]
    total: int


class InMemoryStore:
    def __init__(self, *, max_games: int, max_users_per_game: int) -> None:
        self._boards: dict[str, Leaderboard] = {}
        self._seq = 0
        self._max_games = max_games
        self._max_users_per_game = max_users_per_game

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def total_entries(self) -> int:
        return sum(len(b) for b in self._boards.values())

    def submit(self, game_id: str, user_id: str, score: int) -> SubmitResult:
        board = self._boards.get(game_id)
        if board is None:
            if len(self._boards) >= self._max_games:
                raise CapacityExceeded(
                    f"max_games ({self._max_games}) reached",
                    details={"resource": "games", "limit": self._max_games},
                )
            board = Leaderboard()
            self._boards[game_id] = board

        if user_id not in board.by_user and len(board) >= self._max_users_per_game:
            raise CapacityExceeded(
                f"max_users_per_game ({self._max_users_per_game}) reached",
                details={
                    "resource": "users_per_game",
                    "limit": self._max_users_per_game,
                    "game_id": game_id,
                },
            )

        return board.submit(user_id, score, self._next_seq())

    def top(
        self, game_id: str, limit: int, offset: int
    ) -> tuple[int, list[RankedEntry]]:
        # Unknown / empty game → success with an empty board (not 404).
        board = self._boards.get(game_id)
        if board is None:
            return 0, []
        return len(board), board.top(limit, offset)

    def around(self, game_id: str, user_id: str, window: int) -> AroundResult:
        board = self._require_game(game_id)
        result = board.around(user_id, window)
        if result is None:
            raise UserNotFound(
                f"user '{user_id}' not found in game '{game_id}'",
                details={"game_id": game_id, "user_id": user_id},
            )
        user, above, below = result
        return AroundResult(user=user, above=above, below=below, total=len(board))

    def _require_game(self, game_id: str) -> Leaderboard:
        board = self._boards.get(game_id)
        if board is None:
            raise GameNotFound(
                f"game '{game_id}' not found",
                details={"game_id": game_id},
            )
        return board
