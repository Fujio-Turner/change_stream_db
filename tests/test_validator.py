"""
Tests for schema.validator module
"""

import pytest
from schema.validator import (
    SchemaValidator,
    ValidationResult,
    coerce_value,
    parse_sql_type,
)


class TestParseSQLType:
    def test_simple_int(self):
        base, meta = parse_sql_type("INT")
        assert base == "INT"
        assert meta == {}

    def test_varchar_with_length(self):
        base, meta = parse_sql_type("VARCHAR(255)")
        assert base == "VARCHAR"
        assert meta == {"max_length": 255}

    def test_decimal_with_precision(self):
        base, meta = parse_sql_type("DECIMAL(10,2)")
        assert base == "DECIMAL"
        assert meta == {"precision": 10, "scale": 2}


class TestCoerceValue:
    def test_coerce_int(self):
        assert coerce_value("42", "INT") == 42
        assert coerce_value(42.5, "INT") == 42
        assert coerce_value(True, "INT") == 1

    def test_coerce_varchar(self):
        assert coerce_value(123, "VARCHAR") == "123"
        assert coerce_value("hello", "VARCHAR(10)") == "hello"
        assert coerce_value("hello world!", "VARCHAR(5)") == "hello"

    def test_coerce_decimal(self):
        val = coerce_value("10.567", "DECIMAL(10,2)")
        assert abs(val - 10.57) < 0.01

    def test_coerce_boolean(self):
        assert coerce_value(True, "BOOLEAN") is True
        assert coerce_value(1, "BOOLEAN") is True
        assert coerce_value("true", "BOOLEAN") is True
        assert coerce_value("false", "BOOLEAN") is False

    def test_coerce_none(self):
        assert coerce_value(None, "INT") is None
        assert coerce_value(None, "VARCHAR") is None


class TestSchemaValidator:
    def test_basic_validation(self):
        schema = {
            "order_id": "INT",
            "amount": "DECIMAL(10,2)",
            "status": "VARCHAR(20)",
        }
        validator = SchemaValidator("orders", schema)

        doc = {
            "order_id": "123",
            "amount": "99.999",
            "status": "pending",
        }

        result = validator.validate_and_coerce(doc)

        assert result.valid
        assert result.coerced_doc["order_id"] == 123
        assert abs(result.coerced_doc["amount"] - 100.00) < 0.01
        assert result.coerced_doc["status"] == "pending"

    def test_coercions_tracked(self):
        schema = {
            "order_id": "INT",
            "amount": "DECIMAL(10,2)",
        }
        validator = SchemaValidator("orders", schema)

        doc = {
            "order_id": "456",
            "amount": "50.555",
        }

        result = validator.validate_and_coerce(doc)

        # Check coercions were tracked
        assert "order_id" in result.coercions
        assert result.coercions["order_id"] == ("456", 456)
        assert "amount" in result.coercions

    def test_missing_columns_nulled(self):
        schema = {
            "id": "INT",
            "name": "VARCHAR",
            "created_at": "DATETIME",
        }
        validator = SchemaValidator("users", schema)

        doc = {"id": "1", "name": "Alice"}

        result = validator.validate_and_coerce(doc)

        assert result.valid
        assert result.coerced_doc["id"] == 1
        assert result.coerced_doc["name"] == "Alice"
        assert result.coerced_doc["created_at"] is None

    def test_strict_mode_rejects_extra_columns(self):
        schema = {
            "id": "INT",
            "name": "VARCHAR",
        }
        validator = SchemaValidator("users", schema)

        doc = {"id": "1", "name": "Bob", "extra_field": "should_error"}

        result = validator.validate_and_coerce(doc, strict=True)

        assert not result.valid
        assert "_extra_columns" in result.errors


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
