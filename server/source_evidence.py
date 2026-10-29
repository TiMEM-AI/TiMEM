"""Lossless sources mapped to native memory IDs, with tenant-scoped FTS retrieval."""
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, UTC
from pathlib import Path

_STOP = set('a an the is are was were do did have has of to in on at for from with and or i you my what which who when how many much'.split())


def _expression(query):
    terms = list(dict.fromkeys(t.lower() for t in re.findall(r'\w+', query) if t.lower() not in _STOP))[:32]
    return ' OR '.join('"' + t + '"' for t in terms)


class SourceStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS chunks (
                    user TEXT, id TEXT, session TEXT, digest TEXT,
                    PRIMARY KEY(user,id));
                CREATE TABLE IF NOT EXISTS sources (
                    seq INTEGER PRIMARY KEY, user TEXT, parent TEXT, source_id TEXT,
                    role TEXT, observed_at TEXT, content TEXT);
                CREATE INDEX IF NOT EXISTS source_owner ON sources(user,parent);
                CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(content, tokenize='porter unicode61');
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def put(self, user, memory_id, session, messages):
        digest = hashlib.sha256(json.dumps([session, messages], sort_keys=True).encode()).hexdigest()
        with self.connect() as db:
            old = db.execute('SELECT digest FROM chunks WHERE user=? AND id=?', (user, memory_id)).fetchone()
            if old:
                if old['digest'] != digest:
                    raise ValueError('Source payload changed for existing memory')
                return
            db.execute('INSERT INTO chunks VALUES(?,?,?,?)', (user, memory_id, session, digest))
            for pos, message in enumerate(messages):
                stamp = message.get('timestamp')
                observed = datetime.fromtimestamp(stamp / 1000, UTC).isoformat() if stamp is not None else ''
                source_id = hashlib.sha256(json.dumps([user, memory_id, pos]).encode()).hexdigest()
                row = db.execute('INSERT INTO sources(user,parent,source_id,role,observed_at,content) VALUES(?,?,?,?,?,?)',
                                 (user, memory_id, source_id, message['role'], observed, message['content']))
                db.execute('INSERT INTO source_fts(rowid,content) VALUES(?,?)', (row.lastrowid, message['content']))

    def get(self, user, memory_id):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM sources WHERE user=? AND parent=? ORDER BY seq', (user, memory_id))]

    def search(self, user, query, limit=100):
        expression = _expression(query)
        if not expression:
            return []
        with self.connect() as db:
            rows = db.execute('''SELECT s.parent, bm25(source_fts) AS rank
                FROM source_fts JOIN sources s ON s.seq=source_fts.rowid
                WHERE source_fts MATCH ? AND s.user=? ORDER BY rank LIMIT ?''',
                              (expression, user, limit * 20)).fetchall()
        return list(dict.fromkeys(r['parent'] for r in rows))[:limit]

    def windows(self, user, memory_id, query):
        """Return up to three best matching messages with immediate neighbours."""
        expression = _expression(query)
        if not expression:
            return []
        with self.connect() as db:
            hits = db.execute('''SELECT s.seq, bm25(source_fts) AS rank
                FROM source_fts JOIN sources s ON s.seq=source_fts.rowid
                WHERE source_fts MATCH ? AND s.user=? AND s.parent=?
                ORDER BY rank, s.seq LIMIT 3''', (expression, user, memory_id)).fetchall()
            if not hits:
                return []
            turns = [dict(row) for row in db.execute(
                'SELECT * FROM sources WHERE user=? AND parent=? ORDER BY seq', (user, memory_id))]
        index = {turn['seq']: pos for pos, turn in enumerate(turns)}
        selected = set()
        for hit in hits:
            position = index[hit['seq']]
            selected.update(range(max(0, position - 1), min(len(turns), position + 2)))
        return [turns[position] for position in sorted(selected)]


def supplement(store, user, query, native_records, top_k):
    """Merge native memories and scoped source hits; retain the native summary."""
    source_ids = store.search(user, query, limit=100)
    source_records = []
    source_windows = {}
    for memory_id in source_ids:
        turns = store.windows(user, memory_id, query)
        if turns:
            source_windows[memory_id] = turns
            source_records.append({'id': memory_id})

    records, scores = {}, {}
    for channel in (native_records, source_records):
        seen = set()
        for rank, record in enumerate(channel):
            memory_id = str(record.get('id') or '')
            if not memory_id or memory_id in seen:
                continue
            seen.add(memory_id)
            records.setdefault(memory_id, record)
            scores[memory_id] = scores.get(memory_id, 0) + 1 / (61 + rank)

    result = []
    for memory_id in sorted(scores, key=scores.get, reverse=True)[:max(0, top_k)]:
        record = dict(records[memory_id])
        turns = source_windows.get(memory_id)
        if turns is None:
            turns = store.windows(user, memory_id, query)
        if turns:
            lines = [f"[source={turn['source_id']}; observed_at={turn['observed_at'] or 'unknown'}; role={turn['role']}]\n{turn['content']}" for turn in turns]
            evidence = 'Original conversation evidence:\n' + '\n'.join(lines)
            summary = record.get('content')
            record['content'] = ('Native memory summary:\n' + summary + '\n\n' if summary else '') + evidence
            record.setdefault('created_at', turns[0]['observed_at'] or None)
        result.append(record)
    return result
