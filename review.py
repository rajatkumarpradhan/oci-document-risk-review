"""Auditable batch intake: explicit review decisions, correction provenance, policy rules."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REQUIRED = ("invoice_number", "vendor", "amount", "currency", "due_date")


def digest(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()


def normalize(record):
    fields = record.get("fields", {})
    return {k: {"value": (v.get("value") if isinstance(v, dict) else v),
                "confidence": (v.get("confidence") if isinstance(v, dict) else 1.0),
                "origin": (v.get("origin") if isinstance(v, dict) else "fixture")}
            for k, v in fields.items()}


def decide(fields, policy, duplicate=False):
    values = {k: v.get("value") for k, v in fields.items()}
    flags = []
    missing = [key for key in REQUIRED if not values.get(key)]
    if missing:
        flags.append("missing_fields:" + ",".join(missing))
    low = [key for key in REQUIRED if values.get(key) and (fields[key].get("confidence") is None or
                                                   fields[key]["confidence"] < policy["min_confidence"])]
    if low:
        flags.append("low_confidence:" + ",".join(low))
    if duplicate:
        flags.append("duplicate_invoice_number")
    if values.get("vendor") and values["vendor"].casefold() not in [v.casefold() for v in policy["approved_vendors"]]:
        flags.append("unapproved_vendor")
    try:
        amount = float(str(values.get("amount", "")).replace(",", ""))
        if amount < 0:
            flags.append("invalid_amount")
        elif amount > policy["high_value_threshold"]:
            flags.append("high_value_manual_review")
    except (ValueError, TypeError):
        flags.append("invalid_amount")
    if values.get("currency") and values["currency"] not in policy["allowed_currencies"]:
        flags.append("unsupported_currency")
    if values.get("due_date"):
        try:
            datetime.strptime(values["due_date"], "%Y-%m-%d")
        except ValueError:
            flags.append("invalid_due_date")
    return {"decision": "REVIEW" if flags else "PASS", "flags": flags}


def apply_correction(fields, correction, reviewer):
    if not reviewer:
        raise ValueError("Reviewer identity is required")
    name = correction["field"]
    if name not in REQUIRED:
        raise ValueError("Only canonical fields can be corrected")
    old = fields.get(name, {}).get("value")
    fields[name] = {"value": str(correction["value"]), "confidence": 1.0,
                    "origin": "human_correction", "reviewer": reviewer}
    return {"field": name, "old_value": old, "new_value": fields[name]["value"], "reviewer": reviewer}


class Ledger:
    """Local SQLite idempotency + append-only audit events within a trusted process."""
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY, source_hash TEXT NOT NULL, result_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT, review_id TEXT NOT NULL,
            event TEXT NOT NULL, timestamp TEXT NOT NULL, payload TEXT NOT NULL, prev_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL);
        """)

    def _event(self, review_id, event, payload):
        previous = self.db.execute("SELECT event_hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        prev = previous[0] if previous else "GENESIS"
        stamp = datetime.now(timezone.utc).isoformat()
        canonical = json.dumps(payload, sort_keys=True)
        event_hash = hashlib.sha256(f"{prev}|{review_id}|{event}|{stamp}|{canonical}".encode()).hexdigest()
        self.db.execute("INSERT INTO audit(review_id,event,timestamp,payload,prev_hash,event_hash) VALUES(?,?,?,?,?,?)",
                        (review_id, event, stamp, canonical, prev, event_hash))

    def process(self, record, policy, corrections=(), reviewer=None):
        source_hash = digest(record)
        review_id = record.get("source", source_hash)
        existing = self.db.execute("SELECT source_hash,result_json FROM reviews WHERE id=?", (review_id,)).fetchone()
        if existing:
            if existing[0] != source_hash:
                raise ValueError("Source identifier reused for different content")
            return json.loads(existing[1])  # exact retry: no duplicate events
        fields = normalize(record)
        edits = [apply_correction(fields, change, reviewer) for change in corrections]
        invoice = fields.get("invoice_number", {}).get("value")
        # Duplicate across persisted records, including repeated batch calls.
        past = self.db.execute("SELECT result_json FROM reviews").fetchall()
        duplicate = bool(invoice and any(json.loads(row[0])["fields"].get("invoice_number", {}).get("value") == invoice
                                         for row in past))
        decision = decide(fields, policy, duplicate=duplicate)
        result = {"source": review_id, "fields": fields, "table_rows": record.get("table_rows", []),
                  "edits": edits, **decision}
        with self.db:
            self.db.execute("INSERT INTO reviews(id,source_hash,result_json) VALUES(?,?,?)",
                            (review_id, source_hash, json.dumps(result, sort_keys=True)))
            self._event(review_id, "review_created", {"source_hash": source_hash, "decision": decision})
            for edit in edits:
                self._event(review_id, "field_corrected", edit)
        return result

    def verify_chain(self):
        previous = "GENESIS"
        for review_id, event, stamp, payload, prev, current in self.db.execute(
            "SELECT review_id,event,timestamp,payload,prev_hash,event_hash FROM audit ORDER BY seq"):
            if prev != previous or hashlib.sha256(f"{prev}|{review_id}|{event}|{stamp}|{payload}".encode()).hexdigest() != current:
                return False
            previous = current
        return True

    def close(self):
        self.db.close()
