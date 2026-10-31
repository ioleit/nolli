"""A made-up repository with one reviewed change, for tests of the review view.

"ledger" is a small invoicing app: a FastAPI backend in Python (routes, services, storage),
a TypeScript web client that calls it, and a compose file. The branch ``cache-and-alerts``
puts a cache in front of the invoice store, adds overdue alerts (which reach into the
database directly), moves the web client to a new summary endpoint and deletes a legacy
export module. ``verdicts.json`` judges that change the way a review agent would: one
problem, a few questions, some fine.

    python tests/fixtures/review_repo.py <out_dir>

builds the repository in <out_dir>/ledger and its dataset in <out_dir>/data/ledger-review,
and prints the dataset's path.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

DATASET = "ledger-review"

BASE = {
    "ledger/__init__.py": "",
    "ledger/config.py": "import os\n\nDB_URL = os.environ.get('LEDGER_DB', 'sqlite:///ledger.db')\n",
    "ledger/storage/__init__.py": "",
    "ledger/storage/db.py": (
        "from ledger.config import DB_URL\n\n\nclass Database:\n    def __init__(self, url=DB_URL):\n"
        "        self.url = url\n\n    def query(self, sql, *args):\n        return []\n"),
    "ledger/storage/invoices.py": (
        "from ledger.storage.db import Database\n\n\nclass InvoiceStore:\n    def __init__(self, db: Database):\n"
        "        self.db = db\n\n    def get(self, invoice_id):\n        return self.db.query('select * from invoices where id = ?', invoice_id)\n\n"
        "    def open_for(self, customer_id):\n        return self.db.query('select * from invoices where customer = ?', customer_id)\n"),
    "ledger/storage/customers.py": (
        "from ledger.storage.db import Database\n\n\nclass CustomerStore:\n    def __init__(self, db: Database):\n"
        "        self.db = db\n\n    def get(self, customer_id):\n        return self.db.query('select * from customers where id = ?', customer_id)\n"),
    "ledger/services/__init__.py": "",
    "ledger/services/billing.py": (
        "from ledger.storage.customers import CustomerStore\nfrom ledger.storage.invoices import InvoiceStore\n\n\n"
        "class Billing:\n    def __init__(self, invoices: InvoiceStore, customers: CustomerStore):\n"
        "        self.invoices = invoices\n        self.customers = customers\n\n"
        "    def balance(self, customer_id):\n        return sum(i['amount'] for i in self.invoices.open_for(customer_id))\n"),
    "ledger/services/legacy_export.py": (
        "import csv\n\nfrom ledger.storage.invoices import InvoiceStore\n\n\n"
        "def export(store: InvoiceStore, customer_id, path):\n    with open(path, 'w') as f:\n"
        "        csv.writer(f).writerows(store.open_for(customer_id))\n"),
    "ledger/api/__init__.py": "",
    "ledger/api/app.py": (
        "from fastapi import FastAPI\n\nfrom ledger.api import routes\n\napp = FastAPI()\napp.include_router(routes.router)\n"),
    "ledger/api/routes.py": (
        "from fastapi import APIRouter\n\nfrom ledger.services.billing import Billing\nfrom ledger.services.legacy_export import export\n"
        "from ledger.storage.customers import CustomerStore\nfrom ledger.storage.db import Database\n"
        "from ledger.storage.invoices import InvoiceStore\n\nrouter = APIRouter()\ndb = Database()\n"
        "billing = Billing(InvoiceStore(db), CustomerStore(db))\n\n\n"
        "@router.get('/customers/{customer_id}/balance')\ndef balance(customer_id: str):\n    return {'balance': billing.balance(customer_id)}\n\n\n"
        "@router.post('/customers/{customer_id}/export')\ndef export_csv(customer_id: str):\n"
        "    export(billing.invoices, customer_id, '/tmp/export.csv')\n    return {'ok': True}\n"),
    "web/package.json": '{"name": "ledger-web", "private": true}\n',
    "web/src/client.ts": (
        "export async function balance(customer: string): Promise<number> {\n"
        "  const res = await fetch(`/customers/${customer}/balance`);\n  return (await res.json()).balance;\n}\n\n"
        "export async function exportCsv(customer: string): Promise<void> {\n"
        "  await fetch(`/customers/${customer}/export`, { method: 'POST' });\n}\n"),
    "web/src/views/account.ts": (
        "import { balance, exportCsv } from '../client';\n\n"
        "export async function render(customer: string): Promise<string> {\n"
        "  const b = await balance(customer);\n  return `<p>${b}</p><button onclick=\"${exportCsv}\">CSV</button>`;\n}\n"),
    "docker-compose.yml": (
        "services:\n  api:\n    build: .\n    environment:\n      LEDGER_DB: postgres://db/ledger\n    depends_on: [db]\n"
        "  db:\n    image: postgres:16\n"),
}

HEAD = {
    "ledger/storage/cache.py": (
        "from ledger.storage.invoices import InvoiceStore\n\n\nclass CachedInvoices:\n"
        "    \"\"\"InvoiceStore with a dict in front; never invalidated.\"\"\"\n\n"
        "    def __init__(self, store: InvoiceStore):\n        self.store = store\n        self.hits = {}\n\n"
        "    def open_for(self, customer_id):\n        if customer_id not in self.hits:\n"
        "            self.hits[customer_id] = self.store.open_for(customer_id)\n        return self.hits[customer_id]\n"),
    "ledger/services/alerts.py": (
        "from ledger.storage.db import Database\n\n\nclass OverdueAlerts:\n    def __init__(self, db: Database):\n"
        "        self.db = db\n\n    def overdue(self):\n"
        "        return self.db.query(\"select * from invoices where due < now() and paid = 0\")\n"),
    "ledger/services/billing.py": (
        "from ledger.storage.cache import CachedInvoices\nfrom ledger.storage.customers import CustomerStore\n\n\n"
        "class Billing:\n    def __init__(self, invoices: CachedInvoices, customers: CustomerStore):\n"
        "        self.invoices = invoices\n        self.customers = customers\n\n"
        "    def balance(self, customer_id):\n        return sum(i['amount'] for i in self.invoices.open_for(customer_id))\n\n"
        "    def summary(self, customer_id):\n        return {'customer': self.customers.get(customer_id), 'balance': self.balance(customer_id)}\n"),
    "ledger/services/legacy_export.py": None,
    "ledger/api/routes.py": (
        "from fastapi import APIRouter\n\nfrom ledger.services.alerts import OverdueAlerts\nfrom ledger.services.billing import Billing\n"
        "from ledger.storage.cache import CachedInvoices\nfrom ledger.storage.customers import CustomerStore\n"
        "from ledger.storage.db import Database\nfrom ledger.storage.invoices import InvoiceStore\n\n"
        "router = APIRouter()\ndb = Database()\nbilling = Billing(CachedInvoices(InvoiceStore(db)), CustomerStore(db))\n"
        "alerts = OverdueAlerts(db)\n\n\n"
        "@router.get('/customers/{customer_id}/summary')\ndef summary(customer_id: str):\n    return billing.summary(customer_id)\n\n\n"
        "@router.get('/alerts/overdue')\ndef overdue():\n    return alerts.overdue()\n"),
    "web/src/client.ts": (
        "export async function summary(customer: string): Promise<{ balance: number }> {\n"
        "  const res = await fetch(`/customers/${customer}/summary`);\n  return res.json();\n}\n\n"
        "export async function overdue(): Promise<unknown[]> {\n  return (await fetch('/alerts/overdue')).json();\n}\n"),
    "web/src/views/account.ts": (
        "import { summary } from '../client';\n\n"
        "export async function render(customer: string): Promise<string> {\n"
        "  const s = await summary(customer);\n  return `<p>${s.balance}</p>`;\n}\n"),
    "web/src/views/alerts.ts": (
        "import { overdue } from '../client';\n\n"
        "export async function render(): Promise<string> {\n  const rows = await overdue();\n  return `<p>${rows.length} overdue</p>`;\n}\n"),
}

PR = """Cache invoices and add overdue alerts

Billing reads open invoices through a cache now, the account page uses one summary call,
and there is an overdue-alerts endpoint with a small page. The CSV export was unused and
is gone.
"""

# What a review agent might say about it. Targets are ids in review.mmd.
VERDICTS = [
    {"target": "n_fs_ledger_services_alerts_py --> n_fs_ledger_storage_db_py", "verdict": "problem",
     "reason": "Bypasses the stores: alerts queries the invoices table itself, so the invoice schema now "
               "lives in two places and the cache never sees these reads."},
    {"target": "n_fs_ledger_storage_cache_py", "verdict": "question",
     "reason": "Never invalidated: `CachedInvoices` keeps every customer's open invoices forever, so a paid "
               "invoice stays in the balance until the process restarts. Is that intended?"},
    {"target": "n_fs_ledger_services_billing_py --> n_fs_ledger_storage_g", "verdict": "question",
     "reason": "Billing now depends on the cache class rather than the store it wraps; an interface both "
               "satisfy would keep billing testable without the cache."},
    {"target": "n_fs_web_src_client_ts ==> n_http_GET__alerts_overdue", "verdict": "question",
     "reason": "Unpaged: the client loads every overdue invoice in one response; fine today, but nothing "
               "bounds it as the ledger grows."},
    {"target": "n_fs_ledger_services_legacy_export_py", "verdict": "fine",
     "reason": "Good riddance: the CSV export had no caller in the web client, and its endpoint goes with it."},
    {"target": "n_fs_web_src_views_alerts_ts", "verdict": "fine",
     "reason": "A small page on the new endpoint, through the client like the others."},
    {"target": "n_fs_ledger_storage_customers_py", "verdict": "fine",
     "reason": "Unchanged, now also read by `Billing.summary`."},
]


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
                        "PATH": "/usr/bin:/bin", "HOME": str(repo)})


def write(repo: Path, files: dict[str, str | None]) -> None:
    for rel, text in files.items():
        p = repo / rel
        if text is None:
            p.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)


def build(out: Path) -> Path:
    """The repository under out/ledger, its review dataset under out/data; returns the dataset."""
    from nolli.cli import main

    repo, ds = out / "ledger", out / "data" / DATASET
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "dev@example.com")
    git(repo, "config", "user.name", "dev")
    write(repo, BASE)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "ledger")
    git(repo, "checkout", "-q", "-b", "cache-and-alerts")
    write(repo, HEAD)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", PR.splitlines()[0])
    ds.mkdir(parents=True)
    (ds / "pr.md").write_text(PR)
    if main(["diff", str(repo), "main", "cache-and-alerts", "-o", str(ds), "--title", PR.splitlines()[0]]) != 0:
        raise RuntimeError("nolli diff failed")
    (ds / "verdicts.json").write_text(json.dumps(VERDICTS, indent=1))
    return ds


if __name__ == "__main__":
    print(build(Path(sys.argv[1])))
