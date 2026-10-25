"""Synthetic order generator. No real people, no real products.

    python -m src.producer.generate_orders --count 5 --seed 1
"""
from __future__ import annotations

import argparse
import random

from common.models import Order


def make_orders(
    n: int,
    run_id: str,
    seed: int,
    poison_rate: float = 0.0,
    created_at: float = 0.0,
) -> list[Order]:
    """Deterministic for a given (run_id, seed) so a run can be replayed."""
    rng = random.Random(f"{run_id}:{seed}")
    prefix = (run_id or "run")[:8]
    orders = []
    for i in range(n):
        items = rng.randint(1, 5)
        orders.append(
            Order(
                order_id=f"{prefix}-{i:06d}-{rng.getrandbits(32):08x}",
                customer_ref=f"CUST-{rng.randint(1, 5000):05d}",
                items=items,
                amount_cents=items * rng.randint(199, 4999),
                created_at=created_at,
                run_id=run_id,
                poison=rng.random() < poison_rate,
            )
        )
    return orders


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--run-id", default="demo")
    parser.add_argument("--poison-rate", type=float, default=0.0)
    args = parser.parse_args()
    for order in make_orders(args.count, args.run_id, args.seed, args.poison_rate):
        print(order.to_json())


if __name__ == "__main__":
    main()
