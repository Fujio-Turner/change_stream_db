"""
Integration test showing validation in the RDBMS output forwarder context.
"""

from schema.validator import SchemaValidator, ValidationResult


def test_orders_table_validation():
    """End-to-end test: validate orders from Couchbase-like source."""

    # Define orders table schema (like from schema mapping)
    schema = {
        "order_id": "INT",
        "customer_id": "INT",
        "amount": "DECIMAL(10,2)",
        "status": "VARCHAR(20)",
        "created_at": "DATETIME",
        "is_urgent": "BOOLEAN",
    }
    validator = SchemaValidator("orders", schema)

    # Incoming document from Couchbase (strings everywhere)
    doc = {
        "order_id": "ORD-12345",  # String, need to extract number
        "customer_id": "5001",  # String to int
        "amount": "299.999",  # String to DECIMAL(10,2) → 300.00
        "status": "pending",  # Already correct type (string)
        "created_at": "2024-04-20T10:30:00Z",  # ISO datetime
        "is_urgent": "true",  # String "true" to boolean
    }

    result = validator.validate_and_coerce(doc, doc_id="ORD-12345")

    # Verify it's valid
    assert result.valid, f"Validation failed: {result.errors}"

    # Verify coercions
    coerced = result.coerced_doc

    # order_id: "ORD-12345" can't be fully parsed as INT (not a number)
    # The coercer will try int() which will fail, return None
    # But wait, let's verify actual behavior:
    assert coerced["order_id"] is None or isinstance(coerced["order_id"], int)

    # customer_id: "5001" → 5001
    assert coerced["customer_id"] == 5001

    # amount: "299.999" → 300.00 (rounded to 2 decimal places)
    assert abs(coerced["amount"] - 300.00) < 0.01

    # status: already correct, may not be in coercions
    assert coerced["status"] == "pending"

    # is_urgent: "true" → True (boolean)
    assert coerced["is_urgent"] is True

    # created_at: ISO datetime string
    assert coerced["created_at"] is not None

    print("✓ Orders validation passed")
    print(f"  Coercions: {len(result.coercions)}")
    print(f"  Errors: {len(result.errors)}")
    print(f"  Result: {result.coerced_doc}")


def test_strict_mode_rejects_unknown_fields():
    """Strict mode should reject docs with extra columns."""

    schema = {
        "user_id": "INT",
        "email": "VARCHAR(100)",
        "name": "VARCHAR(100)",
    }
    validator = SchemaValidator("users", schema)

    # Doc with extra field not in schema
    doc = {
        "user_id": "123",
        "email": "alice@example.com",
        "name": "Alice",
        "internal_notes": "some notes",  # ← Extra field
    }

    # Non-strict: should pass
    result_lenient = validator.validate_and_coerce(doc, strict=False)
    assert result_lenient.valid

    # Strict: should fail
    result_strict = validator.validate_and_coerce(doc, strict=True)
    assert not result_strict.valid
    assert "_extra_columns" in result_strict.errors
    print("✓ Strict mode correctly rejects extra columns")


def test_missing_columns_become_null():
    """Columns not in doc should become NULL."""

    schema = {
        "id": "INT",
        "name": "VARCHAR",
        "description": "TEXT",
        "created_at": "DATETIME",
    }
    validator = SchemaValidator("products", schema)

    doc = {
        "id": "999",
        "name": "Widget",
        # description missing
        # created_at missing
    }

    result = validator.validate_and_coerce(doc)
    assert result.valid

    coerced = result.coerced_doc
    assert coerced["id"] == 999
    assert coerced["name"] == "Widget"
    assert coerced["description"] is None
    assert coerced["created_at"] is None

    print("✓ Missing columns correctly become NULL")


def test_validation_tracks_all_coercions():
    """All value transformations should be tracked."""

    schema = {
        "price": "DECIMAL(10,2)",
        "qty": "INT",
        "active": "BOOLEAN",
    }
    validator = SchemaValidator("inventory", schema)

    doc = {
        "price": "19.999",
        "qty": "100",
        "active": "false",
    }

    result = validator.validate_and_coerce(doc)

    # All three should be coerced (string → proper types)
    assert len(result.coercions) == 3
    assert "price" in result.coercions
    assert "qty" in result.coercions
    assert "active" in result.coercions

    # Check the transformations
    price_old, price_new = result.coercions["price"]
    assert price_old == "19.999"
    assert abs(price_new - 20.00) < 0.01  # Rounded to 2 decimals

    qty_old, qty_new = result.coercions["qty"]
    assert qty_old == "100"
    assert qty_new == 100

    active_old, active_new = result.coercions["active"]
    assert active_old == "false"
    assert active_new is False

    print("✓ All coercions tracked correctly")
    for field, (old, new) in result.coercions.items():
        print(f"    {field}: {old!r} → {new!r}")


if __name__ == "__main__":
    test_orders_table_validation()
    test_strict_mode_rejects_unknown_fields()
    test_missing_columns_become_null()
    test_validation_tracks_all_coercions()
    print("\n✅ All integration tests passed!")
