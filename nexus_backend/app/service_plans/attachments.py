"""Standalone monthly originals: collecting a bank statement creates no journal facts."""

import csv
import io
import re
import posixpath
from pathlib import PurePosixPath
from urllib.parse import quote
from zipfile import BadZipFile, ZipFile
from xml.etree.ElementTree import ParseError

from defusedxml.common import DefusedXmlException

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response

from ..accounting.domain import AccountingError
from ..accounting.service import now
from ..business.storage import MAX_FILE_BYTES, validate_attachment


METADATA = ("id", "filename", "media_type", "sha256", "size_bytes", "created_by", "created_at")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def validate_material_original(filename, content_type, data):
    name = re.sub(
        r"[\x00-\x1f\x7f]", "", (filename or "").replace("\\", "/").split("/")[-1]
    ).strip()
    if not name or len(name) > 150:
        raise AccountingError("附件名称须为 1–150 个字符", 422)
    if not data or len(data) > MAX_FILE_BYTES:
        raise AccountingError("资料不能为空，单个原件最多 10 MB", 413)
    suffix = PurePosixPath(name).suffix.lower()
    claimed = (content_type or "").split(";", 1)[0].strip().lower()
    if suffix == ".csv":
        if (
            claimed
            not in {
                "",
                "text/csv",
                "text/plain",
                "application/csv",
                "application/vnd.ms-excel",
                "application/octet-stream",
            }
            or b"\x00" in data
        ):
            raise AccountingError("CSV 格式与实际内容不符", 422)
        text = None
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise AccountingError("CSV 请使用 UTF-8 或 GB18030 编码", 422)
        try:
            rows = csv.reader(io.StringIO(text), strict=True)
            first = next((row for row in rows if any(cell.strip() for cell in row)), None)
            if not first or not 2 <= len(first) <= 200:
                raise ValueError("表头需包含 2–200 列")
            for count, row in enumerate(rows, 1):
                if count > 100000 or len(row) > 200:
                    raise ValueError("资料表格超出范围")
        except (csv.Error, ValueError):
            raise AccountingError("CSV 结构无效，请导出为标准逗号分隔表格", 422) from None
        return name, "text/csv"
    if suffix == ".xlsx":
        if claimed not in {"", XLSX, "application/octet-stream"}:
            raise AccountingError("XLSX 声明格式与实际内容不符", 422)
        try:
            with ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                paths = {entry.filename for entry in entries}
                if len(paths) != len(entries):
                    raise ValueError("工作簿包含重复内容路径")
                if (
                    not 1 <= len(entries) <= 2000
                    or sum(entry.file_size for entry in entries) > 30 * 1024 * 1024
                ):
                    raise ValueError("工作簿展开后过大")
                if (
                    not {"[Content_Types].xml", "xl/workbook.xml", "xl/_rels/workbook.xml.rels"}
                    <= paths
                ):
                    raise ValueError("不是标准工作簿")
                for entry in entries:
                    path = PurePosixPath(entry.filename)
                    if (
                        path.is_absolute()
                        or ".." in path.parts
                        or "\\" in entry.filename
                        or entry.flag_bits & 1
                    ):
                        raise ValueError("工作簿路径或加密格式无效")
                    if "vba" in entry.filename.lower() or entry.filename.lower().endswith(
                        (".exe", ".dll")
                    ):
                        raise ValueError("不接受宏或可执行内容")
                from defusedxml.ElementTree import fromstring, iterparse

                content_types = archive.read("[Content_Types].xml")
                workbook = archive.read("xl/workbook.xml")
                if len(content_types) > 1024 * 1024 or len(workbook) > 2 * 1024 * 1024:
                    raise ValueError("工作簿结构过大")
                types = fromstring(content_types)
                document = fromstring(workbook)
                type_ns = "{http://schemas.openxmlformats.org/package/2006/content-types}"
                sheet_ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
                relation_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
                relationship_id = (
                    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                )
                if types.tag != type_ns + "Types" or document.tag != sheet_ns + "workbook":
                    raise ValueError("不是标准工作簿结构")
                overrides = {
                    item.get("PartName"): item.get("ContentType")
                    for item in types.findall(type_ns + "Override")
                }
                if (
                    overrides.get("/xl/workbook.xml")
                    != "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
                ):
                    raise ValueError("缺少工作簿类型")
                relations = fromstring(archive.read("xl/_rels/workbook.xml.rels"))
                if relations.tag != relation_ns + "Relationships":
                    raise ValueError("工作表关系无效")
                targets = {
                    item.get("Id"): item for item in relations.findall(relation_ns + "Relationship")
                }
                sheets = document.findall(sheet_ns + "sheets/" + sheet_ns + "sheet")
                if not 1 <= len(sheets) <= 500:
                    raise ValueError("工作表数量无效")
                for sheet in sheets:
                    relation = targets.get(sheet.get(relationship_id))
                    if (
                        relation is None
                        or relation.get("TargetMode") == "External"
                        or not relation.get("Type", "").endswith("/worksheet")
                    ):
                        raise ValueError("工作表关系缺失")
                    target = relation.get("Target", "")
                    part = posixpath.normpath(
                        target.lstrip("/") if target.startswith("/") else "xl/" + target
                    )
                    if not part.startswith("xl/") or part not in paths:
                        raise ValueError("工作表内容缺失")
                    if (
                        overrides.get("/" + part)
                        != "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
                    ):
                        raise ValueError("工作表类型无效")
                    with archive.open(part) as stream:
                        root_seen = False
                        for event, element in iterparse(stream, events=("start", "end")):
                            if not root_seen:
                                if element.tag != sheet_ns + "worksheet":
                                    raise ValueError("工作表 XML 无效")
                                root_seen = True
                            if event == "end":
                                element.clear()
                if b"macroenabled" in content_types.lower() or archive.testzip() is not None:
                    raise ValueError("工作簿损坏或含宏")
        except (
            BadZipFile,
            KeyError,
            ValueError,
            OSError,
            RuntimeError,
            EOFError,
            DefusedXmlException,
            ParseError,
        ):
            # No entry is extracted or executed. Avoid reflecting parser data.
            raise AccountingError("XLSX 内容无效、损坏或含不支持的宏／加密内容", 422) from None
        return name, XLSX
    return validate_attachment(name, content_type, data)


class MaterialAttachmentService:
    def __init__(self, plans, store):
        self.plans, self.db, self.store = plans, plans.db, store

    def _uploadable(self, conn, actor, request_id, expected_version):
        row = self.plans._request(conn, actor, request_id, submit=True)
        actions = self.plans._actions(conn, request_id)
        current = actions[-1] if actions else None
        if (current["version"] if current else 1) != expected_version:
            raise AccountingError("资料请求已更新，请刷新后上传", 409)
        if current and current["state"] not in {"pending", "returned"}:
            raise AccountingError("已提交资料须先由独立人员退回，才能追加原件", 409)
        return row

    def require_upload(self, actor, request_id, expected_version):
        with self.db.transaction(actor=actor) as conn:
            self._uploadable(conn, actor, request_id, expected_version)

    def _company_of(self, actor, request_id):
        with self.db.transaction(actor=actor) as conn:
            row = conn.execute(
                "SELECT company_id FROM material_requests WHERE id=? AND firm_id=?",
                (request_id, actor.firm_id),
            ).fetchone()
        if not row:
            raise AccountingError("资料请求不存在或无权访问", 404)
        return row["company_id"]

    def add(self, actor, request_id, expected_version, stored, conn=None):
        if conn is None:
            company_id = self._company_of(actor, request_id)
            with self.db.transaction(
                write=True, actor=actor, company_id=company_id, write_scope="company"
            ) as transaction:
                return self.add(actor, request_id, expected_version, stored, transaction)
        if stored.get("_upload_replay"):
            self.plans._request(conn, actor, request_id)
            previous = conn.execute("SELECT * FROM material_attachments WHERE id=? AND request_id=?", (stored["id"], request_id)).fetchone()
            return {"attachment": {key: previous[key] for key in METADATA}, "version": expected_version, "duplicate": True}
        row = self._uploadable(conn, actor, request_id, expected_version)
        previous = conn.execute(
            "SELECT * FROM material_attachments WHERE request_id=? AND sha256=? AND filename=? AND media_type=?",
            (request_id, stored["sha256"], stored["filename"], stored["media_type"]),
        ).fetchone()
        if previous:
            return {
                "attachment": {key: previous[key] for key in METADATA},
                "version": expected_version,
                "duplicate": True,
            }
        limits = conn.execute(
            "SELECT COUNT(*) AS count,COALESCE(SUM(size_bytes),0) AS bytes FROM material_attachments WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if limits["count"] >= 10 or limits["bytes"] + stored["size_bytes"] > 50 * 1024 * 1024:
            raise AccountingError("每项资料最多 10 个原件，合计最多 50 MB", 413)
        stamp = now()
        conn.execute(
            "INSERT INTO material_attachments(id,request_id,book_id,firm_id,company_id,storage_key,filename,media_type,sha256,size_bytes,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                stored["id"],
                request_id,
                row["book_id"],
                actor.firm_id,
                row["company_id"],
                stored["storage_key"],
                stored["filename"],
                stored["media_type"],
                stored["sha256"],
                stored["size_bytes"],
                actor.user_id,
                stamp,
            ),
        )
        # Native append trigger writes the audit in this same transaction.
        # Retain before COMMIT, including uncertain commit acknowledgements.
        self.store.retain(stored)
        result = {**stored, "created_by": actor.user_id, "created_at": stamp}
        return {
            "attachment": {key: result[key] for key in METADATA},
            "version": expected_version,
            "duplicate": False,
        }

    def _rows(self, conn, actor, row, ids=None):
        self.plans._request(conn, actor, row["id"])
        own = row["assignee_user_id"] == actor.user_id and bool(
            self.plans.access.company_role(conn, actor, row["company_id"])
        )
        if not own:
            import json

            submission = next(
                (
                    action
                    for action in reversed(self.plans._actions(conn, row["id"]))
                    if action["action"] == "submit"
                ),
                None,
            )
            allowed = json.loads(submission["attachment_ids_json"]) if submission else []
            ids = (
                allowed
                if ids is None
                else [identifier for identifier in ids if identifier in allowed]
            )
            if not ids:
                return []
        sql, params = "SELECT * FROM material_attachments WHERE request_id=?", [row["id"]]
        if ids is not None:
            if not ids:
                return []
            sql += " AND id IN (" + ",".join("?" for _ in ids) + ")"
            params.extend(ids)
        return conn.execute(sql + " ORDER BY created_at,id", params).fetchall()

    def list(self, conn, actor, row, latest_submission_attachment_ids=None):
        return [
            {key: item[key] for key in METADATA}
            for item in self._rows(conn, actor, row, latest_submission_attachment_ids)
        ]

    def evidence(self, conn, actor, row, ids):
        found = {item["id"]: item for item in self._rows(conn, actor, row, ids)}
        if len(found) != len(ids) or len(ids) != len(set(ids)):
            raise AccountingError("资料原件已失权或不属于当前请求，请重新核对", 409)
        evidence = []
        for identifier in ids:
            item = found[identifier]
            evidence.append({key: item[key] for key in METADATA})
        return evidence

    def verify(self, actor, request_id, ids, *, review=False):
        """Check immutable blobs without holding a connection or write lock.

        The subsequent write resolves permissions, period, action version and
        these same IDs/hashes again. A storage outage cannot lock other clients.
        """
        with self.db.transaction(actor=actor) as conn:
            row = self.plans._request(conn, actor, request_id, submit=not review, review=review)
            found = {item["id"]: dict(item) for item in self._rows(conn, actor, row, ids)}
            if len(found) != len(ids) or len(ids) != len(set(ids)):
                raise AccountingError("资料原件已失权或不属于当前请求，请重新核对", 409)
            metadata = [found[identifier] for identifier in ids]
        for item in metadata:
            self.store.read(item)
        return [{key: item[key] for key in METADATA} for item in metadata]

    def content(self, actor, request_id, attachment_id):
        with self.db.transaction(actor=actor) as conn:
            row = self.plans._request(conn, actor, request_id)
            # Resolve the last actual submission for staff, never accept an
            # arbitrary identifier as a capability to an unsubmitted original.
            found = next(
                (item for item in self._rows(conn, actor, row) if item["id"] == attachment_id), None
            )
            if not found:
                raise AccountingError("资料原件不存在或无权下载", 404)
            metadata = dict(found)
        return metadata, self.store.read(metadata)


def build_material_attachments_router(actor_dependency):
    router = APIRouter(prefix="/api/material-requests", tags=["周期资料原件"])

    def service(request: Request):
        return request.app.state.service_plans.attachments

    @router.post("/{request_id}/attachments")
    def upload(
        request_id: str,
        request: Request,
        expected_version: int = Query(..., ge=1),
        file: UploadFile | None = File(default=None),
        upload_id: str | None = Form(default=None),
        actor=Depends(actor_dependency),
        attachments=Depends(service),
    ):
        claimed = bool(upload_id)
        uploads = request.app.state.uploads if claimed else None
        stored = None
        try:
            if claimed:
                return uploads.consume(actor, upload_id, "material_attachment", request_id,
                    lambda conn, metadata: attachments.add(actor, request_id, expected_version, metadata, conn=conn))
            attachments.require_upload(actor, request_id, expected_version)
            if file is None:
                raise AccountingError("请选择文件，或先完成大文件上传校验", 422)
            data = file.file.read(MAX_FILE_BYTES + 1)
            name, detected = validate_material_original(file.filename, file.content_type, data)
            stored = attachments.store.put_original(name, detected, data)
            result = attachments.add(actor, request_id, expected_version, stored)
            if result["duplicate"]:
                attachments.store.discard(stored)
            return result
        except AccountingError:
            if claimed:
                raise
            if stored:
                attachments.store.discard(stored)
            raise
        finally:
            if file is not None:
                file.file.close()

    @router.get("/{request_id}/attachments/{attachment_id}/content")
    def download(
        request_id: str,
        attachment_id: str,
        actor=Depends(actor_dependency),
        attachments=Depends(service),
    ):
        metadata, data = attachments.content(actor, request_id, attachment_id)
        return Response(
            content=data,
            media_type=metadata["media_type"],
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(metadata["filename"], safe=""),
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Content-SHA256": metadata["sha256"],
            },
        )

    return router
