"""CLI.

  python -m controller run      --sim http://127.0.0.1:8700 [--db state/x.db]
  python -m controller status   [--db ...]                      loop health + open alerts
  python -m controller view     --workflow ks_cancel [--db ...]  supervisor view
  python -m controller why      --workflow ks_cancel --at "D3 14:00"
  python -m controller customer --lead ks_cancel:l00012345
  python -m controller decisions --workflow ks_cancel [--kind experiment_promote]
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
import sys


def _db(a) -> str:
    if a.db:
        return a.db
    dbs = sorted(glob.glob("state/*.db"), key=os.path.getmtime)
    if not dbs:
        sys.exit("no store found under state/; pass --db")
    return dbs[-1]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="controller")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--sim", required=True)
    r.add_argument("--db")
    for name in ("status", "view", "why", "customer", "decisions"):
        p = sub.add_parser(name)
        p.add_argument("--db")
        if name in ("view", "why", "decisions"):
            p.add_argument("--workflow", required=True)
        if name == "why":
            p.add_argument("--at", required=True, help='"D3 14:00" or an ISO timestamp')
        if name == "customer":
            p.add_argument("--lead", required=True)
        if name == "decisions":
            p.add_argument("--kind")
            p.add_argument("--limit", type=int, default=200)
    a = ap.parse_args(argv)
    logging.basicConfig(level=os.environ.get("CTRL_LOG", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if a.cmd == "run":
        from .loop import Controller
        Controller(a.sim, a.db).run()
        return
    from . import report
    from .store import Store
    store = Store(_db(a))
    if a.cmd == "status":
        print(report.status(store))
    elif a.cmd == "view":
        print(report.supervisor_view(store, a.workflow))
    elif a.cmd == "why":
        print(report.why(store, a.workflow, report.parse_when(a.at)))
    elif a.cmd == "customer":
        print(report.customer(store, a.lead))
    elif a.cmd == "decisions":
        q = "SELECT minute, kind, summary, proposal_id, approval_id, version FROM decisions WHERE wf=?"
        args = [a.workflow]
        if a.kind:
            q += " AND kind=?"
            args.append(a.kind)
        for m, k, s, pid, aid, v in store.q(q + " ORDER BY id LIMIT ?", (*args, a.limit)):
            extra = " ".join(x for x in (pid and f"proposal={pid}", aid and f"approval={aid}", v and f"v{v}") if x)
            print(f"{report.fmt_min(m)}  [{k}] {s}  {extra}")


if __name__ == "__main__":
    main()
