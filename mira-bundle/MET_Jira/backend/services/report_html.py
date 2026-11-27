"""HTML rendering of AI reports: key-number tiles, charts and the AI narrative.

The same markup is the email body and the in-app preview, so it only uses what mail clients
render reliably: tables, inline styles and solid colours (no SVG, scripts or external CSS).
Charts are bars drawn with sized table cells.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from html import escape
from typing import Any

from ..models import AiReport, Project
from .alerts import FONT

TEXT = "#1d1d1f"
MUTED = "#6e6e73"
FAINT = "#86868b"
LINE = "#e5e5ea"
PANEL = "#f5f5f7"
BLUE = "#0a84ff"
GREEN = "#30b158"
RED = "#ff3b30"
ORANGE = "#ff9500"

PRIORITIES = {
    "highest": ("最高", "#ff3b30"),
    "high": ("高", "#ff9500"),
    "medium": ("中", "#f5b400"),
    "low": ("低", "#34c759"),
    "lowest": ("最低", "#8e8e93"),
}
KPIS = (
    ("created", "本期新建", BLUE),
    ("resolved", "本期完成", GREEN),
    ("open", "未完成", TEXT),
    ("in_testing", "测试中", "#5e5ce6"),
    ("overdue", "已逾期", RED),
    ("test_failed", "测试未通过", ORANGE),
)
TAG_STYLES = {
    "h3": f"margin:24px 0 8px;font-size:16px;line-height:1.4;color:{TEXT};"
          f"border-left:3px solid {BLUE};padding-left:8px",
    "h4": f"margin:18px 0 6px;font-size:14px;color:{TEXT}",
    "h5": f"margin:14px 0 4px;font-size:13px;color:{MUTED}",
    "p": "margin:6px 0",
    "ul": "margin:6px 0;padding-left:20px",
    "li": "margin:3px 0",
    "table": "border-collapse:collapse;width:100%;margin:8px 0;font-size:13px",
    "th": f"border:1px solid {LINE};padding:6px 8px;background:{PANEL};text-align:left",
    "td": f"border:1px solid {LINE};padding:6px 8px",
    "code": f"background:{PANEL};border-radius:4px;padding:1px 4px;font-size:12px",
    "a": f"color:{BLUE};text-decoration:none",
}

IssueUrl = Callable[[str], str | None]


def _tag(name: str, styled: bool) -> str:
    return f'<{name} style="{TAG_STYLES[name]}">' if styled else f"<{name}>"


def _inline(text: str, styled: bool, issue_url: IssueUrl | None,
            issue_key: re.Pattern[str] | None) -> str:
    out = escape(text, quote=False)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"`([^`]+)`", lambda m: f"{_tag('code', styled)}{m.group(1)}</code>", out)
    link = _tag("a", styled)[:-1]
    out = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
                 lambda m: f'{link} href="{m.group(2)}">{m.group(1)}</a>', out)
    if issue_url is not None and issue_key is not None:
        def linked(match: re.Match[str]) -> str:
            url = issue_url(match.group(0))
            return f'{link} href="{escape(url)}">{match.group(0)}</a>' if url else match.group(0)

        out = issue_key.sub(linked, out)
    return out


def markdown_html(markdown: str, *, styled: bool = False, issue_url: IssueUrl | None = None,
                  project_key: str | None = None) -> str:
    """Small markdown subset (headings, lists, tables, bold, links) for reports and emails.

    ``styled`` inlines presentation styles for mail clients; ``issue_url`` links issue keys of
    ``project_key``."""
    issue_key = (re.compile(rf"(?<![\w/-]){re.escape(project_key)}-\d+\b")
                 if project_key else None)

    def inline(text: str) -> str:
        return _inline(text, styled, issue_url, issue_key)

    html: list[str] = []
    listing = table = False
    header_row = False

    def close() -> None:
        nonlocal listing, table
        if listing:
            html.append("</ul>")
        if table:
            html.append("</table>")
        listing = table = False

    for raw in markdown.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            if not table:
                close()
                html.append(_tag("table", True) if styled else
                            '<table border="1" cellpadding="4" style="border-collapse:collapse">')
                table = header_row = True
            cell = "th" if styled and header_row else "td"
            header_row = False
            html.append("<tr>" + "".join(f"{_tag(cell, styled)}{inline(c)}</{cell}>"
                                         for c in cells) + "</tr>")
            continue
        bullet = re.match(r"^\s*(?:[-*]|\d+\.)\s+(.*)", line)
        if bullet:
            if not listing:
                close()
                html.append(_tag("ul", styled))
                listing = True
            html.append(f"{_tag('li', styled)}{inline(bullet.group(1))}</li>")
            continue
        close()
        heading = re.match(r"^(#{1,6})\s+(.*)", stripped)
        if heading:
            level = min(len(heading.group(1)) + 1, 5)
            html.append(f"{_tag(f'h{level}', styled)}{inline(heading.group(2))}</h{level}>")
        elif stripped:
            html.append(f"{_tag('p', styled)}{inline(stripped)}</p>")
    close()
    return "".join(html)


# ---------------------------------------------------------------- charts


def _card(title: str, body: str, note: str = "") -> str:
    hint = f'<span style="font-size:12px;color:{FAINT};font-weight:400"> · {note}</span>' \
        if note else ""
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:0 0 14px;border:1px solid {LINE};border-radius:12px;'
        f'border-collapse:separate"><tr><td style="padding:14px 16px">'
        f'<div style="font-size:14px;font-weight:600;color:{TEXT};margin-bottom:10px">'
        f"{escape(title)}{hint}</div>{body}</td></tr></table>"
    )


def _kpis(totals: dict[str, int]) -> str:
    cells = []
    for index, (key, label, color) in enumerate(KPIS):
        value = int(totals.get(key) or 0)
        tone = color if value or key in {"created", "resolved", "open"} else FAINT
        column = index % 3
        gap = {0: "0 6px 10px 0", 1: "0 3px 10px", 2: "0 0 10px 6px"}[column]
        cells.append(
            f'<td width="33%" valign="top" style="padding:{gap}">'
            f'<div style="background:{PANEL};border-radius:10px;padding:12px 14px">'
            f'<div style="font-size:26px;line-height:1.1;font-weight:700;color:{tone}">'
            f"{value}</div>"
            f'<div style="font-size:12px;color:{MUTED};margin-top:4px">{label}</div></div></td>'
        )
    rows = "".join(f"<tr>{''.join(cells[i:i + 3])}</tr>" for i in range(0, len(cells), 3))
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="margin-bottom:4px">{rows}</table>')


def _legend(items: list[tuple[str, str]]) -> str:
    swatches = "".join(
        f'<span style="display:inline-block;width:10px;height:10px;border-radius:2px;'
        f'background:{color};vertical-align:middle;margin-right:4px"></span>'
        f'<span style="vertical-align:middle;margin-right:14px">{escape(name)}</span>'
        for name, color in items)
    return f'<div style="margin-top:8px;font-size:12px;color:{MUTED}">{swatches}</div>'


def _column(value: int, peak: int, color: str, height: int = 96) -> str:
    bar = max(round(value / peak * height), 2) if value else 0
    fill = (f'<tr><td width="14" height="{bar}" bgcolor="{color}" style="background:{color};'
            f'border-radius:3px 3px 0 0;font-size:0;line-height:0">&nbsp;</td></tr>'
            if bar else "")
    return (f'<table role="presentation" width="14" cellpadding="0" cellspacing="0"><tr>'
            f'<td align="center" style="font-size:11px;color:{MUTED};padding-bottom:2px">'
            f"{value if value else ''}</td></tr>{fill}</table>")


def _trend(trend: list[dict[str, Any]]) -> str:
    peak = max([max(int(d["created"]), int(d["resolved"])) for d in trend] + [1])
    columns = "".join(
        f'<td valign="bottom" align="center" style="height:118px;padding:0 2px;'
        f'border-bottom:1px solid {LINE}">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" align="center">'
        f'<tr valign="bottom"><td valign="bottom">{_column(int(d["created"]), peak, BLUE)}</td>'
        f'<td width="3"></td>'
        f'<td valign="bottom">{_column(int(d["resolved"]), peak, GREEN)}</td></tr></table></td>'
        for d in trend
    )
    labels = "".join(
        f'<td align="center" style="font-size:11px;color:{FAINT};padding-top:4px">'
        f'{escape(str(d["date"]))}</td>' for d in trend
    )
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            f"<tr>{columns}</tr><tr>{labels}</tr></table>"
            + _legend([("新建", BLUE), ("完成", GREEN)]))


def _bars(rows: list[tuple[str, list[tuple[int, str]]]], label_width: int = 76) -> str:
    """Horizontal (optionally stacked) bars: ``rows`` = [(label, [(value, colour), ...])]."""
    peak = max([sum(v for v, _ in parts) for _, parts in rows] + [1])
    out = []
    for label, parts in rows:
        total = sum(v for v, _ in parts)
        segments = "".join(
            f'<td width="{max(round(v / peak * 82), 1)}%" height="14" bgcolor="{color}" '
            f'style="background:{color};font-size:0;line-height:0">&nbsp;</td>'
            for v, color in parts if v
        )
        out.append(
            f'<tr><td width="{label_width}" style="font-size:12px;color:{MUTED};'
            f'padding:3px 8px 3px 0;white-space:nowrap;overflow:hidden">{escape(label)}</td>'
            f'<td style="padding:3px 0"><table role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0"><tr>{segments}<td style="font-size:12px;color:{TEXT};'
            f'padding-left:6px;white-space:nowrap">{total}</td></tr></table></td></tr>'
        )
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="table-layout:fixed">{"".join(out)}</table>')


def _empty(text: str = "暂无未完成问题") -> str:
    return f'<div style="font-size:12px;color:{FAINT};padding:6px 0">{text}</div>'


def _confidence_color(probability: float) -> str:
    return GREEN if probability >= 85 else ORANGE if probability >= 50 else RED


def _forecast(data: dict[str, Any]) -> str:
    scope = data.get("scope") or {}
    kind = scope.get("kind")
    name = escape(str(scope.get("name") or ""))
    scope_label = (f"冲刺「{name}」" if kind == "sprint" else f"史诗「{name}」" if kind == "epic"
                   else "全部未完成问题")
    remaining, total = int(scope.get("remaining") or 0), int(scope.get("total") or 0)
    summary = (
        f'<div style="font-size:13px;color:{MUTED};margin-bottom:10px">{scope_label}剩余 '
        f'<b style="color:{TEXT}">{remaining}</b> / {total} 项 · 近 {data.get("sample_days", 30)}'
        f' 天完成 {data.get("throughput_total", 0)} 项（约 {data.get("throughput_per_week", 0)}'
        f" 项/周）</div>"
    )
    finish = data.get("finish")
    if remaining == 0:
        return summary + _empty("范围内的问题已全部完成")
    if not finish:
        return summary + _empty("近期没有完成记录，暂时无法预测完成日期")
    tiles = []
    for index, (key, label, color) in enumerate((("p50", "50% 把握", ORANGE),
                                                  ("p85", "85% 把握", BLUE),
                                                  ("p95", "95% 把握", GREEN))):
        value = finish.get(key)
        gap = {0: "0 6px 0 0", 1: "0 3px", 2: "0 0 0 6px"}[index]
        tiles.append(
            f'<td width="33%" valign="top" style="padding:{gap}">'
            f'<div style="background:{PANEL};border-radius:10px;padding:10px 12px">'
            f'<div style="font-size:12px;color:{MUTED}">{label}完成</div>'
            f'<div style="font-size:18px;font-weight:700;color:{color if value else FAINT};'
            f'margin-top:2px">{escape(value) if value else "一年以上"}</div></div></td>'
        )
    parts = [summary, '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
             f'<tr>{"".join(tiles)}</tr></table>']
    target = data.get("target") or {}
    probability = target.get("probability")
    if probability is not None:
        tone = _confidence_color(float(probability))
        filled = max(min(round(float(probability)), 100), 0)
        deadline = "冲刺结束" if kind == "sprint" and target.get("date") == scope.get("end_date") \
            else "目标日"
        bar = (f'<td width="{filled}%" height="10" bgcolor="{tone}" style="background:{tone};'
               f'font-size:0;line-height:0">&nbsp;</td>' if filled else "")
        rest = (f'<td height="10" bgcolor="{LINE}" style="background:{LINE};font-size:0;'
                f'line-height:0">&nbsp;</td>' if filled < 100 else "")
        parts.append(
            f'<div style="font-size:13px;margin:14px 0 6px">在{deadline} '
            f'<b>{escape(str(target.get("date") or ""))}</b> 前完成的概率 '
            f'<b style="color:{tone}">{probability}%</b></div>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-radius:5px;overflow:hidden"><tr>{bar}{rest}</tr></table>'
        )
    curve = data.get("curve") or []
    if len(curve) > 1:
        columns = "".join(
            f'<td valign="bottom" align="center" style="height:84px;padding:0 2px;'
            f'border-bottom:1px solid {LINE}">'
            f'<div style="font-size:10px;color:{MUTED}">{round(float(c["probability"]))}%</div>'
            f'<table role="presentation" cellpadding="0" cellspacing="0" align="center"><tr>'
            f'<td width="18" height="{max(round(float(c["probability"]) * 0.64), 2)}" '
            f'bgcolor="{_confidence_color(float(c["probability"]))}" style="background:'
            f'{_confidence_color(float(c["probability"]))};border-radius:3px 3px 0 0;'
            f'font-size:0;line-height:0">&nbsp;</td></tr></table></td>'
            for c in curve
        )
        labels = "".join(
            f'<td align="center" style="font-size:11px;color:{FAINT};padding-top:4px">'
            f'{escape(str(c["date"]))}</td>' for c in curve
        )
        parts.append(
            f'<div style="font-size:12px;color:{MUTED};margin:16px 0 6px">'
            f"截至各日期完成剩余工作的累计概率</div>"
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            f"<tr>{columns}</tr><tr>{labels}</tr></table>"
        )
    unreachable = float(data.get("unreachable_pct") or 0)
    if unreachable > 0:
        parts.append(f'<div style="font-size:12px;color:{RED};margin-top:8px">'
                     f"有 {unreachable}% 的模拟在一年内无法完成</div>")
    return "".join(parts)


def charts_html(metrics: dict[str, Any]) -> str:
    totals = metrics.get("totals") or {}
    by_status = metrics.get("by_status") or []
    by_priority = metrics.get("by_priority") or []
    workload = metrics.get("workload") or []
    parts = [_kpis(totals)]
    if metrics.get("trend"):
        parts.append(_card("新建与完成趋势", _trend(metrics["trend"]), "近 7 天"))
    if metrics.get("forecast"):
        parts.append(_card("交付预测", _forecast(metrics["forecast"]),
                           "蒙特卡洛模拟，项目级"))
    status = _bars([(s["name"], [(int(s["count"]), s.get("color") or BLUE)])
                    for s in by_status]) if by_status else _empty()
    priority = _bars([(PRIORITIES.get(p["key"], (p["key"], BLUE))[0],
                       [(int(p["count"]), PRIORITIES.get(p["key"], ("", BLUE))[1])])
                      for p in by_priority], label_width=40) if by_priority else _empty()
    parts.append(
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td width="50%" valign="top" style="padding-right:7px">'
        f'{_card("未完成 · 按状态", status)}</td>'
        f'<td width="50%" valign="top" style="padding-left:7px">'
        f'{_card("未完成 · 按优先级", priority)}</td></tr></table>'
    )
    if workload:
        rows = [(w["name"], [(int(w["open"]) - int(w["overdue"]), BLUE),
                             (int(w["overdue"]), RED)]) for w in workload]
        parts.append(_card("负责人工作量", _bars(rows, label_width=96)
                           + _legend([("进行中", BLUE), ("已逾期", RED)]), "未完成，前 8 名"))
    return "".join(parts)


# ---------------------------------------------------------------- document


def render_report(report: AiReport, project: Project, *, issue_url: IssueUrl | None = None,
                  link: str = "") -> str:
    """Email-safe HTML fragment of a report: header, key numbers, charts and AI narrative."""
    metrics = report.metrics or {}
    label = "日报" if report.period == "daily" else "周报"
    meta = " · ".join(escape(str(x)) for x in (
        f"{project.key} {project.name}", metrics.get("scope"), metrics.get("period")) if x)
    header = (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin-bottom:14px"><tr><td bgcolor="#0b1220" style="background:#0b1220;'
        f'background-image:linear-gradient(135deg,#0b1220,#1b2a4a);border-radius:12px;'
        f'padding:20px 22px">'
        f'<div style="font-size:11px;letter-spacing:2px;color:#8fb4ff;font-weight:600">'
        f"MIRA · AI {label}</div>"
        f'<div style="font-size:20px;line-height:1.35;font-weight:700;color:#ffffff;'
        f'margin-top:6px">{escape(report.title)}</div>'
        f'<div style="font-size:12px;color:#aab4c8;margin-top:6px">{meta}</div>'
        f"</td></tr></table>"
    )
    narrative = markdown_html(report.content, styled=True, issue_url=issue_url,
                              project_key=project.key)
    footer = (f'<p style="margin:22px 0 0"><a href="{escape(link)}" style="display:inline-block;'
              f'background:{BLUE};color:#ffffff;text-decoration:none;border-radius:8px;'
              f'padding:8px 16px;font-size:13px">在 Mira 中查看全部报告</a></p>'
              if link else "")
    return (
        f'<div style="max-width:680px;font-family:{FONT};font-size:14px;line-height:1.6;'
        f'color:{TEXT}">{header}{charts_html(metrics) if metrics else ""}'
        f'<div style="margin-top:6px">{narrative}</div>{footer}</div>'
    )


def preview_document(title: str, body: str) -> str:
    """A standalone page that frames an email body the way a mail client would."""
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{escape(title)}</title><base target="_blank">'
        f'<style>html,body{{margin:0;background:{PANEL}}}</style></head><body>'
        '<div style="max-width:744px;margin:0 auto;padding:20px 12px">'
        '<div style="background:#ffffff;border-radius:14px;padding:26px 28px;'
        f'box-shadow:0 1px 3px rgba(0,0,0,.08)">{body}</div></div></body></html>'
    )
