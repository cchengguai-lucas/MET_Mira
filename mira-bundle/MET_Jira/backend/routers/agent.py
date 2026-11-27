from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db, session_factory, utcnow
from ..deps import current_user, current_user_id, get_issue, get_project
from ..metrics import ai_sse_stream
from ..models import (
    AgentAction,
    AgentConversation,
    AgentFeedback,
    AgentMemory,
    AgentMessage,
    OrgUnit,
    Project,
    User,
)
from ..serialize import iso
from ..services import agent, jql_assist, llm, polish, ratelimit, workflow_assist
from ..services.agent_tools import AgentContext, execute_action
from ..services.issues import IssueError, create_issue

router = APIRouter(prefix="/api/agent", tags=["agent"])


class ChatIn(BaseModel):
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=24)
    project: str | None = None
    conversation_id: int | None = None
    client_message_id: str | None = Field(default=None, max_length=64)
    ui_context: dict[str, Any] = Field(default_factory=dict)


class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=1000)
    project: str | None = None


class FeedbackIn(BaseModel):
    message_id: int
    rating: Literal[-1, 1]
    reason: str = Field(default="", max_length=64)
    comment: str = Field(default="", max_length=1000)


class DraftIn(BaseModel):
    project: str
    text: str = Field(min_length=2, max_length=8000)
    split: bool = False


class DraftCommitIn(BaseModel):
    project: str
    drafts: list[dict[str, Any]]


class TriageIn(BaseModel):
    project: str
    title: str
    description: str = ""
    machine_ids: list[int] = []
    update_scope: list[str] = []
    exclude_key: str | None = None


class JqlAssistIn(BaseModel):
    messages: list[dict[str, Any]]
    current_jql: str = ""


class WorkflowDraftIn(BaseModel):
    project: str
    issue_type: str
    transitions: dict[str, list[str]] = Field(default_factory=dict)
    messages: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    mode: Literal["instruction", "optimize"] = "instruction"
    suggestion_ids: list[str] = Field(default_factory=list, max_length=40)


class PolishIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    title: str = Field("", max_length=500)
    kind: Literal["description", "scheme", "delivery"] = "description"


class ReportIn(BaseModel):
    project: str
    period: Literal["daily", "weekly"] = "weekly"
    org_unit_id: int | None = None


def _sse(events: Iterator[dict[str, Any]]) -> StreamingResponse:
    def gen() -> Iterator[str]:
        with ai_sse_stream():
            try:
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except (llm.LlmError, IssueError, HTTPException) as error:
                message = getattr(error, "detail", None) or getattr(error, "message", None) or str(
                    error
                )
                payload = json.dumps({"type": "error", "message": str(message)}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
            finally:
                close = getattr(events, "close", None)
                if close is not None:
                    close()

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _guarded_sse(user_id: int, events: Iterator[dict[str, Any]]) -> StreamingResponse:
    """Stream ``events`` while holding one of the user's AI slots. The request quota is checked
    up front (plain 429); the slot is taken once streaming starts so a client that never reads
    the body cannot leak it."""
    ratelimit.take_ai_request(user_id)

    def guarded() -> Iterator[dict[str, Any]]:
        try:
            with ratelimit.held_ai_slot(user_id):
                yield from events
        except HTTPException as error:
            yield {"type": "error", "message": error.detail}

    return _sse(guarded())


def _text_events(pieces: Iterator[str]) -> Iterator[dict[str, Any]]:
    for piece in pieces:
        yield {"type": "delta", "text": piece}
    yield {"type": "done"}


def action_out(action: AgentAction) -> dict[str, Any]:
    return {
        "id": action.id,
        "tool": action.tool,
        "args": action.args,
        "summary": action.summary,
        "status": action.status,
        "result": action.result,
        "created_at": iso(action.created_at),
    }


@router.get("/status")
def status(force: bool = False, _user: User = Depends(current_user)) -> dict:
    return llm.probe(force=force)


@router.post("/chat")
def chat(body: ChatIn, user_id: int = Depends(current_user_id)) -> StreamingResponse:
    def events() -> Iterator[dict[str, Any]]:
        probe = llm.probe()
        if not probe.get("reachable"):
            yield {"type": "error", "message": f"LLM 服务不可用：{probe.get('error', '')}"}
            return
        with session_factory()() as db:
            me = db.get(User, user_id)
            assert me is not None
            project = get_project(db, body.project, me) if body.project else None
            conversation = (
                db.get(AgentConversation, body.conversation_id)
                if body.conversation_id
                else None
            )
            if body.conversation_id and (
                conversation is None or conversation.user_id != user_id
            ):
                raise HTTPException(404, "对话不存在")
            if conversation is None:
                conversation = AgentConversation(
                    user_id=user_id,
                    project_id=project.id if project else None,
                    scope="project" if project else "global",
                )
                db.add(conversation)
                db.flush()
            elif project and conversation.project_id not in {None, project.id}:
                raise HTTPException(409, "该对话属于另一个项目")
            project_key = project.key if project else (
                db.scalar(select(Project.key).where(Project.id == conversation.project_id))
                if conversation.project_id else None
            )
            current = next(
                (
                    str(message.get("content") or "").strip()
                    for message in reversed(body.messages)
                    if message.get("role") == "user"
                ),
                "",
            )
            if not current:
                raise HTTPException(422, "问题不能为空")
            if len(current) > 8000:
                raise HTTPException(422, "单条问题不能超过 8000 个字符")
            existing = None
            if body.client_message_id:
                existing = db.scalar(
                    select(AgentMessage).where(
                        AgentMessage.conversation_id == conversation.id,
                        AgentMessage.client_message_id == body.client_message_id,
                    )
                )
            if existing is not None:
                replay = db.scalar(
                    select(AgentMessage)
                    .where(
                        AgentMessage.conversation_id == conversation.id,
                        AgentMessage.id > existing.id,
                        AgentMessage.role == "assistant",
                    )
                    .order_by(AgentMessage.id)
                )
                if replay is not None:
                    yield {"type": "delta", "text": replay.content}
                    yield {"type": "conversation", "id": conversation.id}
                    yield {"type": "message", "id": replay.id}
                    yield {"type": "done"}
                    return
            else:
                db.add(
                    AgentMessage(
                        conversation_id=conversation.id,
                        role="user",
                        content=current,
                        client_message_id=body.client_message_id,
                        token_count=max(1, len(current) // 4),
                    )
                )
                conversation.title = conversation.title or current[:120]
                conversation.updated_at = utcnow()
                db.commit()
            stored = list(
                db.scalars(
                    select(AgentMessage)
                    .where(AgentMessage.conversation_id == conversation.id)
                    .order_by(AgentMessage.id.desc())
                    .limit(20)
                )
            )
            history = [
                {"role": message.role, "content": message.content}
                for message in reversed(stored)
                if message.role in {"user", "assistant"}
            ]
            snapshot = agent.prepare_chat(
                db,
                me,
                history,
                project_key,
                native=probe.get("tool_mode") == "native",
                ui_context=body.ui_context,
            )
            conversation_id = conversation.id
        answer = ""
        status = "complete"
        for event in agent.run_chat_snapshot(snapshot):
            if event.get("type") == "delta":
                answer += str(event.get("text") or "")
            if event.get("type") == "error":
                status = "error"
            if event.get("type") != "done":
                yield event
        with session_factory()() as db:
            conversation = db.get(AgentConversation, conversation_id)
            if conversation is not None:
                message = AgentMessage(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=answer,
                    status=status,
                    token_count=max(1, len(answer) // 4) if answer else 0,
                )
                db.add(message)
                conversation.updated_at = utcnow()
                recent = list(
                    db.scalars(
                        select(AgentMessage)
                        .where(AgentMessage.conversation_id == conversation_id)
                        .order_by(AgentMessage.id.desc())
                        .limit(8)
                    )
                )
                conversation.summary = "\n".join(
                    f"{row.role}: {row.content[:500]}" for row in reversed(recent)
                )[-4000:]
                db.commit()
                yield {"type": "conversation", "id": conversation_id}
                yield {"type": "message", "id": message.id}
        yield {"type": "done"}

    return _guarded_sse(user_id, events())


@router.get("/conversations")
def list_conversations(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    rows = db.scalars(
        select(AgentConversation)
        .where(AgentConversation.user_id == user.id)
        .order_by(AgentConversation.updated_at.desc())
        .limit(50)
    )
    return {
        "conversations": [
            {
                "id": row.id,
                "scope": row.scope,
                "project_id": row.project_id,
                "title": row.title,
                "summary": row.summary,
                "updated_at": iso(row.updated_at),
            }
            for row in rows
        ]
    }


@router.get("/memories")
def list_memories(
    project: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    project_id = get_project(db, project, user).id if project else None
    stmt = select(AgentMemory).where(
        AgentMemory.user_id == user.id,
        AgentMemory.active.is_(True),
    )
    if project_id:
        stmt = stmt.where(
            (AgentMemory.project_id.is_(None)) | (AgentMemory.project_id == project_id)
        )
    rows = db.scalars(stmt.order_by(AgentMemory.id.desc()))
    return {
        "memories": [
            {
                "id": row.id,
                "scope": row.scope,
                "project_id": row.project_id,
                "content": row.content,
                "confirmed_by_user": row.confirmed_by_user,
                "created_at": iso(row.created_at),
            }
            for row in rows
        ]
    }


@router.post("/memories")
def create_memory(
    body: MemoryIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    project = get_project(db, body.project, user) if body.project else None
    memory = AgentMemory(
        user_id=user.id,
        project_id=project.id if project else None,
        scope="project" if project else "global",
        content=body.content.strip(),
        confirmed_by_user=True,
    )
    db.add(memory)
    db.commit()
    db.refresh(memory)
    return {"id": memory.id, "content": memory.content, "scope": memory.scope}


@router.delete("/memories/{memory_id}")
def delete_memory(
    memory_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    memory = db.get(AgentMemory, memory_id)
    if memory is None or memory.user_id != user.id:
        raise HTTPException(404, "记忆不存在")
    memory.active = False
    db.commit()
    return {"ok": True}


@router.post("/feedback")
def submit_feedback(
    body: FeedbackIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    message = db.get(AgentMessage, body.message_id)
    conversation = (
        db.get(AgentConversation, message.conversation_id) if message else None
    )
    if message is None or conversation is None or conversation.user_id != user.id:
        raise HTTPException(404, "消息不存在")
    feedback = AgentFeedback(
        user_id=user.id,
        message_id=message.id,
        rating=body.rating,
        reason=body.reason,
        comment=body.comment,
    )
    db.add(feedback)
    db.commit()
    return {"ok": True}


@router.get("/actions")
def list_actions(state: str = "pending", user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(AgentAction).where(AgentAction.user_id == user.id, AgentAction.status == state)
        .order_by(AgentAction.id.desc()).limit(50)
    )
    return {"actions": [action_out(a) for a in rows]}


def _action(db: Session, action_id: int, user: User) -> AgentAction:
    action = db.get(AgentAction, action_id)
    if action is None or action.user_id != user.id:
        raise HTTPException(404, "操作不存在")
    return action


@router.post("/actions/{action_id}/confirm")
def confirm(action_id: int, _body: dict[str, Any] | None = None,
            user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    action = _action(db, action_id, user)
    # Confirm executes the frozen pending args; clients cannot rewrite them here.
    try:
        result = execute_action(AgentContext(db, user), action)
    except IssueError as error:
        db.rollback()
        action = _action(db, action_id, user)
        action.status = "failed"
        action.result = {"error": error.message}
        action.decided_at = utcnow()
        db.commit()
        raise
    return {"action": action_out(action), "result": result}


@router.post("/actions/{action_id}/reject")
def reject(action_id: int, user: User = Depends(current_user),
           db: Session = Depends(get_db)) -> dict:
    action = _action(db, action_id, user)
    if action.status == "pending":
        action.status = "rejected"
        action.decided_at = utcnow()
        db.commit()
    return {"action": action_out(action)}


@router.post("/draft")
def draft(body: DraftIn, user: User = Depends(current_user),
          db: Session = Depends(get_db)) -> dict:
    project = get_project(db, body.project, user, need="member")
    try:
        with ratelimit.ai_slot(user.id):
            return agent.draft_issues(db, user, project, body.text, split=body.split)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except llm.LlmError as error:
        raise HTTPException(503, f"LLM 服务不可用：{error}") from error


def _draft_fields(item: dict[str, Any]) -> dict[str, Any]:
    allowed = {"type", "title", "description", "priority", "machine_ids", "update_scope",
               "project_name", "involves_software", "verify_method", "verify_criteria", "labels",
               "assignee_id", "tester_id", "due_date", "custom_fields"}
    data = {k: v for k, v in item.items() if k in allowed and v not in (None, "")}
    data.pop("status_id", None)  # drafts always start at the type's initial status
    if isinstance(data.get("due_date"), str):
        from datetime import date

        data["due_date"] = date.fromisoformat(data["due_date"][:10])
    return data


@router.post("/draft/commit")
def draft_commit(body: DraftCommitIn, user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> dict:
    project = get_project(db, body.project, user, need="member")
    # Bound the blast radius of a single commit (UI already confirms the draft list).
    if len(body.drafts) > 50:
        raise HTTPException(400, "单次提交不能超过 50 个草稿")
    created: list[dict[str, str]] = []
    with ratelimit.ai_slot(user.id):
        for item in body.drafts:
            if item.get("assignee_username") and not item.get("assignee_id"):
                assignee = db.scalar(select(User).where(User.username == item["assignee_username"]))
                item["assignee_id"] = assignee.id if assignee else None
            parent = create_issue(db, project, user, _draft_fields(item))
            created.append({"key": parent.key, "title": parent.title})
            for child in item.get("children") or []:
                fields = _draft_fields(child)
                if parent.type == "epic":
                    fields["epic_id"] = parent.id
                    if fields.get("type") == "subtask":
                        fields["type"] = "task"
                else:
                    fields["type"] = "subtask"
                    fields["parent_id"] = parent.id
                child_issue = create_issue(db, project, user, fields)
                created.append({"key": child_issue.key, "title": child_issue.title})
    db.commit()
    return {"created": created}


@router.post("/triage")
def triage(body: TriageIn, user: User = Depends(current_user),
           db: Session = Depends(get_db)) -> dict:
    project = get_project(db, body.project, user)
    exclude = get_issue(db, body.exclude_key, user).id if body.exclude_key else None
    return agent.triage(db, user, project, body.title, body.description, body.machine_ids,
                        body.update_scope, exclude)


@router.post("/workflow-draft")
def workflow_draft(body: WorkflowDraftIn, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> dict:
    """Preview a workflow edit for the canvas. Nothing is written."""
    project = get_project(db, body.project, user, need="admin")
    try:
        if body.mode == "optimize":
            return workflow_assist.optimize(
                db, user, project, body.issue_type, body.transitions, body.suggestion_ids,
            )
        with ratelimit.ai_slot(user.id):
            return workflow_assist.assist(
                db, user, project, body.issue_type, body.transitions, body.messages,
            )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except llm.LlmError as error:
        raise HTTPException(503, f"LLM 服务不可用：{error}") from error


@router.post("/jql")
def jql_assist_endpoint(body: JqlAssistIn, user: User = Depends(current_user),
                        db: Session = Depends(get_db)) -> dict:
    try:
        with ratelimit.ai_slot(user.id):
            return jql_assist.assist(db, user, body.messages, body.current_jql)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except llm.LlmError as error:
        raise HTTPException(503, f"LLM 服务不可用：{error}") from error


@router.post("/suggest-subtasks/{key}")
def suggest_subtasks(key: str, user: User = Depends(current_user),
                     db: Session = Depends(get_db)) -> dict:
    issue = get_issue(db, key, user)
    try:
        with ratelimit.ai_slot(user.id):
            return agent.suggest_subtasks(db, user, issue)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except llm.LlmError as error:
        raise HTTPException(503, f"LLM 服务不可用：{error}") from error


@router.post("/suggest-related/{key}")
def suggest_related(key: str, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict:
    """AI analysis of which issues are truly related (not just lexically similar)."""
    issue = get_issue(db, key, user)
    try:
        with ratelimit.ai_slot(user.id):
            return agent.suggest_related(db, user, issue)
    except llm.LlmError as error:
        raise HTTPException(503, f"LLM 服务不可用：{error}") from error


@router.post("/summarize/{key}")
def summarize(key: str, user_id: int = Depends(current_user_id)) -> StreamingResponse:
    def events() -> Iterator[dict[str, Any]]:
        with session_factory()() as db:
            me = db.get(User, user_id)
            assert me is not None
            issue = get_issue(db, key, me)
            issue_key, messages = agent.prepare_summary(db, issue)
        yield from _text_events(agent.summarize_snapshot_stream(user_id, issue_key, messages))

    return _guarded_sse(user_id, events())


@router.post("/polish")
def polish_description(body: PolishIn,
                       user_id: int = Depends(current_user_id)) -> StreamingResponse:
    def events() -> Iterator[dict[str, Any]]:
        yield from _text_events(polish.polish_snapshot_stream(
            user_id, body.text, body.title, kind=body.kind,
        ))

    return _guarded_sse(user_id, events())


@router.post("/report")
def report(body: ReportIn, user_id: int = Depends(current_user_id)) -> StreamingResponse:
    def events() -> Iterator[dict[str, Any]]:
        with session_factory()() as db:
            me = db.get(User, user_id)
            assert me is not None
            project = get_project(db, body.project, me)
            unit = db.get(OrgUnit, body.org_unit_id) if body.org_unit_id else None
            snapshot = agent.prepare_report(db, me, project, body.period, unit)
        saved: list[int] = []
        for piece in agent.report_snapshot_stream(snapshot, on_saved=saved.append):
            yield {"type": "delta", "text": piece}
        if saved:
            yield {"type": "report", "id": saved[0]}
        yield {"type": "done"}

    return _guarded_sse(user_id, events())
