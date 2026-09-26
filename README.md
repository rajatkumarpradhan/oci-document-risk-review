# OCI Document Risk Review

A synthetic invoice intake pipeline for finance operations. It reads an offline fixture without any cloud account, or extracts text from a real document using **OCI AI Document Understanding**. A transparent rules engine flags duplicate invoice numbers, unknown vendors, missing fields and high amounts. It does **not** approve or pay invoices.

> Portfolio simulation, not an Oracle internal project. The offline demo was tested; the OCI branch requires a configured tenancy, service access and a real document and has not been exercised against a live tenancy.

## Architecture

```text
Synthetic JSON fixture OR PDF/image -> OCI Document Understanding text extraction
    -> key-value/table extraction with confidence -> policy + corrections -> SQLite audit/JSON review queue
```

No production invoices or credentials are included. Offline data and company names are invented. This is a reference workflow, not a full fraud model: duplicate detection persists in SQLite when --ledger is provided, regex extraction expects labeled fields, and a human makes the final decision. For production, persist idempotency keys in a database, validate currencies/dates, manage PII, encrypt documents, add approval audit logs and review thresholds with finance owners.

## Run offline

Python 3.10+ is enough; no pip install is needed:

```bash
python3 app.py --mode offline
python3 -m unittest -v
python3 app.py --mode offline --output output.json
```

Expected: three synthetic records; one PASS and two REVIEW (duplicate and high-value/unapproved vendor). `--input` can point to another JSON document with `approved_vendors` and `invoices` following `sample_invoices.json`.

## Switch to OCI

1. Enable OCI AI Document Understanding in a supported region and set IAM permissions for document analysis in the target compartment. Pricing and model availability depend on tenancy/region; check current Oracle documentation before use.
2. Create your own OCI API signing-key config at `~/.oci/config` (do not commit it); choose a profile and set `OCI_CONFIG_FILE` if stored elsewhere. See [SDK configuration](https://docs.oracle.com/en-us/iaas/Content/API/Concepts/sdkconfig.htm).
3. Install `python3 -m pip install -r requirements.txt` and set `OCI_COMPARTMENT_OCID`, optionally `OCI_PROFILE` and `APPROVED_VENDORS` as comma-separated exact vendor names.
4. Run `python3 app.py --mode oci --input invoice.pdf`. The file is sent to OCI as inline base64; the demo limits input to 10 MiB. Do not use private financial documents until retention and access policies are reviewed.

The OCR request uses the official `oci.ai_document.AIServiceDocumentClient.analyze_document` API with `AnalyzeDocumentDetails`, `InlineDocumentDetails`, and `DocumentTextExtractionFeature`. See [Oracle SDK API](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/ai_document/client/oci.ai_document.AIServiceDocumentClient.html) and [Oracle example](https://docs.oracle.com/en-us/iaas/tools/python-sdk-examples/latest/aidocument/analyze_document.py.html). The OCI response's page lines are normalized by explicit regexes, not claimed to be perfect extraction. Configure endpoint/region with the OCI SDK profile. No cloud deployment is supplied.

## Test boundary

`python3 -m unittest -v` checks the deterministic offline decision path. Real OCI invocation is not part of CI and may incur service charges. This repository contains no secrets, private dataset or proprietary Oracle materials.

## Review workflow and policy

The deeper workflow is in `review.py`. `policy.json` controls exact approved vendors, allowed currencies, review threshold and minimum extraction confidence. A local SQLite ledger records each review once by source ID and content hash, checks duplicate invoice numbers across runs, and writes a chained event log. `--ledger path/to/reviews.sqlite` persists it; default is an in-memory demo. The hash chain detects accidental/naive tampering but is **not** a cryptographically trusted audit system because an operator with database write access can rebuild it. Restrict DB permissions and add external signing/WORM storage for real audits.

A reviewer can submit a JSON corrections file, e.g. `{"synthetic-003":[{"field":"vendor","value":"Bluebird Logistics"}]}`, then run:

```bash
python3 app.py --corrections corrections.json --reviewer qa@example.test --ledger reviews.sqlite
python3 evaluate.py
```

Corrections preserve original and new values plus reviewer identity in the event log. No automatic financial approval follows a PASS. The four labeled synthetic policy cases in `eval_cases.json` report decision accuracy, **not** OCR extraction precision. Never commit real documents or a ledger containing sensitive data. The OCI response parser uses `KEY_VALUE_EXTRACTION`, `TABLE_EXTRACTION` and `TEXT_EXTRACTION`; it carries key-value confidences and table cell confidences into the review output, with a low-confidence review flag. If an extracted field lacks a key-value match, a regex fallback records origin `ocr_regex` and confidence 0.5, requiring review. Validate target model/region support, quotas and invoice field names on your own tenant before relying on it.

These controls are a runnable reference, not a deployed production approval system. OCI mode has not been live-tested. Oracle SDK surface reference: [key-value feature](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/ai_document/models/oci.ai_document.models.DocumentKeyValueExtractionFeature.html), [table feature](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/ai_document/models/oci.ai_document.models.DocumentTableExtractionFeature.html).
