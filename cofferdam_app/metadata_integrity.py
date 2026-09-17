"""Audit and cautiously repair dangling Frappe metadata references.

Frappe validates most metadata while it is saved, but a removed app, fixture,
or DocType can leave records that refer to metadata no longer on the site.
This module detects those structured references without building Meta for the
potentially broken records.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, ClassVar, cast


@dataclass(frozen=True)
class MetadataFinding:
    """One structured metadata reference that cannot be resolved."""

    source_doctype: str
    source_name: str | None
    owner_doctype: str | None
    fieldname: str | None
    fieldtype: str | None
    target: str | None
    reason: str


@dataclass(frozen=True)
class MetadataRepair:
    """The disposition of one finding during a repair run."""

    finding: MetadataFinding
    action: str
    detail: str


class MetadataIntegrityAuditor:
    """Check and optionally repair dangling, structured Frappe metadata.

    ``check()`` is read-only.  ``fix()`` is dry-run by default and never
    changes standard ``DocField`` records or business configuration such as a
    Workflow or Report.  Deletion of a Custom Field or Property Setter requires
    an explicit opt-in flag.
    """

    _REFERENCE_FIELDTYPES: ClassVar[frozenset[str]] = frozenset(
        {"Link", "Table", "Table MultiSelect"}
    )
    _DOCUMENT_REFERENCE_SOURCES: ClassVar[dict[str, tuple[str, ...]]] = {
        "Assignment Rule": ("document_type",),
        "Auto Email Report": ("reference_doctype",),
        "Client Script": ("dt",),
        "Dashboard Chart": ("document_type",),
        "Kanban Board": ("reference_doctype",),
        "Notification": ("document_type",),
        "Print Format": ("doc_type",),
        "Report": ("ref_doctype",),
        "Server Script": ("reference_doctype",),
        "Web Form": ("doc_type",),
        "Webhook": ("webhook_doctype", "document_type"),
        "Workflow": ("document_type",),
    }

    def __init__(self, frappe_api: Any | None = None) -> None:
        if frappe_api is None:
            import frappe

            frappe_api = frappe
        self.frappe = frappe_api

    def check(self) -> list[MetadataFinding]:
        """Return all detected gaps, sorted for stable review and testing."""
        findings: list[MetadataFinding] = []
        known_doctypes = {
            row["name"] for row in self._rows("DocType", ["name"]) if row.get("name")
        }

        standard_fields = self._rows(
            "DocField",
            ["name", "parent", "parenttype", "fieldname", "fieldtype", "options"],
        )
        custom_fields = self._rows(
            "Custom Field",
            ["name", "dt", "fieldname", "fieldtype", "options"],
        )

        fields_by_doctype: dict[str, dict[str, dict[str, Any]]] = {}
        field_records: list[tuple[str, str | None, dict[str, Any]]] = []

        for field in standard_fields:
            if field.get("parenttype") != "DocType":
                continue
            owner = self._text(field.get("parent"))
            fieldname = self._text(field.get("fieldname"))
            if owner and fieldname:
                fields_by_doctype.setdefault(owner, {})[fieldname] = field
            field_records.append(("DocField", owner, field))

        for field in custom_fields:
            owner = self._text(field.get("dt"))
            fieldname = self._text(field.get("fieldname"))
            if owner and fieldname:
                fields_by_doctype.setdefault(owner, {})[fieldname] = field
            field_records.append(("Custom Field", owner, field))

        for source_doctype, record_owner, field in field_records:
            owner = record_owner or ""
            source_name = self._text(field.get("name"))
            fieldname = self._text(field.get("fieldname"))
            fieldtype = self._text(field.get("fieldtype"))
            options = self._text(field.get("options"))

            if not owner:
                findings.append(
                    MetadataFinding(
                        source_doctype,
                        source_name,
                        None,
                        fieldname,
                        fieldtype,
                        None,
                        "field has no owning DocType",
                    )
                )
                continue

            if owner not in known_doctypes:
                findings.append(
                    MetadataFinding(
                        source_doctype,
                        source_name,
                        owner,
                        fieldname,
                        fieldtype,
                        owner,
                        "field is attached to a missing DocType",
                    )
                )
                continue

            if (
                fieldtype in self._REFERENCE_FIELDTYPES
                and options
                and options not in known_doctypes
            ):
                findings.append(
                    MetadataFinding(
                        source_doctype,
                        source_name,
                        owner,
                        fieldname,
                        fieldtype,
                        options,
                        "field options names a missing DocType",
                    )
                )

            # Dynamic Link options names a driver field.  Valid drivers include
            # Select fields, whose values can be URLs or conditionally supplied
            # DocTypes; only a missing driver is a reliable integrity gap.
            if fieldtype == "Dynamic Link" and options:
                if options not in fields_by_doctype.get(owner, {}):
                    findings.append(
                        MetadataFinding(
                            source_doctype,
                            source_name,
                            owner,
                            fieldname,
                            fieldtype,
                            options,
                            "Dynamic Link driver field does not exist",
                        )
                    )

        self._check_document_references(known_doctypes, findings)
        self._check_property_setters(known_doctypes, fields_by_doctype, findings)

        return sorted(
            findings,
            key=lambda finding: (
                finding.source_doctype,
                finding.owner_doctype or "",
                finding.source_name or "",
                finding.fieldname or "",
                finding.reason,
            ),
        )

    def fix(
        self,
        findings: Iterable[MetadataFinding] | None = None,
        *,
        dry_run: bool = True,
        delete_custom_fields: bool = False,
        delete_property_setters: bool = False,
        commit: bool = False,
    ) -> list[MetadataRepair]:
        """Apply explicitly authorised, narrowly safe repairs.

        Only dangling Custom Fields and Property Setters are eligible.  Every
        other finding is reported as manual because deleting it could discard
        a Workflow, Report, or standard framework metadata.
        """
        selected = list(self.check() if findings is None else findings)
        repairs: list[MetadataRepair] = []
        deleted: set[tuple[str, str]] = set()

        for finding in selected:
            key = (finding.source_doctype, finding.source_name or "")

            auto_delete = (
                finding.source_doctype == "Custom Field" and delete_custom_fields
            ) or (
                finding.source_doctype == "Property Setter" and delete_property_setters
            )
            if not auto_delete:
                repairs.append(
                    MetadataRepair(
                        finding,
                        "manual_review",
                        "No automatic deletion is authorised for this metadata type.",
                    )
                )
                continue

            if not finding.source_name:
                repairs.append(
                    MetadataRepair(finding, "manual_review", "Record has no name."))
                continue

            if key in deleted:
                repairs.append(
                    MetadataRepair(finding, "already_handled", "Record was already selected."))
                continue

            if dry_run:
                repairs.append(
                    MetadataRepair(
                        finding,
                        "would_delete",
                        f"Would delete {finding.source_doctype} {finding.source_name!r}.",
                    )
                )
                deleted.add(key)
                continue

            self.frappe.delete_doc(
                finding.source_doctype,
                finding.source_name,
                force=True,
                ignore_permissions=True,
            )
            deleted.add(key)
            repairs.append(
                MetadataRepair(
                    finding,
                    "deleted",
                    f"Deleted {finding.source_doctype} {finding.source_name!r}.",
                )
            )

        if deleted and not dry_run:
            self.frappe.clear_cache()
            if commit:
                self.frappe.db.commit()

        return repairs

    def _check_document_references(
        self,
        known_doctypes: set[str],
        findings: list[MetadataFinding],
    ) -> None:
        for source_doctype, candidate_columns in self._DOCUMENT_REFERENCE_SOURCES.items():
            for record in self._rows(source_doctype, ["name", *candidate_columns]):
                target_column = next(
                    (
                        column
                        for column in candidate_columns
                        if self._text(record.get(column))
                    ),
                    None,
                )
                if not target_column:
                    continue
                target = self._text(record.get(target_column))
                if target not in known_doctypes:
                    findings.append(
                        MetadataFinding(
                            source_doctype,
                            self._text(record.get("name")),
                            None,
                            target_column,
                            None,
                            target,
                            f"{target_column} names a missing DocType",
                        )
                    )

    def _check_property_setters(
        self,
        known_doctypes: set[str],
        fields_by_doctype: dict[str, dict[str, dict[str, Any]]],
        findings: list[MetadataFinding],
    ) -> None:
        for setter in self._rows(
            "Property Setter",
            ["name", "doc_type", "field_name", "property", "value"],
        ):
            owner = self._text(setter.get("doc_type"))
            fieldname = self._text(setter.get("field_name"))
            name = self._text(setter.get("name"))

            if owner and owner not in known_doctypes:
                findings.append(
                    MetadataFinding(
                        "Property Setter",
                        name,
                        owner,
                        fieldname,
                        None,
                        owner,
                        "Property Setter is attached to a missing DocType",
                    )
                )
            elif owner and fieldname and fieldname not in fields_by_doctype.get(owner, {}):
                findings.append(
                    MetadataFinding(
                        "Property Setter",
                        name,
                        owner,
                        fieldname,
                        None,
                        fieldname,
                        "Property Setter names a field that no longer exists",
                    )
                )

    def _rows(self, doctype: str, columns: list[str]) -> list[dict[str, Any]]:
        """Read rows without calling Frappe's potentially broken Meta loader."""
        if not self.frappe.db.table_exists(doctype):
            return []

        table = f"`tab{doctype}`"
        available = {
            row["Field"]
            for row in self.frappe.db.sql(f"SHOW COLUMNS FROM {table}", as_dict=True)
        }
        selected = [column for column in columns if column in available]
        if not selected:
            return []

        selected_sql = ", ".join(f"`{column}`" for column in selected)
        # `table` and `selected_sql` derive only from module-owned constants.
        result = self.frappe.db.sql(
            f"SELECT {selected_sql} FROM {table}",  # noqa: S608
            as_dict=True,
        )
        return cast(list[dict[str, Any]], result)

    @staticmethod
    def _text(value: object) -> str:
        return str(value).strip() if value is not None else ""


def audit_metadata_integrity(
    *,
    fix: bool = False,
    dry_run: bool = True,
    delete_custom_fields: bool = False,
    delete_property_setters: bool = False,
    commit: bool = False,
) -> list[MetadataFinding] | list[MetadataRepair]:
    """Convenience Frappe-console entry point for check or explicit repair."""
    auditor = MetadataIntegrityAuditor()
    if not fix:
        return auditor.check()
    return auditor.fix(
        dry_run=dry_run,
        delete_custom_fields=delete_custom_fields,
        delete_property_setters=delete_property_setters,
        commit=commit,
    )
