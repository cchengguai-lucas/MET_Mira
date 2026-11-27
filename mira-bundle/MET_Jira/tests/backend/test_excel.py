from __future__ import annotations

import io
from datetime import date

from openpyxl import Workbook, load_workbook


def _legacy_sheet() -> bytes:
    """Mimic the existing requirement sheet: section row, header row, merged machine groups."""
    wb = Workbook()
    ws = wb.active
    ws.append(["需求提出人填写", None, None, None, None, None, "电气组填写", None, None, None,
               None, "测试人员填写", None, None, None])
    ws.append(["序号", "需求提出人", "项目名称", "机台（可以自己添加）", "机台（可以自己添加）",
               "需求说明", "更新涉及（可多选）", "是否涉及软件", "验证方法", "验证标准",
               "当前状态", "更新日期", "测试人员", "是否通过", "备注"])
    ws.append([1, "外部同事", "XX 项目", "CMB-1", "PM-A", "PM-A 温控优化\n详细说明",
               "1.硬件、2.软件", "是", "上机", "温度稳定", "2. 研发中", date(2026, 9, 1),
               None, "通过", "无"])
    ws.append([2, None, "XX 项目", None, "LL", "LL 门阀互锁", "工作流程", "否", None, None,
               "测试中", None, None, None, None])
    ws.append([3, None, "YY 项目", "NEW-9", "TM", "新机台需求", "其他", None, None, None, None,
               None, None, None, None])
    ws.merge_cells("D3:D4")
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_import_preview_commit_undo(admin):
    files = {"file": ("legacy.xlsx", _legacy_sheet(),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    preview = admin.post("/api/projects/ELEC/import/preview", files=files)
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data["header_row"] == 2 and data["total"] == 3
    guesses = {c["index"]: c["guess"] for c in data["columns"]}
    assert guesses[4] == "machine_group" and guesses[5] == "machine"
    assert guesses[6] == "description" and guesses[11] == "status"
    assert "未匹配的机台组" in data["warnings"]

    commit = admin.post("/api/projects/ELEC/import/commit", json={
        "token": data["token"], "mapping": data["mapping"], "create_missing_machines": True})
    assert commit.status_code == 200, commit.text
    created = commit.json()["created"]
    assert len(created) == 3
    repeated = admin.post("/api/projects/ELEC/import/commit", json={
        "token": data["token"], "mapping": data["mapping"], "create_missing_machines": True})
    assert repeated.status_code == 200
    assert repeated.json() == commit.json()
    first = admin.get(f"/api/issues/{created[0]['key']}").json()
    assert first["title"] == "PM-A 温控优化"
    assert first["status"]["name"] == "研发中"
    assert first["update_scope"] == ["硬件", "软件"]
    assert first["involves_software"] is True and first["test_passed"] is True
    assert "_software_test_wait" not in (first["extra"] or {})
    assert first["software_test_wait"] is None
    assert "原需求提出人：外部同事" in first["remark"]
    second = admin.get(f"/api/issues/{created[1]['key']}").json()
    assert second["machines"][0]["group"] == "CMB-1" and second["machines"][0]["name"] == "LL"
    third = admin.get(f"/api/issues/{created[2]['key']}").json()
    assert third["machines"][0]["group"] == "NEW-9"

    exported = admin.get("/api/projects/ELEC/export.xlsx")
    assert exported.status_code == 200
    wb = load_workbook(io.BytesIO(exported.content))
    ws = wb.active
    assert ws.cell(row=1, column=1).value == "需求提出人填写"
    assert ws.cell(row=2, column=7).value == "需求说明"
    assert ws.max_row == 5
    key_cell = ws.cell(row=3, column=2)
    assert key_cell.value == created[0]["key"]
    assert key_cell.hyperlink is not None
    assert key_cell.hyperlink.target.endswith(f"/issues/{created[0]['key']}")
    assert key_cell.font.underline == "single"

    batches = admin.get("/api/projects/ELEC/imports").json()["imports"]
    undo = admin.post(f"/api/imports/{batches[0]['id']}/undo")
    assert undo.json()["deleted"] == 3
    assert admin.get(f"/api/issues/{created[0]['key']}").status_code == 404
