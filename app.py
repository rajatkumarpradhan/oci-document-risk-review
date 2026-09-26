"""Document risk intake demo: offline fixture or OCI Document Understanding."""
import argparse
import base64
import json
import os
import re
from pathlib import Path
from review import Ledger

ROOT = Path(__file__).resolve().parent
REQUIRED = ("invoice_number", "vendor", "amount", "currency", "due_date")


def extract_offline(path):
    # Fixture is synthetic and deliberately contains no personal data.
    return json.loads(Path(path).read_text(encoding="utf-8"))


def extract_oci(path):
    import oci  # Optional: offline runs need no SDK or credentials.
    config = oci.config.from_file(file_location=os.getenv("OCI_CONFIG_FILE", "~/.oci/config"),
                                  profile_name=os.getenv("OCI_PROFILE", "DEFAULT"))
    compartment = os.environ["OCI_COMPARTMENT_OCID"]
    content = Path(path).read_bytes()
    if not content or len(content) > 10 * 1024 * 1024:
        raise ValueError("Provide a nonempty document of at most 10 MiB")
    client = oci.ai_document.AIServiceDocumentClient(config)
    detail = oci.ai_document.models.AnalyzeDocumentDetails(
        compartment_id=compartment,
        document=oci.ai_document.models.InlineDocumentDetails(
            source="INLINE", data=base64.b64encode(content).decode("ascii")),
        features=[oci.ai_document.models.DocumentTextExtractionFeature(feature_type="TEXT_EXTRACTION"),
                  oci.ai_document.models.DocumentKeyValueExtractionFeature(feature_type="KEY_VALUE_EXTRACTION"),
                  oci.ai_document.models.DocumentTableExtractionFeature(feature_type="TABLE_EXTRACTION")])
    result = client.analyze_document(analyze_document_details=detail).data
    lines = [line.text for page in result.pages or [] for line in page.lines or []]
    text = "\n".join(lines)
    # Transparent regex normalization for this synthetic invoice format, not a generic invoice model.
    patterns = {
        "invoice_number": r"Invoice(?:\s+No\.?|\s+Number)?\s*[:#]\s*([A-Za-z0-9-]+)",
        "vendor": r"Vendor\s*:\s*([^\n]+)",
        "amount": r"Total\s*:\s*(?:[A-Z]{3}\s*)?([0-9,]+(?:\.[0-9]{2})?)",
        "currency": r"(?:Currency\s*:\s*|Total\s*:\s*)([A-Z]{3})\b",
        "due_date": r"Due\s+Date\s*:\s*(\d{4}-\d{2}-\d{2})",
    }
    fields = {}
    for name, pattern in patterns.items():
        match = re.search(pattern, text, re.I)
        fields[name] = match.group(1).strip() if match else None
    confidence = {}
    table_rows = []
    for page in result.pages or []:
        for field in page.document_fields or []:
            label = getattr(getattr(field, "field_name", None), "name", None) or getattr(getattr(field, "field_label", None), "name", None)
            value = getattr(field, "field_value", None)
            if label and value and value.text:
                key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
                aliases = {"invoice_no": "invoice_number", "invoice_number": "invoice_number", "total": "amount", "due_date": "due_date", "vendor": "vendor", "currency": "currency"}
                canonical = aliases.get(key)
                if canonical:
                    fields[canonical] = value.text.strip()
                    confidence[canonical] = value.confidence
        for table in page.tables or []:
            for row in (table.header_rows or []) + (table.body_rows or []) + (table.footer_rows or []):
                table_rows.append([{"text": cell.text, "confidence": cell.confidence} for cell in row.cells or []])
    return {"fields": {k: {"value": v, "confidence": confidence.get(k, 0.5),
                          "origin": "oci_key_value" if k in confidence else "ocr_regex"} for k, v in fields.items()},
            "source": str(path), "table_rows": table_rows, "extracted_text": text}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=("offline", "oci"), default="offline")
    p.add_argument("--input", help="Offline JSON fixture or OCI PDF/image")
    p.add_argument("--output", help="Optional JSON output path")
    p.add_argument("--ledger", help="Persist idempotency and audit events to local SQLite")
    p.add_argument("--policy", default=str(ROOT / "policy.json"))
    p.add_argument("--corrections", help="Optional JSON mapping from source ID to list of field/value corrections")
    p.add_argument("--reviewer", help="Required if applying corrections")
    args = p.parse_args(argv)
    if args.mode == "offline":
        fixture = extract_offline(args.input or ROOT / "sample_invoices.json")
        records = fixture["invoices"]
        approved = fixture["approved_vendors"]
    else:
        if not args.input:
            p.error("--input is required in OCI mode")
        records = [extract_oci(args.input)]
        approved = os.getenv("APPROVED_VENDORS", "Northstar Cloud Services").split(",")
    policy = json.loads(Path(args.policy).read_text(encoding="utf-8"))
    if args.mode == "offline":
        policy["approved_vendors"] = approved
    if args.mode == "oci":
        policy["approved_vendors"] = approved
    corrections = json.loads(Path(args.corrections).read_text()) if args.corrections else {}
    if corrections and not args.reviewer:
        p.error("--reviewer is required with --corrections")
    ledger = Ledger(args.ledger or ":memory:")
    try:
        results = [ledger.process(item, policy, corrections=corrections.get(item.get("source"), []),
                                  reviewer=args.reviewer) for item in records]
        audit_ok = ledger.verify_chain()
    finally:
        ledger.close()
    payload = {"mode": args.mode, "processed": len(results), "results": results, "audit_chain_valid": audit_ok,
               "notice": "Decision support only; a human must approve payments."}
    serialized = json.dumps(payload, indent=2)
    if args.output:
        Path(args.output).write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return payload


if __name__ == "__main__":
    main()
