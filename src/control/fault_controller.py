"""Turn faults on and off while a run is going.

Live mode: the deployed functions read the SSM parameter named in
FAULT_PARAM_NAME (cached ~5 s), so writing a new JSON value there flips the
fault without a redeploy. The runner writes the whole schedule once at the
start of a run (mode, rate and the absolute on/off times) and clears it at the
end, so it does not have to be awake at exactly the right second.

Dry-run mode: the simulator gets the FaultConfig directly, so the
LocalFaultController here is mostly for demos and tests.

    python -m src.control.fault_controller --param /sqs-rr/dev/fault --mode unhandled_error --rate 1
    python -m src.control.fault_controller --param /sqs-rr/dev/fault --off
"""
from __future__ import annotations

import argparse
import json
import time

from common.faults import FAULT_MODES, FaultConfig


class LocalFaultController:
    def __init__(self) -> None:
        self.current = FaultConfig()
        self.history: list[tuple[float, str]] = []

    def enable(self, cfg: FaultConfig) -> None:
        self.current = cfg
        self.history.append((time.time(), cfg.to_json()))

    def disable(self) -> None:
        self.enable(FaultConfig())


class SsmFaultController:
    def __init__(self, param_name: str, ssm_client=None) -> None:
        if ssm_client is None:
            import boto3

            ssm_client = boto3.client("ssm")
        self.param_name = param_name
        self.ssm = ssm_client

    def enable(self, cfg: FaultConfig) -> None:
        self.ssm.put_parameter(Name=self.param_name, Value=cfg.to_json(), Type="String", Overwrite=True)

    def disable(self) -> None:
        self.enable(FaultConfig())

    def current(self) -> FaultConfig:
        raw = self.ssm.get_parameter(Name=self.param_name)["Parameter"]["Value"]
        return FaultConfig.from_json(raw)


def schedule_for_run(mode: str, rate: float, run_start: float, on_s: float, window_s: float, point: str = "auto") -> FaultConfig:
    """Absolute-time schedule for one live run."""
    if mode == "none":
        return FaultConfig()
    return FaultConfig(
        mode=mode,
        rate=rate,
        window_start=run_start + on_s,
        window_end=run_start + on_s + window_s,
        point=point,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="flip the fault switch of the deployed stack")
    parser.add_argument("--param", required=True, help="SSM parameter name, e.g. /sqs-rr/dev/fault")
    parser.add_argument("--mode", choices=FAULT_MODES, default="none")
    parser.add_argument("--rate", type=float, default=1.0)
    parser.add_argument("--window", type=float, default=0.0, help="seconds from now, 0 = until turned off")
    parser.add_argument("--off", action="store_true")
    args = parser.parse_args()

    ctl = SsmFaultController(args.param)
    if args.off or args.mode == "none":
        ctl.disable()
        print("fault disabled")
        return
    now = time.time()
    cfg = FaultConfig(
        mode=args.mode,
        rate=args.rate,
        window_start=now,
        window_end=now + args.window if args.window > 0 else None,
    )
    ctl.enable(cfg)
    print("fault enabled:", json.dumps(json.loads(cfg.to_json()), indent=2))


if __name__ == "__main__":
    main()
