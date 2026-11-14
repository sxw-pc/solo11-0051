"""Regenerate the deterministic demo CSV files used by the example pipeline."""

from __future__ import annotations

import csv
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"

def write(name: str, columns: list[str], rows: list[dict[str, str]]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / name).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

def members() -> None:
    channels = ["web"] * 5 + ["store"] * 4 + ["partner"] * 2 + ["kiosk"]
    rows = []
    for index in range(72):
        rows.append(
            {
                "member_id": f"demo-m-{index + 1:04d}",
                "region": ["north", "south", "east", "west"][index % 4],
                "tier": ["gold", "silver", "bronze"][index % 3],
                "status": "disabled" if index % 24 == 0 else ("pending" if index % 11 == 0 else "active"),
                "channel": channels[index % 12],
                "owner": f"demo-org-{(index % 5) + 1:02d}",
                "score": str(40 + (index * 17) % 61),
                "joined_at": f"2026-{(index % 9) + 1:02d}-{(index % 28) + 1:02d}",
            }
        )
    write("demo_members.csv", list(rows[0]), rows)

def orders() -> None:
    tiers = {
        "north": ["gold"] * 10 + ["silver"] * 7 + ["bronze"] * 5 + ["platinum"] * 2,
        "south": ["gold"] * 8 + ["silver"] * 8 + ["bronze"] * 6 + ["platinum"] * 2,
        "east": ["gold"] * 9 + ["silver"] * 7 + ["bronze"] * 6 + ["platinum"] * 2,
        "west": ["gold"] * 7 + ["silver"] * 8 + ["bronze"] * 8 + ["platinum"] * 1,
    }
    rows = []
    for region in ("north", "south", "east", "west"):
        for tier in tiers[region]:
            index = len(rows)
            status = (
                "cancelled"
                if index % 19 == 0
                else ("refunded" if index % 23 == 0 else ("pending" if index % 31 == 0 else "paid"))
            )
            rows.append(
                {
                    "order_id": f"demo-o-{index + 1:04d}",
                    "region": region,
                    "tier": tier,
                    "status": status,
                    "channel": ["web", "store", "partner"][index % 3],
                    "owner": f"demo-org-{(index % 7) + 1:02d}",
                    "amount": f"{100 + (index * 37) % 900}.00",
                    "created_at": f"2026-03-{(index % 28) + 1:02d}",
                }
            )
    write("demo_orders.csv", list(rows[0]), rows)

def events() -> None:
    rows = []
    for index in range(90):
        category = (
            "archive"
            if index in {0, 58, 86}
            else ("text" if index % 5 == 0 else ("audio" if index % 2 == 0 else "video"))
        )
        rows.append(
            {
                "event_id": f"demo-e-{index + 1:04d}",
                "region": ["north", "south", "east", "west"][index % 4],
                "category": category,
                "status": "invalid" if index % 17 == 0 else ("retry" if index % 13 == 0 else "valid"),
                "channel": ["web", "store", "partner", "kiosk"][index % 4],
                "resolver": f"demo-org-{(index % 6) + 1:02d}",
                "duration_ms": str(120 + (index * 53) % 1800),
                "created_at": f"2026-05-{(index % 28) + 1:02d}",
            }
        )
    write("demo_events.csv", list(rows[0]), rows)

if __name__ == "__main__":
    members()
    orders()
    events()
    print(f"wrote synthetic demo data to {DATA}")
