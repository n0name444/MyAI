"""Сводка по журналу маршрутов: где роутер и инструменты чаще всего отказывают.

Запуск:  python route_report.py            (журнал logs/routes.jsonl)
         python route_report.py --last 200 (только последние 200 записей)
         python route_report.py --path другой.jsonl
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import List, Optional, Sequence

from route_journal import RouteJournal

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PATH = BASE_DIR / "logs" / "routes.jsonl"


def _p95(values: Sequence[int]) -> int:
    if not values:
        return 0
    s = sorted(values)
    return s[max(0, math.ceil(0.95 * len(s)) - 1)]


def summarize(entries: List[dict], top: int = 8) -> str:
    if not entries:
        return "Журнал пуст: записей о маршрутах пока нет."
    by_route = defaultdict(list)
    for e in entries:
        by_route[e.get("route", "?")].append(e)

    lines = [f"Записей: {len(entries)} ({entries[0].get('ts', '?')} … {entries[-1].get('ts', '?')})", ""]
    lines.append(f"{'Маршрут':<12}{'всего':>6}{'ок':>6}{'отказ':>7}{'ошибки':>8}{'стоп':>6}{'ср., мс':>9}{'p95, мс':>9}")
    for route in sorted(by_route, key=lambda r: -len(by_route[r])):
        rows = by_route[route]
        c = Counter(r.get("outcome", "ok") for r in rows)
        ms = [int(r.get("ms") or 0) for r in rows]
        lines.append(f"{route:<12}{len(rows):>6}{c['ok']:>6}{c['empty']:>7}{c['error']:>8}{c['stopped']:>6}"
                     f"{sum(ms) // len(ms):>9}{_p95(ms):>9}")

    bad = [e for e in entries if e.get("outcome") in ("empty", "error")]
    lines += ["", f"Отказов и ошибок: {len(bad)} из {len(entries)} ({100 * len(bad) / len(entries):.0f}%)"]
    if bad:
        lines.append("Последние:")
        for e in bad[-top:]:
            label = "ошибка" if e.get("outcome") == "error" else "отказ"
            extra = f" [{e['error']}]" if e.get("error") else ""
            lines.append(f"  {str(e.get('ts', ''))[:16]} [{e.get('route')}] {label}{extra}  «{e.get('text', '')}»")

    reasons = Counter((e.get("route"), e.get("reason") or "-") for e in entries)
    lines += ["", f"Частые причины выбора маршрута (топ {top}):"]
    for (route, reason), n in reasons.most_common(top):
        lines.append(f"  {n:>4}  {route:<11} {reason[:60]}")

    anime = by_route.get("ANIME", [])
    if anime:
        lines += ["", f"Аниме: {len(anime)} запросов, из них уточнений: {sum(1 for e in anime if e.get('followup'))}"]
    slow = sorted(entries, key=lambda e: -int(e.get("ms") or 0))[:5]
    lines += ["", "Самые долгие:"]
    for e in slow:
        lines.append(f"  {int(e.get('ms') or 0):>7} мс  [{e.get('route')}] «{e.get('text', '')}»")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Сводка по журналу маршрутов MyAI")
    ap.add_argument("--path", default=str(DEFAULT_PATH), help="путь к routes.jsonl")
    ap.add_argument("--last", type=int, default=0, help="учесть только последние N записей")
    args = ap.parse_args(argv)
    path = Path(args.path)
    entries = RouteJournal.read_entries(path)
    if args.last > 0:
        entries = entries[-args.last:]
    if not entries and not path.exists():
        print(f"Журнал не найден: {path}\nОн появляется после первых сообщений в MyAI (отключается MYAI_ROUTE_LOG=0).")
        return 0
    print(summarize(entries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
