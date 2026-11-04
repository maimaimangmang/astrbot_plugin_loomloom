"""Local durable confirmation ledger. Credentials are never persisted here."""
import json
import sqlite3
from pathlib import Path

class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.db = sqlite3.connect(self.root / 'tasks.sqlite3')
        self.db.execute('CREATE TABLE IF NOT EXISTS records (code TEXT PRIMARY KEY, owner TEXT NOT NULL, body TEXT NOT NULL)')
        self.db.commit()
        (self.root / 'tasks.sqlite3').chmod(0o600)

    def put(self, record):
        self.db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)', (record['code'], record['owner'], json.dumps(record, ensure_ascii=False)))
        self.db.commit()

    def get(self, code, owner):
        row = self.db.execute('SELECT body FROM records WHERE code=? AND owner=?', (code, owner)).fetchone()
        return json.loads(row[0]) if row else None

    def by_run(self, run_id, owner):
        return next((r for r in self.list(owner) if r.get('run_id') == run_id), None)

    def list(self, owner):
        return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM records WHERE owner=? ORDER BY rowid DESC', (owner,))]

    def all(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM records')]

    def close(self):
        self.db.close()
