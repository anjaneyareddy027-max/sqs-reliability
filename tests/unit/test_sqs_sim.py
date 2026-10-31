import random

import pytest

from localsim.sqs import SimQueue


def make(vt=30, mrc=None, dup=0.0):
    dlq = SimQueue("dlq", 30) if mrc else None
    q = SimQueue("q", vt, dlq=dlq, max_receive_count=mrc, rng=random.Random(1), dup_prob=dup)
    return q, dlq


def test_received_message_is_hidden_until_the_visibility_timeout_ends():
    q, _ = make(vt=30)
    q.send("a", now=0)
    [first] = q.receive(10, now=1)
    assert q.receive(10, now=30.9) == []
    [again] = q.receive(10, now=31)
    assert again["attributes"]["ApproximateReceiveCount"] == "2"
    assert again["receiptHandle"] != first["receiptHandle"]


def test_delete_with_the_latest_handle():
    q, _ = make()
    q.send("a", 0)
    [m] = q.receive(1, 0)
    assert q.delete(m["receiptHandle"]) is True
    assert len(q) == 0
    assert q.receive(10, 100) == []


def test_old_receipt_handle_is_ignored():
    q, _ = make(vt=10)
    q.send("a", 0)
    [first] = q.receive(1, 0)
    [second] = q.receive(1, 10)
    assert q.delete(first["receiptHandle"]) is False
    assert len(q) == 1
    assert q.delete(second["receiptHandle"]) is True


def test_redrive_after_max_receive_count():
    q, dlq = make(vt=10, mrc=2)
    q.send("a", 0)
    assert len(q.receive(1, 0)) == 1
    assert len(q.receive(1, 10)) == 1
    assert q.receive(1, 20) == []  # third attempt moves it instead
    assert len(q) == 0
    assert len(dlq) == 1
    assert q.counters["moved_to_dlq"] == 1
    assert dlq.bodies() == ["a"]


def test_dlq_keeps_the_message_id():
    q, dlq = make(vt=1, mrc=1)
    mid = q.send("x", 0)
    q.receive(1, 0)
    q.receive(1, 5)
    [m] = dlq.receive(1, 5)
    assert m["messageId"] == mid


def test_delivery_delay():
    q, _ = make()
    q.send("a", 0, delay=5)
    assert q.receive(1, 4.9) == []
    assert q.depth(4.9) == {"visible": 0, "inflight": 0, "delayed": 1}
    assert len(q.receive(1, 5)) == 1


def test_depth_counts_visible_and_in_flight():
    q, _ = make(vt=30)
    for i in range(5):
        q.send(str(i), 0)
    q.receive(2, 1)
    assert q.depth(1) == {"visible": 3, "inflight": 2, "delayed": 0}


def test_receive_respects_batch_limit():
    q, _ = make()
    for i in range(25):
        q.send(str(i), 0)
    assert len(q.receive(10, 0)) == 10


def test_duplicate_copy_arrives_even_after_delete():
    q, _ = make(dup=1.0)
    mid = q.send("a", 0)
    [m] = q.receive(1, 0)
    q.delete(m["receiptHandle"])
    later = q.receive(1, 10)
    assert len(later) == 1
    assert later[0]["messageId"] == mid
    q.delete(later[0]["receiptHandle"])
    assert q.receive(1, 20) == []  # copies do not make more copies


def test_next_visible_at():
    q, _ = make(vt=30)
    assert q.next_visible_at() is None
    q.send("a", 0)
    q.receive(1, 2)
    assert q.next_visible_at() == pytest.approx(32)


def test_dlq_requires_a_max_receive_count():
    with pytest.raises(ValueError):
        SimQueue("q", 30, dlq=SimQueue("d", 30))
