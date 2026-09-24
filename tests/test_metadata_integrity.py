from __future__ import annotations

import re
from typing import Any

from cofferdam_app.metadata_integrity import MetadataIntegrityAuditor


class FakeDatabase:
    def __init__(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        self.tables = tables
        self.commits = 0

    def table_exists(self, doctype: str) -> bool:
        return doctype in self.tables

    def sql(self, query: str, *, as_dict: bool) -> list[dict[str, Any]]:
        table_match = re.search(r"`tab([^`]+)`", query)
        assert table_match
        doctype = table_match.group(1)
        rows = self.tables[doctype]

        if query.startswith("SHOW COLUMNS"):
            columns = set().union(*(row.keys() for row in rows)) if rows else set()
            return [{"Field": column} for column in columns]

        selected = re.findall(r"`([^`]+)`", query)
        return [{column: row.get(column) for column in selected} for row in rows]

    def commit(self) -> None:
        self.commits += 1


class FakeFrappe:
    def __init__(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        self.db = FakeDatabase(tables)
        self.deleted: list[tuple[str, str]] = []
        self.cache_cleared = False

    def delete_doc(self, doctype: str, name: str, **kwargs: object) -> None:
        self.deleted.append((doctype, name))

    def clear_cache(self) -> None:
        self.cache_cleared = True


def test_check_accepts_select_dynamic_link_driver_and_finds_actual_gaps() -> None:
    frappe = FakeFrappe(
        {
            "DocType": [
                {"name": "Customer"},
                {"name": "Purchase Receipt"},
                {"name": "Example"},
            ],
            "Accounting Dimension": [
                {
                    "name": "Legacy Dimension",
                    "document_type": "Removed DocType",
                }
            ],
            "DocField": [
                {
                    "name": "example-reference-type",
                    "parent": "Example",
                    "parenttype": "DocType",
                    "fieldname": "reference_type",
                    "fieldtype": "Select",
                    "options": "Customer\nURL",
                },
                {
                    "name": "example-reference-name",
                    "parent": "Example",
                    "parenttype": "DocType",
                    "fieldname": "reference_name",
                    "fieldtype": "Dynamic Link",
                    "options": "reference_type",
                },
                {
                    "name": "example-broken-link",
                    "parent": "Example",
                    "parenttype": "DocType",
                    "fieldname": "broken_link",
                    "fieldtype": "Link",
                    "options": "Removed DocType",
                },
            ],
            "Custom Field": [
                {
                    "name": "Example-legacy_link",
                    "dt": "Example",
                    "fieldname": "legacy_link",
                    "fieldtype": "Link",
                    "options": "Removed DocType",
                }
            ],
            "Property Setter": [
                {
                    "name": "Purchase Receipt-old_field-hidden",
                    "doc_type": "Purchase Receipt",
                    "field_name": "old_field",
                    "property": "hidden",
                    "value": "1",
                }
            ],
        }
    )

    findings = MetadataIntegrityAuditor(frappe).check()

    assert {(item.source_doctype, item.source_name) for item in findings} == {
        ("Accounting Dimension", "Legacy Dimension"),
        ("DocField", "example-broken-link"),
        ("Custom Field", "Example-legacy_link"),
        ("Property Setter", "Purchase Receipt-old_field-hidden"),
    }


def test_fix_is_dry_run_by_default_and_requires_explicit_deletion_flags() -> None:
    frappe = FakeFrappe(
        {
            "DocType": [{"name": "Example"}],
            "Accounting Dimension": [
                {
                    "name": "Legacy Dimension",
                    "document_type": "Removed DocType",
                }
            ],
            "DocField": [],
            "Custom Field": [
                {
                    "name": "Example-legacy_link",
                    "dt": "Example",
                    "fieldname": "legacy_link",
                    "fieldtype": "Link",
                    "options": "Removed DocType",
                }
            ],
            "Property Setter": [
                {
                    "name": "Example-old_field-hidden",
                    "doc_type": "Example",
                    "field_name": "old_field",
                    "property": "hidden",
                    "value": "1",
                }
            ],
        }
    )
    auditor = MetadataIntegrityAuditor(frappe)

    dry_run = auditor.fix(
        delete_custom_fields=True,
        delete_property_setters=True,
    )
    assert {repair.action for repair in dry_run} == {"would_delete"}
    assert frappe.deleted == []

    repairs = auditor.fix(
        dry_run=False,
        delete_custom_fields=True,
        delete_property_setters=True,
        commit=True,
    )
    assert {repair.action for repair in repairs} == {"deleted"}
    assert frappe.deleted == [
        ("Accounting Dimension", "Legacy Dimension"),
        ("Custom Field", "Example-legacy_link"),
        ("Property Setter", "Example-old_field-hidden"),
    ]
    assert frappe.cache_cleared is True
    assert frappe.db.commits == 1
