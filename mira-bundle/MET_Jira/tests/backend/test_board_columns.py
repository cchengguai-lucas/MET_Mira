"""Board columns follow the project's statuses."""

from backend.services.workflow import ensure_status_columns


def test_each_status_gets_a_column_and_a_shared_status_is_split_out():
    columns = [
        {"name": "待评估", "status_ids": [1], "wip": 3},
        {"name": "完成", "status_ids": [3, 4], "wip": None},
        {"name": "备忘", "status_ids": [], "wip": 1},
    ]
    statuses = [(1, "待评估"), (2, "方案讨论"), (3, "已完成"), (4, "已驳回")]

    result = ensure_status_columns(columns, statuses)

    assert result == [
        {"name": "待评估", "status_ids": [1], "wip": 3},
        {"name": "方案讨论", "status_ids": [2], "wip": None},
        {"name": "完成", "status_ids": [3], "wip": None},
        {"name": "已驳回", "status_ids": [4], "wip": None},
        {"name": "备忘", "status_ids": [], "wip": 1},
    ]


def test_new_status_joins_both_boards_without_restoring_a_removed_column(admin):
    boards = admin.get("/api/projects/ELEC/boards").json()["boards"]
    kanban = next(board for board in boards if board["kind"] == "kanban")
    scrum = next(board for board in boards if board["kind"] == "scrum")
    kept = kanban["columns"][:-1]
    kept[0]["wip"] = 2
    removed = kanban["columns"][-1]["status_ids"][0]
    assert admin.put(f"/api/boards/{kanban['id']}", json={"columns": kept}).status_code == 200

    created = admin.post("/api/projects/ELEC/statuses", json={
        "name": "联调", "category": "doing", "color": "#0a84ff",
    })
    assert created.status_code == 200, created.text
    status_id = created.json()["id"]

    kanban_columns = admin.get(f"/api/boards/{kanban['id']}").json()["board"]["columns"]
    assert kanban_columns[-1] == {"name": "联调", "status_ids": [status_id], "wip": None}
    assert kanban_columns[0]["wip"] == 2
    assert all(removed not in column["status_ids"] for column in kanban_columns)

    scrum_columns = admin.get(f"/api/boards/{scrum['id']}").json()["board"]["columns"]
    assert scrum_columns[-1]["name"] == "联调"
    assert scrum_columns[-1]["status_ids"] == [status_id]

    renamed = admin.put(f"/api/projects/ELEC/statuses/{status_id}", json={
        "name": "联调中", "category": "doing", "color": "#0a84ff",
    })
    assert renamed.status_code == 200, renamed.text
    columns = admin.get(f"/api/boards/{kanban['id']}").json()["board"]["columns"]
    assert columns[-1]["name"] == "联调中"

    assert admin.delete(f"/api/projects/ELEC/statuses/{status_id}").status_code == 200
    names = [column["name"] for column in
             admin.get(f"/api/boards/{kanban['id']}").json()["board"]["columns"]]
    assert "联调中" not in names
    assert admin.get(f"/api/boards/{kanban['id']}").json()["board"]["columns"][0]["wip"] == 2
