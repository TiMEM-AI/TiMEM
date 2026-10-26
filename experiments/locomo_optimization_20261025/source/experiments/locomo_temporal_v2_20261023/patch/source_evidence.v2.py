"""Lossless, timestamped leaf evidence for AML recall.

Only Add payloads enter this index. Search never reads datasets, questions,
answers or labels. SQLite FTS5 supplies a bounded lexical source channel; the
existing TiMem recall remains the semantic/summary channel and fallback.

A0 (opt-in) adds deterministic calendar annotations on top of the V1 evidence:
see `server/temporal_evidence.py`. With `TIMEM_TEMPORAL_RENDERER` unset the
output is byte-identical to V1.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_STOP = set('a an the is are was were be been being do does did have has had of to in on at for from with by and or as it its this that these those i you he she they we my your his her their our what which who when where why how would could should can will likely some any about both between'.split())
_TEMPORAL = re.compile(r'\b(when|how long|how many (?:days|weeks|months|years)|before|after|earlier|later|first|last time|what (?:date|year|month))\b', re.I)

def enabled():
    return os.getenv('TIMEM_SOURCE_EVIDENCE', '1').lower() not in {'0','false','off','no'}

def temporal_renderer_enabled():
    """A0 gate; independent from, and narrower than, the V1 source gate."""
    return os.getenv('TIMEM_TEMPORAL_RENDERER', 'off').strip().lower() in {'calendar_v1', '1', 'on', 'true'}

def default_path():
    return os.getenv('TIMEM_SOURCE_EVIDENCE_PATH', str(Path(__file__).resolve().parents[1]/'data'/'aml_sources.sqlite'))

def _iso(value):
    try:
        if isinstance(value, (float, int)):
            return datetime.fromtimestamp(value/1000, timezone.utc).isoformat()
        if isinstance(value, str) and value.strip():
            return datetime.fromisoformat(value.replace('Z','+00:00')).isoformat()
    except (ValueError, OverflowError, OSError):
        pass
    return ''

class SourceEvidenceStore:
    def __init__(self, path=None):
        self.path = str(path or default_path())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS source_requests (
                    user_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    digest TEXT NOT NULL, PRIMARY KEY(user_id,request_id));
                CREATE TABLE IF NOT EXISTS source_turns (
                    seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,
                    user_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    request_id TEXT NOT NULL, position INTEGER NOT NULL,
                    observed_at TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS source_owner ON source_turns(user_id,session_id,observed_at,seq);
                CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(content, tokenize='porter unicode61');
            ''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path, timeout=30)
        db.row_factory=sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def put(self, user_id, session_id, request_id, messages):
        if not user_id or not request_id:
            raise ValueError('source owner and request id are required')
        digest=hashlib.sha256(json.dumps([session_id,messages],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT digest FROM source_requests WHERE user_id=? AND request_id=?',(user_id,request_id)).fetchone()
            if previous:
                if previous['digest']!=digest:
                    raise ValueError('source request payload differs from stored payload')
                return 0
            n=0
            for pos, msg in enumerate(messages):
                text=str(msg.get('content') or '').strip()
                if not text:
                    continue
                identity=json.dumps([user_id,session_id,request_id,pos])
                sid='src:'+hashlib.sha256(identity.encode()).hexdigest()[:32]
                cursor=db.execute('INSERT INTO source_turns(id,user_id,session_id,request_id,position,observed_at,role,content) VALUES(?,?,?,?,?,?,?,?)',
                    (sid,user_id,session_id,request_id,pos,_iso(msg.get('timestamp')),str(msg.get('role') or 'user'),text))
                db.execute('INSERT INTO source_fts(rowid,content) VALUES(?,?)',(cursor.lastrowid,text))
                n+=1
            db.execute('INSERT INTO source_requests VALUES(?,?,?)',(user_id,request_id,digest))
        return n

    def search(self, user_id, query, limit=20):
        if not user_id or limit<=0:
            return []
        terms=list(dict.fromkeys(t.lower() for t in re.findall(r"[\w]+",query) if t.lower() not in _STOP and len(t)>1))[:32]
        if not terms:
            return []
        expression=' OR '.join('"'+t+'"' for t in terms)
        with self.connect() as db:
            hits=db.execute('''SELECT s.*,bm25(source_fts) AS rank FROM source_fts
                JOIN source_turns s ON s.seq=source_fts.rowid
                WHERE source_fts MATCH ? AND s.user_id=? ORDER BY rank,s.seq LIMIT 100''',(expression,user_id)).fetchall()
            # Both the FTS candidates and context expansion are tenant scoped.
            covered=set(); out=[]; chars=0
            for hit in hits:
                if hit['seq'] in covered:
                    continue
                group=db.execute('''SELECT * FROM source_turns WHERE user_id=? AND session_id=? AND observed_at=? ORDER BY seq''',
                    (user_id,hit['session_id'],hit['observed_at'])).fetchall()
                index=next(i for i,r in enumerate(group) if r['seq']==hit['seq'])
                window=group[max(0,index-2):index+3]
                # Opening exchanges preserve speaker/vocative context. Never
                # borrow a timestamp from an adjacent conversation segment.
                opening=group[:2] if index>3 else []
                unique={r['seq']:r for r in opening+window}
                turns=[unique[k] for k in sorted(unique)]
                lines=[f"[{r['observed_at'] or 'time unknown'}] {r['role']}: {r['content']}" for r in turns]
                text='Original conversation evidence:\n'+'\n'.join(lines)
                if out and chars+len(text)>80000:
                    break
                covered.update(r['seq'] for r in window)
                chars+=len(text)
                out.append({'id':hit['id'],'content':text,'created_at':hit['observed_at'] or None,
                    'score':float(-hit['rank']),'level':'L0','source_ids':[r['id'] for r in turns]})
                if len(out)>=min(limit,20):
                    break
        return out


class ReadOnlySourceIndex:
    """Read-only view used by the offline harness so the snapshot is never written."""

    def __init__(self, path):
        self.path = str(path)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(f'file:{self.path}?mode=ro', uri=True, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()


def read_source_turns(store, user_id, source_ids, *, batch_size=200):
    """Read original turns by id, strictly scoped to the owning user."""
    if not user_id or not source_ids:
        return {}
    from server.temporal_evidence import SourceTurn

    store = store or SourceEvidenceStore()
    unique = list(dict.fromkeys(str(sid) for sid in source_ids if sid))
    found = {}
    with store.connect() as db:
        for start in range(0, len(unique), batch_size):
            batch = unique[start:start + batch_size]
            placeholders = ','.join('?' for _ in batch)
            rows = db.execute(
                f'SELECT id,user_id,role,content,observed_at FROM source_turns '
                f'WHERE user_id=? AND id IN ({placeholders})',
                (user_id, *batch),
            ).fetchall()
            for row in rows:
                found[row['id']] = SourceTurn(
                    source_id=row['id'], user_id=row['user_id'], role=row['role'],
                    content=row['content'], observed_at=row['observed_at'],
                )
    return found


def _ground_evidence_v1(user_id, query, rows, top_k, *, store=None):
    """Frozen V1 behaviour: original dated leaves for time questions."""
    if top_k<=0:
        return []
    if not enabled():
        return list(rows)[:top_k]
    sources=(store or SourceEvidenceStore()).search(user_id,query,min(top_k,20))
    if not sources:
        return list(rows)[:top_k]
    if _TEMPORAL.search(query):
        # Derived dates may have been attached to the chunk rather than the
        # utterance. Original timestamps are the authority for temporal recall.
        return sources[:top_k]
    summaries=[]
    for row in list(rows)[:4]:
        summaries.append({**row,'content':'Derived memory summary:\n'+str(row.get('content') or '')})
    return (sources+summaries)[:top_k]


def ground_evidence(user_id, query, rows, top_k, *, store=None, budget_chars=None):
    """V1 grounding plus the optional A0 calendar annotation layer."""
    legacy = _ground_evidence_v1(user_id, query, rows, top_k, store=store)
    if not temporal_renderer_enabled() or not legacy:
        return legacy
    try:
        from server.temporal_evidence import DEFAULT_BUDGET_CHARS, plan_date_query, render_date_evidence

        plan = plan_date_query(query)
        if plan is None:
            return legacy
        source_ids = []
        for row in legacy:
            for sid in row.get('source_ids') or []:
                source_ids.append(str(sid))
        if not source_ids:
            return legacy
        turns = read_source_turns(store, user_id, source_ids)
        result = render_date_evidence(
            plan, legacy, turns, budget_chars=budget_chars or DEFAULT_BUDGET_CHARS
        )
        return result.rows if result.changed else legacy
    except Exception as exc:  # a renderer failure must never change V1 output
        logger.warning('temporal renderer skipped: %s', exc)
        return legacy
