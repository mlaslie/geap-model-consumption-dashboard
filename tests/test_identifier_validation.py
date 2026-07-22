"""
Tests for the SQL identifier validation helper in backend.bq_client.

_validate_identifier must reject strings that could be used for SQL injection
and accept normal alphanumeric identifiers.
"""
import pytest
from backend.bq_client import _validate_identifier


class TestValidateIdentifierAccepts:
    """Values that _validate_identifier should accept without raising."""

    def test_simple_word(self):
        _validate_identifier("myproject", "test")

    def test_alphanumeric(self):
        _validate_identifier("dataset123", "test")

    def test_with_underscore(self):
        _validate_identifier("my_dataset", "test")

    def test_with_hyphen(self):
        _validate_identifier("my-dataset", "test")

    def test_with_dot(self):
        _validate_identifier("my.view", "test")

    def test_mixed_case(self):
        _validate_identifier("MyProject01", "test")

    def test_project_with_hyphens(self):
        _validate_identifier("my-gcp-project-123", "test")

    def test_all_allowed_chars(self):
        _validate_identifier("Az09._-", "test")


class TestValidateIdentifierRejects:
    """Values that _validate_identifier should raise ValueError for."""

    def test_space_in_middle(self):
        with pytest.raises(ValueError):
            _validate_identifier("my project", "test")

    def test_leading_space(self):
        with pytest.raises(ValueError):
            _validate_identifier(" myproject", "test")

    def test_backtick(self):
        with pytest.raises(ValueError):
            _validate_identifier("my`table", "test")

    def test_semicolon(self):
        with pytest.raises(ValueError):
            _validate_identifier("foo;DROP TABLE users", "test")

    def test_single_quote(self):
        with pytest.raises(ValueError):
            _validate_identifier("foo'bar", "test")

    def test_double_quote(self):
        with pytest.raises(ValueError):
            _validate_identifier('foo"bar', "test")

    def test_parenthesis(self):
        with pytest.raises(ValueError):
            _validate_identifier("foo(bar)", "test")

    def test_slash(self):
        with pytest.raises(ValueError):
            _validate_identifier("foo/bar", "test")

    def test_asterisk(self):
        with pytest.raises(ValueError):
            _validate_identifier("foo*", "test")

    def test_empty_string(self):
        with pytest.raises(ValueError):
            _validate_identifier("", "test")

    def test_newline(self):
        with pytest.raises(ValueError):
            _validate_identifier("foo\nbar", "test")

    def test_tab(self):
        with pytest.raises(ValueError):
            _validate_identifier("foo\tbar", "test")
