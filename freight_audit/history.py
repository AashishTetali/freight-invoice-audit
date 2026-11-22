"""Status changes are recorded as NEW history rows, never by overwriting.

`transition` appends an event (from -> to) and updates the record's
current_status snapshot so the two always agree.
"""


def transition(record, history, key, to_status, when, actor, note=None):
    history.append(dict(event_id=None, **{key: record[key]},
                        from_status=record.get("current_status"), to_status=to_status,
                        event_date=when, actor_role=actor, note=note))
    record["current_status"] = to_status


def assign_event_ids(rows, prefix, key):
    """Number events by entity then date. Python's sort is stable, so events
    on the same day keep the order in which they happened."""
    rows.sort(key=lambda e: (e[key], e["event_date"]))
    for i, e in enumerate(rows, 1):
        e["event_id"] = f"{prefix}-{i:07d}"


def sync_current_status(records, history, key):
    last = {}
    for e in history:                  # history is sorted by entity, then date
        last[e[key]] = e["to_status"]
    for r in records:
        r["current_status"] = last.get(r[key])
