"""
Tăng tải theo bậc để tìm ngưỡng chịu tải (dùng cùng các luồng trong locustfile.py).

    bậc 1: STEP_USERS user        giữ STEP_DURATION giây
    bậc 2: 2 x STEP_USERS user    giữ STEP_DURATION giây
    ...  cho tới MAX_USERS, rồi dừng.

Mỗi bậc được giữ đủ lâu để Prometheus (scrape 30s) và HPA (15s, scale down sau 5 phút) phản
ứng. Thời điểm bắt đầu mỗi bậc được in ra (và ghi vào step_load_timeline.csv) để đối chiếu
với biểu đồ CPU/RAM trên Grafana: bậc nào tỉ lệ lỗi hoặc p95 bắt đầu tăng thì đó là ngưỡng.

Chạy:  locust -f step_load.py --host http://localhost --headless --csv results/run1 --html results/run1.html
"""

import csv
import os
import time
from datetime import datetime
from pathlib import Path

from locust import LoadTestShape, events

from locustfile import EmployeeReader, TaskUpdater  # noqa: F401  (2 luồng dùng cho test)

STEP_USERS = int(os.getenv("STEP_USERS", "20"))
STEP_DURATION = int(os.getenv("STEP_DURATION", "180"))   # giây mỗi bậc
MAX_USERS = int(os.getenv("MAX_USERS", "200"))
SPAWN_RATE = float(os.getenv("SPAWN_RATE", "5"))           # user mới mỗi giây
TIMELINE_FILE = Path(os.getenv("TIMELINE_FILE", Path(__file__).parent / "step_load_timeline.csv"))


class StepLoadShape(LoadTestShape):
    def __init__(self):
        super().__init__()
        self._current_step = 0

    def tick(self):
        run_time = self.get_run_time()
        step = int(run_time // STEP_DURATION) + 1
        users = step * STEP_USERS
        if users > MAX_USERS:
            return None  # kết thúc test
        if step != self._current_step:
            self._current_step = step
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[step {step}] {now} -> {users} users")
            new_file = not TIMELINE_FILE.exists()
            with TIMELINE_FILE.open("a", newline="") as f:
                w = csv.writer(f)
                if new_file:
                    w.writerow(["step", "start_time", "users"])
                w.writerow([step, now, users])
        return users, SPAWN_RATE


@events.test_start.add_listener
def _show_plan(**_):
    steps = MAX_USERS // STEP_USERS
    print(f"[step load] {steps} steps x {STEP_DURATION}s: {STEP_USERS} -> {MAX_USERS} users "
          f"(~{steps * STEP_DURATION // 60} min), spawn rate {SPAWN_RATE}/s")
