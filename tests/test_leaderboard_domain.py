"""Domain unit tests — ordering, ties, replace semantics, structure sync."""

from __future__ import annotations

from app.domain.leaderboard import Entry, Leaderboard


def _board(*pairs: tuple[str, int]) -> Leaderboard:
    b = Leaderboard()
    for i, (uid, score) in enumerate(pairs, start=1):
        b.submit(uid, score, seq=i)
    return b


class TestOrdering:
    def test_higher_score_ranks_first(self) -> None:
        b = _board(("a", 100), ("b", 300), ("c", 200))
        top = b.top(10, 0)
        assert [(e.user_id, e.score, e.rank) for e in top] == [
            ("b", 300, 1),
            ("c", 200, 2),
            ("a", 100, 3),
        ]

    def test_tie_broken_by_who_reached_first(self) -> None:
        b = Leaderboard()
        b.submit("first", 100, seq=1)
        b.submit("second", 100, seq=2)
        assert [e.user_id for e in b.top(10, 0)] == ["first", "second"]

    def test_tie_then_user_id(self) -> None:
        b = Leaderboard()
        for uid in ("bob", "alice"):
            e = Entry(user_id=uid, score=100, achieved_seq=5)
            b.by_user[uid] = e
            b.ranked.add(b._key(e))
        assert [e.user_id for e in b.top(10, 0)] == ["alice", "bob"]


class TestReplaceSemantics:
    def test_higher_replaces_and_improves(self) -> None:
        b = Leaderboard()
        r1 = b.submit("u", 10, seq=1)
        assert r1.created and r1.improved and r1.replaced
        r2 = b.submit("u", 20, seq=2)
        assert r2.improved and r2.replaced and not r2.created
        assert r2.entry.score == 20 and r2.entry.achieved_seq == 2

    def test_lower_replaces_and_rank_drops(self) -> None:
        b = Leaderboard()
        b.submit("a", 100, seq=1)
        b.submit("b", 50, seq=2)
        r = b.submit("a", 40, seq=3)
        assert r.replaced and not r.improved
        assert r.entry.score == 40
        assert r.rank == 2
        assert [e.user_id for e in b.top(10, 0)] == ["b", "a"]

    def test_equal_score_is_noop(self) -> None:
        b = Leaderboard()
        b.submit("u", 10, seq=1)
        r = b.submit("u", 10, seq=2)
        assert not r.replaced and not r.improved
        assert r.entry.achieved_seq == 1

    def test_equal_resubmit_keeps_tie_order(self) -> None:
        b = Leaderboard()
        b.submit("alice", 100, seq=1)
        b.submit("bob", 100, seq=2)
        assert [e.user_id for e in b.top(10, 0)] == ["alice", "bob"]
        b.submit("alice", 100, seq=3)
        assert [e.user_id for e in b.top(10, 0)] == ["alice", "bob"]


class TestAround:
    def test_middle(self) -> None:
        b = _board(("a", 300), ("b", 200), ("c", 100))
        user, above, below = b.around("b", 1)  # type: ignore[misc]
        assert user.rank == 2
        assert [e.user_id for e in above] == ["a"]
        assert [e.user_id for e in below] == ["c"]

    def test_rank_one_empty_above(self) -> None:
        b = _board(("a", 300), ("b", 200), ("c", 100))
        user, above, below = b.around("a", 1)  # type: ignore[misc]
        assert user.rank == 1 and above == []
        assert [e.user_id for e in below] == ["b"]

    def test_last_empty_below(self) -> None:
        b = _board(("a", 300), ("b", 200), ("c", 100))
        _, above, below = b.around("c", 1)  # type: ignore[misc]
        assert below == []
        assert [e.user_id for e in above] == ["b"]

    def test_single_user(self) -> None:
        b = _board(("solo", 42))
        user, above, below = b.around("solo", 1)  # type: ignore[misc]
        assert user.rank == 1 and above == [] and below == []

    def test_unknown_user(self) -> None:
        assert _board(("a", 1)).around("ghost", 1) is None


class TestTopOffset:
    def test_offset_past_end_empty(self) -> None:
        assert _board(("a", 1), ("b", 2)).top(10, offset=100) == []

    def test_limit_and_offset(self) -> None:
        page = _board(("a", 10), ("b", 30), ("c", 20)).top(1, offset=1)
        assert page[0].user_id == "c" and page[0].rank == 2


class TestInvariant:
    def test_dict_and_sorted_stay_in_sync(self) -> None:
        b = Leaderboard()
        for i in range(20):
            b.submit(f"u{i % 7}", score=i * 3, seq=i + 1)
        assert b.invariant_holds()
        assert len(b.by_user) == 7
