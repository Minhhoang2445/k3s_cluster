"""
Stress test PMS: nhiều nhân viên dùng hệ thống cùng lúc, chia làm 2 luồng.

  EmployeeReader (luồng ĐỌC)  - nhân viên mở trang, xem project của mình, danh sách task,
                                chi tiết project, task được giao, thông tin cá nhân.
  TaskUpdater    (luồng GHI)  - nhân viên cập nhật trạng thái task được giao cho mình
                                (todo -> in_progress -> review -> done -> todo ...).

Tỉ lệ mặc định 4 người đọc : 1 người ghi (đổi bằng READ_WEIGHT / WRITE_WEIGHT).

Tài khoản: accounts.csv (tạo bởi 01_create_accounts.py), dữ liệu project/task do
02_seed_projects_tasks.py tạo. Mỗi user ảo dùng một tài khoản theo vòng tròn; nhiều user ảo
có thể dùng chung một tài khoản.

Token: access token sống 15 phút, signin dùng bcrypt (tốn CPU) và ghi 1 dòng RefreshToken.
Token được cache theo tài khoản: mỗi tài khoản chỉ đăng nhập lại khi token sắp hết hạn hoặc
bị 401, để tải đo được là tải của 2 luồng chứ không phải tải đăng nhập.

Cách chạy: xem README.md trong thư mục này.
"""

import csv
import itertools
import os
import random
import time
from pathlib import Path

import gevent
import gevent.lock
from locust import HttpUser, between, events, task

HERE = Path(__file__).parent

# ----------------------------------------------------------------- cấu hình (biến môi trường)
ACCOUNTS_FILE = Path(os.getenv("ACCOUNTS_FILE", HERE / "accounts.csv"))
READ_WEIGHT = int(os.getenv("READ_WEIGHT", "4"))
WRITE_WEIGHT = int(os.getenv("WRITE_WEIGHT", "1"))
WAIT_MIN = float(os.getenv("WAIT_MIN", "1"))      # giây nghỉ giữa 2 thao tác của 1 user
WAIT_MAX = float(os.getenv("WAIT_MAX", "3"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "10"))  # quá thời gian này = lỗi
# signin chạy bcrypt nên chậm hơn các API khác; lỗi đăng nhập -> nghỉ rồi mới thử lại
LOGIN_TIMEOUT = float(os.getenv("LOGIN_TIMEOUT", "30"))
LOGIN_RETRY_DELAY = float(os.getenv("LOGIN_RETRY_DELAY", "5"))
# Làm mới token trước khi hết hạn 15 phút
TOKEN_MAX_AGE = float(os.getenv("TOKEN_MAX_AGE", str(13 * 60)))

# Vòng trạng thái của luồng ghi (bỏ "cancelled" để task luôn tiếp tục được cập nhật)
STATUS_CYCLE = ["todo", "in_progress", "review", "done"]


def load_accounts():
    if not ACCOUNTS_FILE.exists():
        raise SystemExit(f"Không tìm thấy {ACCOUNTS_FILE}. Chạy 01_create_accounts.py trước.")
    with ACCOUNTS_FILE.open(newline="", encoding="utf-8") as f:
        rows = [(r["email"], r["password"]) for r in csv.DictReader(f)]
    if not rows:
        raise SystemExit(f"{ACCOUNTS_FILE} không có tài khoản nào.")
    return rows


ACCOUNTS = load_accounts()
_account_cycle = itertools.cycle(ACCOUNTS)
_account_lock = gevent.lock.Semaphore()


def next_account():
    with _account_lock:
        return next(_account_cycle)


class TokenCache:
    """Token dùng chung giữa các user ảo cùng tài khoản; mỗi tài khoản có 1 khoá riêng
    để chỉ một user ảo đăng nhập, các user khác chờ và dùng lại token."""

    def __init__(self):
        self._tokens = {}   # email -> (token, user_id, issued_at)
        self._locks = {}
        self._guard = gevent.lock.Semaphore()

    def _lock_for(self, email):
        with self._guard:
            return self._locks.setdefault(email, gevent.lock.Semaphore())

    def get(self, client, email, password, force=False):
        with self._lock_for(email):
            cached = self._tokens.get(email)
            if cached and not force and time.time() - cached[2] < TOKEN_MAX_AGE:
                return cached
            with client.post(
                "/api/auth/signin",
                json={"email": email, "password": password},
                name="POST /api/auth/signin",
                timeout=LOGIN_TIMEOUT,
                catch_response=True,
            ) as resp:
                if resp.status_code != 200:
                    resp.failure(f"signin {resp.status_code}")
                    return None
                body = resp.json()
                entry = (body["accessToken"], body["user"]["id"], time.time())
                self._tokens[email] = entry
                return entry


TOKENS = TokenCache()


class PmsEmployee(HttpUser):
    """Phần chung: đăng nhập, gọi API có token, tự đăng nhập lại khi bị 401."""

    abstract = True
    wait_time = between(WAIT_MIN, WAIT_MAX)

    def on_start(self):
        self.email, self.password = next_account()
        self.token = None
        self.user_id = None
        self.my_projects = []     # [{id, name}]
        self.my_tasks = []        # [{id, status, project: {id}}] được giao cho user này
        self.login()
        self.refresh_my_data()

    def login(self, force=False):
        entry = TOKENS.get(self.client, self.email, self.password, force=force)
        if entry:
            self.token, self.user_id, _ = entry
            return True
        # Không đăng nhập lại dồn dập khi hệ thống đang quá tải
        gevent.sleep(LOGIN_RETRY_DELAY)
        return False

    def api(self, method, path, name, expected=(200,), **kwargs):
        """Gọi API có Bearer token. 401 -> đăng nhập lại 1 lần rồi thử lại.
        Trả về JSON (hoặc None nếu lỗi). Mọi mã khác `expected` hoặc timeout đều tính là lỗi."""
        if not self.token and not self.login():
            return None
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self.token}"}
            with self.client.request(
                method, path, name=name, headers=headers, timeout=REQUEST_TIMEOUT,
                catch_response=True, **kwargs,
            ) as resp:
                if resp.status_code == 401 and attempt == 1:
                    # token hết hạn: không tính là lỗi của hệ thống, đăng nhập lại
                    resp.success()
                    if not self.login(force=True):
                        return None
                    continue
                if resp.status_code not in expected:
                    resp.failure(f"HTTP {resp.status_code}")
                    return None
                try:
                    return resp.json()
                except ValueError:
                    resp.failure("response is not JSON")
                    return None
        return None

    def refresh_my_data(self):
        projects = self.api("GET", "/api/project/user/me/projects", "GET /api/project/user/me/projects")
        if projects is not None:
            self.my_projects = projects
        tasks = self.api("GET", "/api/project/user/me/tasks", "GET /api/project/user/me/tasks")
        if tasks is not None:
            self.my_tasks = tasks


class EmployeeReader(PmsEmployee):
    """Luồng ĐỌC: nhân viên vào xem project và task."""

    weight = READ_WEIGHT

    @task(1)
    def open_app(self):
        # Trang frontend (nginx): tải lên các pod frontend
        self.client.get("/", name="GET / (frontend)", timeout=REQUEST_TIMEOUT)

    @task(3)
    def list_my_projects(self):
        self.refresh_my_data()

    @task(4)
    def view_project_detail(self):
        if not self.my_projects:
            return
        pid = random.choice(self.my_projects)["id"]
        self.api("GET", f"/api/project/{pid}", "GET /api/project/[id]")

    @task(4)
    def view_project_tasks(self):
        if not self.my_projects:
            return
        pid = random.choice(self.my_projects)["id"]
        self.api("GET", f"/api/project/{pid}/tasks", "GET /api/project/[id]/tasks")

    @task(2)
    def view_my_task(self):
        if not self.my_tasks:
            return
        t = random.choice(self.my_tasks)
        self.api("GET", f"/api/project/{t['project']['id']}/tasks/{t['id']}",
                 "GET /api/project/[id]/tasks/[taskId]")

    @task(1)
    def view_profile(self):
        self.api("GET", "/api/user/me", "GET /api/user/me")


class TaskUpdater(PmsEmployee):
    """Luồng GHI: nhân viên cập nhật trạng thái task của mình."""

    weight = WRITE_WEIGHT

    @task(5)
    def update_task_status(self):
        if not self.my_tasks:
            self.refresh_my_data()
            return
        t = random.choice(self.my_tasks)
        current = t.get("status", "todo")
        nxt = STATUS_CYCLE[(STATUS_CYCLE.index(current) + 1) % len(STATUS_CYCLE)] \
            if current in STATUS_CYCLE else "todo"
        result = self.api(
            "PATCH", f"/api/project/{t['project']['id']}/tasks/{t['id']}",
            "PATCH /api/project/[id]/tasks/[taskId] (status)",
            json={"status": nxt},
        )
        if result is not None:
            t["status"] = nxt

    @task(1)
    def reload_my_tasks(self):
        # Sau khi cập nhật, nhân viên thường xem lại danh sách task của mình
        tasks = self.api("GET", "/api/project/user/me/tasks", "GET /api/project/user/me/tasks")
        if tasks is not None:
            self.my_tasks = tasks


@events.test_start.add_listener
def _show_config(environment, **_):
    print(
        f"[PMS stress test] accounts={len(ACCOUNTS)} read:write={READ_WEIGHT}:{WRITE_WEIGHT} "
        f"wait={WAIT_MIN}-{WAIT_MAX}s timeout={REQUEST_TIMEOUT}s login-timeout={LOGIN_TIMEOUT}s "
        f"host={environment.host}"
    )
