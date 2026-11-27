"""Product knowledge base for the AI assistant: Mira features, rules and definitions."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

KB_PATH = Path(__file__).resolve().parent.parent / "knowledge" / "mira_help.md"
_TOKEN = re.compile(r"[a-z_][a-z0-9_]*|\d+")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_STOP = {
    "怎么", "什么", "为什", "如何", "哪里", "在哪", "哪些", "哪个", "可以", "能不", "不能", "一下",
    "我的", "我们", "你们", "请问", "是不", "有没", "没有", "这个", "那个", "时候", "多少", "需要",
    "是什", "么是", "么样", "怎样", "意思", "么意", "么用", "有什", "现在", "目前", "一个", "帮我",
    "吗", "呢", "的", "了", "是", "我", "你", "在", "有",
    "how", "what", "why", "where", "when", "who", "which", "is", "are", "do", "does", "can",
    "the", "a", "an", "to", "of", "in", "on", "for", "and", "or", "it", "my", "i", "me", "be",
}
_EN_ALIASES = {
    "import": "导入", "export": "导出", "excel": "Excel", "board": "看板", "kanban": "看板",
    "sprint": "冲刺", "backlog": "待办事项", "workflow": "工作流", "transition": "流转",
    "status": "状态", "permission": "权限", "role": "角色", "admin": "管理员", "approval": "审批",
    "approve": "审批", "gate": "门禁", "template": "模板", "master": "主数据", "material": "物料",
    "report": "报表", "dashboard": "我的工作", "filter": "筛选", "search": "筛选",
    "notification": "通知", "email": "邮件", "mail": "邮件", "reminder": "提醒", "test": "测试",
    "testing": "测试", "tester": "测试人", "health": "健康度", "cycle": "周期时间",
    "lead": "前置时间", "forecast": "预测", "velocity": "速度", "burndown": "燃尽图",
    "epic": "史诗", "subtask": "子任务", "story": "故事", "bug": "缺陷", "task": "任务",
    "requirement": "需求", "assignee": "负责人", "reporter": "报告人", "priority": "优先级",
    "delete": "删除", "create": "创建", "attachment": "附件", "screenshot": "截图",
    "image": "截图", "shortcut": "快捷键", "shortcuts": "快捷键", "language": "语言",
    "english": "英文", "theme": "主题", "dark": "深色模式", "avatar": "头像", "password": "密码",
    "login": "登录", "org": "组织架构", "organization": "组织架构", "member": "成员",
    "members": "成员", "machine": "机台", "project": "项目", "archive": "归档", "comment": "评论",
    "mention": "提及", "watch": "关注", "link": "关联", "wip": "在制品上限", "swimlane": "泳道",
    "migration": "迁移", "migrate": "迁移", "field": "字段", "fields": "字段",
    "software": "软件负责人",
    "assistant": "AI 助手", "summary": "AI 总结", "draft": "AI 起草", "triage": "智能分派",
    "duplicate": "查重", "mobile": "移动端", "ldap": "AD 同步", "sync": "同步", "user": "用户",
    "users": "用户", "list": "列表视图", "faq": "常见问题",
}


@dataclass(frozen=True)
class Section:
    title: str
    body: str
    keywords: str

    @property
    def text(self) -> str:
        return f"## {self.title}\n{self.body}".strip()


def _terms(text: str) -> set[str]:
    """Latin words (and their ``_`` parts) plus CJK character bigrams."""
    lowered = (text or "").lower()
    terms: set[str] = set()
    for token in _TOKEN.findall(lowered):
        terms.add(token)
        terms.update(part for part in token.split("_") if len(part) > 1)
    for run in _CJK_RUN.findall(lowered):
        if len(run) == 1:
            terms.add(run)
        terms.update(run[i:i + 2] for i in range(len(run) - 1))
    return terms


def _query_terms(query: str) -> set[str]:
    words = set(_TOKEN.findall((query or "").lower()))
    expanded = " ".join([query or "", *(_EN_ALIASES[w] for w in words if w in _EN_ALIASES)])
    return _terms(expanded) - _STOP


def _idf(docs: list[set[str]]) -> dict[str, float]:
    df: dict[str, int] = {}
    for terms in docs:
        for term in terms:
            df[term] = df.get(term, 0) + 1
    return {term: math.log(1 + len(docs) / count) for term, count in df.items()}


@lru_cache(maxsize=1)
def _index() -> tuple[list[Section], list[tuple[set[str], set[str]]],
                      dict[str, float], dict[str, float]]:
    raw = KB_PATH.read_text(encoding="utf-8")
    sections: list[Section] = []
    for chunk in re.split(r"^## ", raw, flags=re.M)[1:]:
        title, _, body = chunk.partition("\n")
        keywords = ""
        lines = []
        for line in body.strip().splitlines():
            if line.startswith("关键词：") and not keywords:
                keywords = line.removeprefix("关键词：")
            else:
                lines.append(line)
        sections.append(Section(title.strip(), "\n".join(lines).strip(), keywords))
    terms = [(_terms(s.title + " " + s.keywords), _terms(s.body)) for s in sections]
    head_idf = _idf([head for head, _ in terms])
    body_idf = _idf([head | body for head, body in terms])
    return sections, terms, head_idf, body_idf


def sections() -> list[Section]:
    return _index()[0]


def topics() -> list[str]:
    return [s.title for s in sections()]


def search(query: str, limit: int = 3) -> list[tuple[Section, float]]:
    """Rank sections by IDF-weighted term overlap; title/keyword hits weigh triple and use
    their own (sparser) document frequencies."""
    docs, terms, head_idf, body_idf = _index()
    wanted = _query_terms(query)
    if not wanted:
        return []
    exact = (query or "").strip()
    scored = []
    for section, (head, body) in zip(docs, terms, strict=True):
        score = sum(3.0 * head_idf[t] if t in head else body_idf[t]
                    for t in wanted if t in head or t in body)
        if exact and exact == section.title:
            score += 100.0
        if score > 0:
            scored.append((section, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:limit]


def lookup(query: str, limit: int = 3, budget: int = 6000) -> list[Section]:
    """Top sections within a character budget; weak tails relative to the best match are dropped."""
    ranked = search(query, limit)
    if not ranked:
        return []
    best = ranked[0][1]
    chosen: list[Section] = []
    used = 0
    for section, score in ranked:
        if chosen and score < best * 0.35:
            break
        if chosen and used + len(section.text) > budget:
            break
        chosen.append(section)
        used += len(section.text)
    return chosen


def context_for(question: str, min_score: float = 9.0, limit: int = 3,
                budget: int = 5000) -> str:
    """Reference text to prepend for ``question``, or "" when nothing matches well."""
    ranked = search(question, 1)
    if not ranked or ranked[0][1] < min_score:
        return ""
    return "\n\n".join(s.text for s in lookup(question, limit, budget))
