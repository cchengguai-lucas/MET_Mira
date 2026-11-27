"""Optional outbound alert adapters. In-app notifications remain the source of truth."""

from __future__ import annotations

import base64
import hashlib
import logging
import smtplib
import ssl
from collections.abc import Iterable
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any

import httpx

from ..config import get_settings

LOGGER = logging.getLogger(__name__)
LOGO = Path(__file__).resolve().parent.parent / "assets" / "mira-logo.png"
SIGNATURE_NAME = "MET AI Group"
SIGNATURE_TAGLINE = "Mira · 宸微设备科技｜本邮件由系统自动发送"
FONT = "-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif"


@dataclass(frozen=True)
class Mail:
    to: list[str]
    subject: str
    text: str
    html: str | None = None


def _delivery_headers(
    delivery_id: str | None, dedupe_key: str | None
) -> dict[str, str]:
    headers = {}
    if delivery_id:
        headers["X-Mira-Outbox-ID"] = delivery_id
    if dedupe_key:
        headers["Idempotency-Key"] = dedupe_key
    return headers


def send(
    kind: str,
    title: str,
    payload: dict[str, Any],
    *,
    delivery_id: str | None = None,
    dedupe_key: str | None = None,
) -> bool:
    url = get_settings().alert_webhook_url
    if not url:
        return False
    try:
        response = httpx.post(
            url,
            json={"kind": kind, "title": title, "payload": payload},
            headers=_delivery_headers(delivery_id, dedupe_key),
            timeout=5,
        )
        response.raise_for_status()
        return True
    except Exception as error:  # noqa: BLE001 - alerts must not break the scheduler
        LOGGER.warning("Alert webhook delivery failed: %s", type(error).__name__)
        return False


def recipients(emails: Iterable[str | None], *, redirect: bool = True) -> list[str]:
    targets = sorted({e.strip().lower() for e in emails if e and "@" in e})
    override = get_settings().alert_redirect
    if redirect and override and targets:
        return [override]
    return targets


def wecom_configured() -> bool:
    settings = get_settings()
    return bool(settings.wecom_push_url and settings.wecom_push_token)


def push_wecom(
    emails: Iterable[str | None],
    markdown: str,
    *,
    redirect: bool = True,
    delivery_id: str | None = None,
    dedupe_key: str | None = None,
) -> dict[str, Any]:
    """Push markdown to each email's bound WeCom account; unbound recipients are skipped."""
    settings = get_settings()
    targets = recipients(emails, redirect=redirect)
    result: dict[str, Any] = {"configured": wecom_configured(), "delivered": []}
    if not result["configured"] or not targets or not markdown.strip():
        return result
    try:
        response = httpx.post(
            settings.wecom_push_url,
            json={"emails": targets, "markdown": markdown},
            headers={
                "Authorization": f"Bearer {settings.wecom_push_token}",
                **_delivery_headers(delivery_id, dedupe_key),
            },
            timeout=30,
        )
        response.raise_for_status()
        result["delivered"] = response.json().get("delivered") or []
    except httpx.ConnectError:
        LOGGER.warning("WeCom push failed: gateway unreachable (%s)", settings.wecom_push_url)
        result["error"] = "企业微信网关不可达"
    except httpx.HTTPStatusError as error:
        LOGGER.warning("WeCom push failed: HTTP %s", error.response.status_code)
        result["error"] = f"企业微信网关返回 {error.response.status_code}"
    except Exception as error:  # noqa: BLE001 - alerts must not break the scheduler
        LOGGER.warning("WeCom push failed: %s", type(error).__name__)
        result["error"] = type(error).__name__
    return result


def mail_configured() -> bool:
    settings = get_settings()
    return bool(settings.smtp_host and settings.smtp_user and settings.smtp_password)


@lru_cache(maxsize=1)
def _logo() -> bytes | None:
    try:
        return LOGO.read_bytes()
    except OSError:
        return None


def logo_data_uri() -> str | None:
    logo = _logo()
    return f"data:image/png;base64,{base64.b64encode(logo).decode()}" if logo else None


def _signed_html(mail: Mail, logo_cid: str | None) -> str:
    """The message body plus the MET AI Group signature; the logo is an inline (cid) image
    because most mail clients block remote pictures."""
    body = mail.html or "".join(
        f"<p>{escape(line)}</p>" if line.strip() else "" for line in mail.text.splitlines()
    )
    return signed_html(body, f"cid:{logo_cid}" if logo_cid else None)


def signed_html(body: str, logo_src: str | None) -> str:
    logo = (
        f'<td style="padding-right:10px;vertical-align:middle">'
        f'<img src="{logo_src}" width="40" height="40" alt="Mira" '
        f'style="display:block;border-radius:9px"></td>'
        if logo_src else ""
    )
    signature = (
        '<table cellpadding="0" cellspacing="0" style="margin-top:28px;padding-top:14px;'
        'border-top:1px solid #e5e5ea;border-collapse:separate"><tr>'
        f"{logo}"
        '<td style="vertical-align:middle">'
        f'<div style="font-size:14px;font-weight:600;color:#1d1d1f">{SIGNATURE_NAME}</div>'
        f'<div style="font-size:12px;color:#86868b">{escape(SIGNATURE_TAGLINE)}</div>'
        "</td></tr></table>"
    )
    return (f'<div style="font-family:{FONT};font-size:14px;line-height:1.6;color:#1d1d1f">'
            f"{body}{signature}</div>")


def send_mail(
    mails: Iterable[Mail],
    *,
    redirect: bool = True,
    delivery_id: str | None = None,
    dedupe_key: str | None = None,
) -> dict[str, Any]:
    """Send each message over one SMTP session; failures are logged, never raised."""
    settings = get_settings()
    result: dict[str, Any] = {"configured": mail_configured(), "sent": 0, "failed": 0}
    batch = [(mail, recipients(mail.to, redirect=redirect)) for mail in mails]
    batch = [(mail, to) for mail, to in batch if to]
    if not result["configured"] or not batch:
        return result
    sender = formataddr((settings.mail_from_name, settings.mail_from))
    domain = settings.mail_from.split("@")[-1]
    logo = _logo()
    try:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20,
                              context=ssl.create_default_context()) as smtp:
            smtp.login(settings.smtp_user, settings.smtp_password)
            for index, (mail, to) in enumerate(batch, 1):
                message = EmailMessage()
                message["From"] = sender
                message["To"] = ", ".join(to)
                message["Subject"] = mail.subject
                message["Date"] = formatdate(localtime=True)
                if delivery_id:
                    suffix = (
                        hashlib.sha256(dedupe_key.encode()).hexdigest()[:16]
                        if dedupe_key
                        else "event"
                    )
                    message["Message-ID"] = f"<{delivery_id}.{index}.{suffix}@{domain}>"
                    message["X-Mira-Outbox-ID"] = delivery_id
                else:
                    message["Message-ID"] = make_msgid(domain=domain)
                message.set_content(f"{mail.text}\n\n-- \n{SIGNATURE_NAME}\n{SIGNATURE_TAGLINE}\n")
                cid = make_msgid("mira-logo", domain=domain) if logo else None
                message.add_alternative(_signed_html(mail, cid and cid[1:-1]), subtype="html")
                if logo and cid:
                    message.get_payload()[1].add_related(logo, "image", "png", cid=cid,
                                                         filename="mira-logo.png",
                                                         disposition="inline")
                try:
                    smtp.send_message(message)
                    result["sent"] += 1
                except smtplib.SMTPException as error:
                    LOGGER.warning("Mail delivery failed: %s", type(error).__name__)
                    result["failed"] += 1
    except Exception as error:  # noqa: BLE001 - alerts must not break the scheduler
        LOGGER.warning("SMTP session failed: %s", type(error).__name__)
        result["error"] = type(error).__name__
        result["failed"] += len(batch) - result["sent"]
    return result
