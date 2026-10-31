from common.idempotency import record_order
from common.models import Outcome
from localsim.datastore import InMemoryOrderStore


def test_first_write_then_duplicate(orders):
    store = InMemoryOrderStore()
    order = orders[0]
    assert record_order(store, order, {}) == Outcome.FIRST_SUCCESS
    assert record_order(store, order, {}) == Outcome.DUPLICATE_SUCCESS
    assert store.apply_count[order.order_id] == 1
    assert store.conditional_failures == 1


def test_without_idempotency_the_order_is_applied_twice(orders):
    store = InMemoryOrderStore()
    order = orders[0]
    assert record_order(store, order, {}, idempotent=False) == Outcome.FIRST_SUCCESS
    assert record_order(store, order, {}, idempotent=False) == Outcome.UNSAFE_DOUBLE_APPLY
    assert store.apply_count[order.order_id] == 2


def test_different_orders_do_not_clash(orders):
    store = InMemoryOrderStore()
    outcomes = [record_order(store, o, {}) for o in orders]
    assert outcomes == [Outcome.FIRST_SUCCESS] * len(orders)
    assert len(store.items) == len(orders)
