"""Neo4j storage for memory episodes. A missing URI or password disables the graph."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from timem.memory_graph.episode import merge_source_l2_ids
from timem.utils.logging import get_logger

logger = get_logger(__name__)

_REL_TYPES = (
    "MENTIONS",
    "HAS_SUBJECT",
    "HAS_OBJECT",
    "HAS_LOCATION",
    "HAS_TIME",
    "HAS_CAUSE",
    "HAS_RESULT",
    "HAS_ACTION",
)


def neo4j_settings() -> Optional[Dict[str, str]]:
    uri = (os.getenv("NEO4J_URI") or "").strip()
    password = (os.getenv("NEO4J_PASSWORD") or "").strip()
    user = (os.getenv("NEO4J_USER") or "").strip()
    database = (os.getenv("NEO4J_DATABASE") or "").strip()
    if not uri or not password:
        try:
            from timem.utils.config_manager import get_config_manager

            cfg = get_config_manager().get_config("storage.graph") or {}
            uri = uri or str(cfg.get("uri") or "").strip()
            password = password or str(cfg.get("password") or "").strip()
            user = user or str(cfg.get("user") or "").strip()
            database = database or str(cfg.get("database") or "").strip()
        except Exception as exc:
            logger.warning("memory graph config skipped: %s", exc)
    if not uri or not password:
        return None
    return {
        "uri": uri,
        "user": user or "neo4j",
        "password": password,
        "database": database or "neo4j",
    }


class DisabledMemoryGraph:
    enabled = False

    async def find_l2(self, user_id: str, expert_id: str, l2_id: str) -> Optional[Dict[str, Any]]:
        return None

    async def write_episode(self, episode: Dict[str, Any], facts: List[Dict[str, str]]) -> None:
        return None

    async def context_nodes(self, user_id: str, expert_id: str, question: str) -> List[Dict[str, Any]]:
        return []


class MemoryGraphStore:
    """Episode and entity writer. Labels stay off the legacy :Memory nodes."""

    enabled = True

    def __init__(self, settings: Dict[str, str]):
        self.settings = settings
        self._driver = None

    def _driver_or_create(self):
        if self._driver is None:
            from neo4j import GraphDatabase

            self._driver = GraphDatabase.driver(
                self.settings["uri"],
                auth=(self.settings["user"], self.settings["password"]),
            )
        return self._driver

    def _run(self, cypher: str, **params):
        driver = self._driver_or_create()
        with driver.session(database=self.settings["database"]) as session:
            return list(session.run(cypher, **params))

    async def find_l2(self, user_id: str, expert_id: str, l2_id: str) -> Optional[Dict[str, Any]]:
        rows = self._run(
            """
            MATCH (e:MemoryEpisode {user_id: $user_id, expert_id: $expert_id, l2_id: $l2_id})
            RETURN e.content_hash AS content_hash, e.episode_id AS episode_id
            LIMIT 1
            """,
            user_id=user_id,
            expert_id=expert_id,
            l2_id=l2_id,
        )
        if not rows:
            return None
        row = rows[0]
        return {"content_hash": row["content_hash"], "episode_id": row["episode_id"]}

    async def write_episode(self, episode: Dict[str, Any], facts: List[Dict[str, str]]) -> None:
        self._run(
            """
            MERGE (e:MemoryEpisode {episode_id: $episode_id})
            SET e.user_id = $user_id,
                e.expert_id = $expert_id,
                e.l2_id = $l2_id,
                e.content_hash = $content_hash,
                e.name = $name,
                e.sentence = $sentence,
                e.report_date = $report_date,
                e.episode_key = $episode_key,
                e.source_l2_ids = $source_l2_ids,
                e.node_id = $episode_id,
                e.embedding = $embedding
            """,
            episode_id=episode["episode_id"],
            user_id=episode["user_id"],
            expert_id=episode["expert_id"],
            l2_id=episode["l2_id"],
            content_hash=episode["content_hash"],
            name=episode["name"],
            sentence=episode["sentence"],
            report_date=episode["report_date"],
            episode_key=episode["episode_key"],
            source_l2_ids=[episode["l2_id"]],
            embedding=episode.get("embedding"),
        )
        self._run(
            """
            MATCH (e:MemoryEpisode {episode_id: $episode_id})
            OPTIONAL MATCH (e)-[r]->()
            DELETE r
            """,
            episode_id=episode["episode_id"],
        )
        for fact in facts:
            rel = fact["rel_type"]
            if rel not in _REL_TYPES:
                continue
            self._run(
                f"""
                MATCH (e:MemoryEpisode {{episode_id: $episode_id}})
                MERGE (n:MemoryEntity {{entity_id: $entity_id}})
                ON CREATE SET n.source_l2_ids = $source_l2_ids
                ON MATCH SET n.source_l2_ids = [x IN coalesce(n.source_l2_ids, []) WHERE NOT x IN $source_l2_ids] + $source_l2_ids
                SET n.name = $name,
                    n.slot = $slot,
                    n.user_id = $user_id,
                    n.expert_id = $expert_id,
                    n.sentence = $sentence,
                    n.node_id = $entity_id,
                    n.embedding = $embedding
                MERGE (e)-[:MENTIONS]->(n)
                MERGE (e)-[:{rel}]->(n)
                """,
                episode_id=episode["episode_id"],
                entity_id=fact["entity_id"],
                name=fact["name"],
                slot=fact["slot"],
                sentence=fact["sentence"],
                user_id=episode["user_id"],
                expert_id=episode["expert_id"],
                source_l2_ids=merge_source_l2_ids([episode["l2_id"]], episode["l2_id"]),
                embedding=fact.get("embedding"),
            )

    async def lexical_seed_ids(self, user_id: str, expert_id: str, query: str, limit: int) -> List[str]:
        tokens = _query_tokens(query)
        if not tokens or limit <= 0:
            return []
        rows = self._run(
            """
            MATCH (n)
            WHERE (n:MemoryEpisode OR n:MemoryEntity)
              AND n.user_id = $user_id AND n.expert_id = $expert_id
              AND any(tok IN $tokens WHERE toLower(coalesce(n.name, '')) CONTAINS tok)
            RETURN coalesce(n.node_id, n.episode_id, n.entity_id) AS id
            LIMIT $limit
            """,
            user_id=user_id,
            expert_id=expert_id,
            tokens=tokens,
            limit=limit,
        )
        return _ids_from_rows(rows)

    async def fulltext_seed_ids(self, user_id: str, expert_id: str, query: str, limit: int) -> List[str]:
        tokens = _query_tokens(query)
        if not tokens or limit <= 0:
            return []
        rows = self._run(
            """
            MATCH (n)
            WHERE (n:MemoryEpisode OR n:MemoryEntity)
              AND n.user_id = $user_id AND n.expert_id = $expert_id
              AND any(tok IN $tokens WHERE toLower(coalesce(n.sentence, '')) CONTAINS tok)
            RETURN coalesce(n.node_id, n.episode_id, n.entity_id) AS id
            LIMIT $limit
            """,
            user_id=user_id,
            expert_id=expert_id,
            tokens=tokens,
            limit=limit,
        )
        return _ids_from_rows(rows)

    async def vector_seed_ids(self, user_id: str, expert_id: str, vector: List[float], limit: int) -> List[str]:
        if not vector or limit <= 0:
            return []
        rows = self._run(
            """
            MATCH (n)
            WHERE (n:MemoryEpisode OR n:MemoryEntity)
              AND n.user_id = $user_id AND n.expert_id = $expert_id
              AND n.embedding IS NOT NULL
            RETURN coalesce(n.node_id, n.episode_id, n.entity_id) AS id, n.embedding AS embedding
            LIMIT 500
            """,
            user_id=user_id,
            expert_id=expert_id,
        )
        ranked = []
        for row in rows:
            node_id = str(row["id"] or "").strip()
            score = _cosine(vector, row["embedding"] or [])
            if node_id and score > 0:
                ranked.append((score, node_id))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [node_id for _, node_id in ranked[:limit]]

    async def expand(self, user_id: str, expert_id: str, seed_ids: List[str], hops: int, limit: int) -> Dict[str, List]:
        if not seed_ids or limit <= 0:
            return {"nodes": [], "edges": []}
        hop_bound = min(max(int(hops or 1), 1), 3)
        rows = self._run(
            f"""
            MATCH (n)
            WHERE (n:MemoryEpisode OR n:MemoryEntity)
              AND n.user_id = $user_id AND n.expert_id = $expert_id
              AND coalesce(n.node_id, n.episode_id, n.entity_id) IN $seed_ids
            OPTIONAL MATCH path = (n)-[rels*1..{hop_bound}]-(m)
            WHERE (m:MemoryEpisode OR m:MemoryEntity)
              AND m.user_id = $user_id AND m.expert_id = $expert_id
            WITH n, rels, m
            LIMIT $limit
            RETURN coalesce(n.node_id, n.episode_id, n.entity_id) AS from_id,
                   n.name AS from_name,
                   labels(n) AS from_labels,
                   n.source_l2_ids AS from_l2,
                   coalesce(m.node_id, m.episode_id, m.entity_id) AS to_id,
                   m.name AS to_name,
                   labels(m) AS to_labels,
                   m.source_l2_ids AS to_l2,
                   [r IN coalesce(rels, []) | type(r)] AS rel_types,
                   m.sentence AS fact
            """,
            user_id=user_id,
            expert_id=expert_id,
            seed_ids=seed_ids,
            limit=limit,
        )
        return _view_parts(rows, seed_ids)

    async def context_nodes(self, user_id: str, expert_id: str, question: str) -> List[Dict[str, Any]]:
        tokens = _query_tokens(question)
        if not tokens:
            return []
        rows = self._run(
            """
            MATCH (seed)
            WHERE (seed:MemoryEpisode OR seed:MemoryEntity)
              AND seed.user_id = $user_id
              AND seed.expert_id = $expert_id
              AND any(tok IN $tokens WHERE
                    toLower(coalesce(seed.name, '')) CONTAINS tok
                    OR toLower(coalesce(seed.sentence, '')) CONTAINS tok)
            WITH seed LIMIT 20
            CALL {
              WITH seed
              RETURN seed AS node
              UNION
              WITH seed
              MATCH (seed)-[*1..3]-(node)
              WHERE (node:MemoryEpisode OR node:MemoryEntity)
                AND node.user_id = $user_id
                AND node.expert_id = $expert_id
              RETURN node
            }
            RETURN DISTINCT node.name AS name,
                            node.sentence AS sentence,
                            node.source_l2_ids AS source_l2_ids
            """,
            user_id=user_id,
            expert_id=expert_id,
            tokens=tokens,
        )
        nodes = []
        for row in rows:
            name = str(row["name"] or "").strip()
            if not name:
                continue
            raw_ids = row["source_l2_ids"] or []
            if not isinstance(raw_ids, list):
                raw_ids = [raw_ids]
            nodes.append({
                "name": name,
                "sentence": str(row["sentence"] or ""),
                "source_l2_ids": [str(item) for item in raw_ids if str(item or "").strip()],
            })
        return nodes


def _ids_from_rows(rows) -> List[str]:
    ids = []
    for row in rows:
        node_id = str(row["id"] or "").strip()
        if node_id and node_id not in ids:
            ids.append(node_id)
    return ids


def _cosine(left: List[float], right: List[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right):
        dot += float(a) * float(b)
        left_norm += float(a) * float(a)
        right_norm += float(b) * float(b)
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / ((left_norm ** 0.5) * (right_norm ** 0.5))


def _label_of(labels) -> str:
    names = [str(item) for item in (labels or [])]
    if "MemoryEpisode" in names:
        return "MemoryEpisode"
    if "MemoryEntity" in names:
        return "MemoryEntity"
    return names[0] if names else "MemoryGraph"


def _l2_ids(raw) -> List[str]:
    if not isinstance(raw, list):
        raw = [raw] if raw else []
    return [str(item) for item in raw if str(item or "").strip()]


def _view_parts(rows, seed_ids: List[str]) -> Dict[str, List]:
    nodes: Dict[str, Dict[str, Any]] = {}
    edges = []
    seen_edges = set()

    def _add_node(node_id, name, labels, source_l2):
        if not node_id or node_id in nodes:
            return
        nodes[node_id] = {
            "id": node_id,
            "name": str(name or ""),
            "label": _label_of(labels),
            "source_l2_ids": _l2_ids(source_l2),
        }

    for row in rows:
        _add_node(str(row["from_id"] or ""), row["from_name"], row["from_labels"], row["from_l2"])
        to_id = str(row["to_id"] or "")
        if to_id:
            _add_node(to_id, row["to_name"], row["to_labels"], row["to_l2"])
            rels = row["rel_types"] or []
            rel = str(rels[-1] if rels else "RELATED")
            key = (str(row["from_id"] or ""), rel, to_id)
            if key not in seen_edges:
                seen_edges.add(key)
                edges.append({
                    "id": ":".join(key),
                    "type": rel,
                    "from": key[0],
                    "to": to_id,
                    "fact": str(row["fact"] or ""),
                })
    ordered = [nodes[item] for item in seed_ids if item in nodes]
    ordered.extend(node for node_id, node in nodes.items() if node_id not in seed_ids)
    return {"nodes": ordered, "edges": edges}


def _query_tokens(question: str) -> List[str]:
    """Whitespace words, plus two-character slices for uninterrupted Chinese."""
    text = (question or "").lower()
    for mark in ("，", "。", "？", "?", "！", "!", "、"):
        text = text.replace(mark, " ")
    parts: List[str] = []
    for raw in text.split():
        token = raw.strip()
        if len(token) >= 2 and token not in parts:
            parts.append(token)
        if any("\u4e00" <= char <= "\u9fff" for char in token) and len(token) > 2:
            for index in range(len(token) - 1):
                gram = token[index : index + 2]
                if gram not in parts:
                    parts.append(gram)
                if len(parts) >= 12:
                    return parts
        if len(parts) >= 12:
            break
    return parts[:12]


_store: Optional[Any] = None


def get_store():
    global _store
    if _store is not None:
        return _store
    settings = neo4j_settings()
    _store = DisabledMemoryGraph() if settings is None else MemoryGraphStore(settings)
    return _store


def set_store(store) -> None:
    global _store
    _store = store
