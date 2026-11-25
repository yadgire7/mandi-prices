"""One email per run: OK, WARN or FAIL, with the ready-to-paste post inside.

Mail settings come only from environment variables (GitHub Secrets in
Phase 3, `.env` locally). Before anything is sent or saved, every known secret
value is scrubbed from the subject and body, so a secret can never leave in
an email even if some error message happened to contain one.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path

SECRET_ENV = ("DATA_GOV_IN_API_KEY", "SMTP_PASSWORD")
MAIL_ENV = ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM", "MAIL_TO")
POST_START = "----- WhatsApp post: copy from the next line -----"
POST_END = "----- end of post -----"


class MailError(Exception):
    """Sending failed; the message says at which stage, never with a setting's value."""


class NotConfigured(Exception):
    """Mail settings are missing, so the email was saved to a file instead."""


@dataclass
class Report:
    status: str                      # OK, WARN or FAIL
    when: str                        # "25 Nov 2026 07:00 IST"
    post: str | None = None
    summary: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)
    log_url: str | None = None
    tag: str = ""                    # e.g. SHADOW during the trial week


def scrub(text: str, env=os.environ) -> str:
    for name in SECRET_ENV:
        value = (env.get(name) or "").strip()
        if len(value) >= 6:
            text = text.replace(value, f"[{name} removed]")
    return text


def subject(report: Report) -> str:
    extra = " - post ready" if report.post else ""
    tag = f"[{report.tag}] " if report.tag else ""
    return f"{tag}[{report.status}] Mandi prices {report.when}{extra}"


def body(report: Report, max_warnings: int = 40) -> str:
    out = [f"Status: {report.status}", f"Run: {report.when}", ""]
    if report.summary:
        out += report.summary + [""]
    if report.post:
        out += [POST_START, report.post.rstrip("\n"), POST_END, ""]
    else:
        out += ["No post this run.", ""]
    if report.warnings:
        out.append(f"Warnings ({len(report.warnings)}):")
        out += [f"- {w}" for w in report.warnings[:max_warnings]]
        if len(report.warnings) > max_warnings:
            out.append(f"- ...and {len(report.warnings) - max_warnings} more in the run log")
        out.append("")
    if report.info:
        out.append("Info:")
        out += [f"- {i}" for i in report.info]
        out.append("")
    if report.log_url:
        out.append(f"Run log: {report.log_url}")
    return "\n".join(out).rstrip() + "\n"


def message(report: Report, env=os.environ) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = scrub(subject(report), env)
    msg["From"] = env.get("MAIL_FROM", "mandi-prices@localhost")
    msg["To"] = env.get("MAIL_TO", "admin@localhost")
    msg.set_content(scrub(body(report), env), charset="utf-8")
    return msg


def send(report: Report, env=os.environ, smtp_ssl=smtplib.SMTP_SSL, smtp=smtplib.SMTP) -> None:
    missing = [name for name in MAIL_ENV if not (env.get(name) or "").strip()]
    if missing:
        raise NotConfigured("mail settings missing: " + ", ".join(missing))
    msg = message(report, env)
    host = env["SMTP_HOST"].strip()
    try:
        port = int(str(env.get("SMTP_PORT") or 465).strip())
    except ValueError:
        raise MailError("SMTP_PORT is not a number; use 465 (SSL) or 587 (STARTTLS)") from None
    mode = "SSL" if port == 465 else "STARTTLS"
    context = ssl.create_default_context()
    stage = "connecting"
    try:
        if port == 465:
            server = smtp_ssl(host, port, context=context, timeout=30)
        else:
            server = smtp(host, port, timeout=30)
        with server:
            if port != 465:
                stage = "starting TLS"
                server.starttls(context=context)  # never send the password unencrypted
            stage = "logging in"
            server.login(env["SMTP_USER"].strip(), env["SMTP_PASSWORD"].strip())
            stage = "sending"
            server.send_message(msg)
    except Exception as err:
        # Says where it failed without printing any setting: GitHub would hide
        # them anyway, and a host or port is enough to guess the mailbox.
        hint = {"connecting": " Check SMTP_HOST and SMTP_PORT.",
                "starting TLS": " Port 465 needs SSL; 587 needs STARTTLS.",
                "logging in": " Check SMTP_USER and the app password (not the login password)."
                }.get(stage, "")
        raise MailError(f"{stage} ({mode}) failed: {type(err).__name__}: {err}.{hint}") from err


def save(report: Report, path: Path, env=os.environ) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(message(report, env)))
    return path


def log_url(env=os.environ) -> str | None:
    server, repo, run = (env.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{server}/{repo}/actions/runs/{run}" if server and repo and run else None
