def annotate(rows: Sequence[dict], turns: Dict[str, dict], query: str, budget_chars: int = 8000) -> Tuple[List[dict], List[dict], bool]:
    if arm_b.a0.plan_date_query(query) is None:
        return list(rows), [], False
    c1_rows, trace_b, _ = arm_b.annotate(rows, turns, query, budget_chars=budget_chars)
    week_rows, trace_w, changed = week_ext.add_week_anchors(
        c1_rows, rows, turns, with_range=True, budget_chars=budget_chars)
    return week_rows, trace_b + trace_w, changed
