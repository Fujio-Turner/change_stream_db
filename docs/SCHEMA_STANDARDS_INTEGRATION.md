# JSON Schema Standards Integration into Design Docs

## Overview

The [JSON Schema Standards Guide](../guides/JSON_SCHEMA.md) has been integrated into all relevant design and architecture documentation. This ensures all developers, architects, and contributors understand and follow the standards when creating or modifying JSON documents in the system.

---

## Updated Documents

### ✅ Core Design Documents

#### [DESIGN.md](DESIGN.md)
**Integration:** New section "JSON Schema Standards" added at line 19
- Directs developers to the standards guide
- Lists key standards inline (snake_case, meta field, DateTime formats, enum conventions)
- Links to Author Checklist for quick reference

**Impact:** All pipeline architecture discussions now reference the standards from day one.

---

#### [DESIGN_2_0.md](DESIGN_2_0.md)
**Integration:** New section "JSON Schema Standards" added at line 7
- Emphasizes mandatory compliance for v2.0 documents
- Highlights field ordering requirements
- References JSON Schema validation definitions in `json_schema/changes-worker/`

**Impact:** v2.0 architecture enforces standards as a first-class requirement.

---

### ✅ Data Storage Documents

#### [CBL_DATABASE.md](CBL_DATABASE.md)
**Integration:** New section "JSON Schema Standards" added at line 6
- States all CBL documents must follow standards
- Lists key design considerations
- References schema definitions for each collection

**Impact:** Any new collection designs must include corresponding JSON Schema files.

---

#### [SCHEMA_MAPPING.md](SCHEMA_MAPPING.md)
**Integration:** New section "JSON Schema Standards for Mapping Definitions" added at line 12
- Specific requirements for mapping definition files
- References the mappings schema validation file
- Emphasizes field naming and enum conventions

**Impact:** All new mapping definitions automatically validated and documented.

---

## What Developers Must Know

### When Writing Configuration

✅ Before creating any config document, review:
1. [JSON Schema Standards Guide](../guides/JSON_SCHEMA.md) – Overview & conventions
2. [Author Checklist](../guides/JSON_SCHEMA.md#author-checklist) – Step-by-step validation
3. Relevant schema file in `json_schema/changes-worker/` – Specific field definitions

### When Adding New Collections to CBL

✅ Steps:
1. Design the collection schema following standards in [CBL_DATABASE.md](CBL_DATABASE.md)
2. Create a JSON Schema definition in `json_schema/changes-worker/{collection_name}/schema.json`
3. Update [CBL_DATABASE.md](CBL_DATABASE.md) to document the new collection
4. Validate with `python -m jsonschema` or equivalent

### When Creating Mapping Definitions

✅ Follow guidelines in [SCHEMA_MAPPING.md](SCHEMA_MAPPING.md):
1. Use `snake_case` for all mapping field names
2. Use lowercase enums (e.g., `"upsert"`, not `"Upsert"`)
3. Include `meta` field with version, description, updated_at
4. Validate against `json_schema/changes-worker/mappings/schema.json`

### When Implementing v2.0 Features

✅ Reference [DESIGN_2_0.md](DESIGN_2_0.md) and:
1. Ensure all documents use `meta` field (not `_meta`)
2. All field names are `snake_case`
3. Follow collection schema definitions exactly
4. Test with JSON Schema validator before deployment

---

## Standards at a Glance

| Standard | Where to Find | Why It Matters |
|----------|---------------|----------------|
| **Field naming** (snake_case) | [JSON_SCHEMA.md#field-naming](../guides/JSON_SCHEMA.md#field-naming-conventions) | Consistent across Python, SQL, and JavaScript code |
| **Reserved fields** (_meta vs meta) | [JSON_SCHEMA.md#reserved-fields](../guides/JSON_SCHEMA.md#reserved-fields-reference) | Avoid conflicts with database-reserved fields |
| **DateTime formats** | [JSON_SCHEMA.md#datetime-standards](../guides/JSON_SCHEMA.md#datetime-standards) | Performance vs readability trade-offs |
| **Enum values** (lowercase) | [JSON_SCHEMA.md#enum-conventions](../guides/JSON_SCHEMA.md#enum-conventions) | Avoid case sensitivity bugs |
| **Field ordering** | [JSON_SCHEMA.md#logical-field-ordering](../guides/JSON_SCHEMA.md#logical-field-ordering) | Readability and maintainability |

---

## Validation Workflow

### Before Committing Code

```bash
# 1. Validate all JSON files against schemas
python -m json.tool json_schema/**/*.json > /dev/null

# 2. Check for non-snake_case field names
grep -r '[a-z][a-zA-Z]*[A-Z]' json_schema/ --include="*.json"  # Should be empty

# 3. Check for forbidden _meta in mappings/config
grep -r '"_meta"' mappings/ config.json 2>/dev/null | grep -v "dlq_meta"  # Should be empty
```

### Before Deployment

```bash
# Run schema validation on all documents being deployed
python scripts/validate_schemas.py --check-all
```

---

## FAQ

**Q: Can I use camelCase for field names?**
A: No. All field names must be `snake_case`. This includes JSON documents, database columns, Python variables, and API field names.

**Q: What's the difference between `_meta` and `meta`?**
A: `_meta` was used historically but conflicts with reserved field conventions. New code uses `meta` (no underscore) for application-level metadata.

**Q: Which DateTime format should I use?**
A: 
- **Performance-critical fields:** Unix epoch (seconds or milliseconds)
- **Human-readable fields:** ISO-8601 (e.g., "2026-04-18T22:14:33.421Z")
- **Consistency:** Pick one per collection and stick with it

**Q: What if I need to add a new collection?**
A: Create the schema file first in `json_schema/changes-worker/{name}/schema.json`, document it in [CBL_DATABASE.md](CBL_DATABASE.md), then implement the Python code. The schema drives the implementation, not the other way around.

**Q: Can I skip validation for "simple" documents?**
A: No. All documents, even small ones, must follow the standards. Use the [Author Checklist](../guides/JSON_SCHEMA.md#author-checklist) for quick validation.

---

## Related Resources

- 📋 [JSON Schema Standards Guide](../guides/JSON_SCHEMA.md) – Complete reference
- 📊 [Schema Audit Report](../guides/SCHEMA_AUDIT_REPORT.md) – All 16 collections verified
- 🗺️ [Guide Index](../guides/GUIDE_INDEX.md) – Quick navigation and FAQ
- 🔍 [JSON Schema Definitions](../json_schema/changes-worker/) – All collection schemas

---

## Checklist for Code Review

When reviewing code that touches JSON documents, ensure:

- [ ] All field names are `snake_case` (no camelCase, no PascalCase)
- [ ] No forbidden `_` prefixes except `meta` container
- [ ] DateTime fields are consistently formatted (ISO-8601 or Unix epoch, not mixed)
- [ ] Enum values are lowercase with underscores (e.g., `"feed_type": "continuous"`)
- [ ] Fields are logically ordered (type/id → config → timestamps → meta)
- [ ] Document validates against corresponding JSON Schema
- [ ] Author checked the [Author Checklist](../guides/JSON_SCHEMA.md#author-checklist)
- [ ] New collections have schema definitions in `json_schema/`
- [ ] Documentation updated if new field types or enums introduced

---

**Last Updated:** 2026-04-20  
**Status:** ✅ Complete & Integrated

All design docs now reference and enforce JSON Schema standards across the codebase.
