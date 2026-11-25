"""

Cách chạy:
    source ../locust-venv/bin/activate
    python 01_create_accounts.py --host http://<ip-gateway>:8000
    python 01_create_accounts.py --host http://<ip-gateway>:8000 --count 50 --password 'Test@123'
"""

import argparse
import csv
import random
import sys
from pathlib import Path

import requests

FIRST_NAMES = ["An", "Binh", "Chi", "Dung", "Giang", "Hoa", "Khanh", "Linh", "Minh", "Nam",
               "Phuong", "Quan", "Son", "Thao", "Trang", "Tuan", "Kha", "Viet", "Yen", "Long"]
LAST_NAMES = ["Nguyen", "Tran", "Le", "Pham", "Hoang", "Vu", "Vo", "Dang", "Bui", "Do"]
DEPARTMENTS = {
    "Engineering": ["Backend Developer", "Frontend Developer", "DevOps Engineer", "QA Engineer"],
    "Product": ["Product Manager", "Business Analyst"],
    "Design": ["UI Designer", "UX Researcher"],
    "Marketing": ["Content Writer", "Marketing Executive"],
}

OUTPUT_FILE = Path(__file__).parent / "accounts.csv"


def build_account(i: int, password: str, domain: str) -> dict:
    rnd = random.Random(i)  # cố định theo index -> chạy lại sinh cùng dữ liệu
    department = rnd.choice(list(DEPARTMENTS))
    return {
        "firstName": rnd.choice(FIRST_NAMES),
        "lastName": rnd.choice(LAST_NAMES),
        "email": f"loadtest{i:03d}@{domain}",
        "password": password,
        "designation": rnd.choice(DEPARTMENTS[department]),
        "department": department,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Tạo tài khoản test cho stress test")
    parser.add_argument("--host", required=True, help="VD: http://192.168.1.10:8000 (không kèm /api)")
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--password", default="Test@123")
    parser.add_argument("--domain", default="loadtest.local", help="domain của email test")
    args = parser.parse_args()

    url = f"{args.host.rstrip('/')}/api/auth/signup"
    session = requests.Session()
    created = existed = failed = 0
    ok_accounts = []

    for i in range(1, args.count + 1):
        acc = build_account(i, args.password, args.domain)
        try:
            r = session.post(url, json=acc, timeout=15)
        except requests.RequestException as e:
            print(f"[ERR ] {acc['email']}: {e}")
            failed += 1
            continue

        if r.status_code == 201:
            created += 1
            print(f"[NEW ] {acc['email']}")
        elif r.status_code == 409:
            existed += 1
            print(f"[SKIP] {acc['email']} đã tồn tại")
        else:
            failed += 1
            print(f"[FAIL] {acc['email']}: {r.status_code} {r.text[:200]}")
            continue
        ok_accounts.append(acc)

    with OUTPUT_FILE.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["email", "password", "firstName", "lastName",
                                               "designation", "department"])
        writer.writeheader()
        writer.writerows(ok_accounts)

    print(f"\nTạo mới: {created} | Đã có: {existed} | Lỗi: {failed}")
    print(f"Đã ghi {len(ok_accounts)} tài khoản vào {OUTPUT_FILE}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
