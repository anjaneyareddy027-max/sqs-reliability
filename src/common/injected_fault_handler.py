# Implemented to resolve the gap of real-world downstream failures in SQS processing
# Addresses Kyrychenko et al. (2025b) steady-load constraint
import random
import time

class SQSDownstreamFaultHandler:
    def __init__(self, throttle_rate=0.2, crash_probability=0.05, timeout_s=3):
        self.throttle_rate = throttle_rate
        self.crash_probability = crash_probability
        self.timeout_s = timeout_s

    def process_with_fault_injection(self, batch):
        responses = []
        for message in batch:
            if random.random() < self.crash_probability:
                raise Exception("CRASH_LOOP: Simulated unhandled downstream crash.")
            if random.random() < self.throttle_rate:
                time.sleep(self.timeout_s)
            responses.append({"id": message.get("id"), "status": "processed"})
        return responses
