"""Parse supported coding-agent hook events into a minimal outbound schema."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .security import canonical_hash, project_identity, sanitize_text, sha256_short


@dataclass(frozen=True)
class NotificationEvent:
    event: str
    notification_id: str
    dedupe_key: str
    occurred_at: str
    project_name: str
    project_id: str
    session_id_hash: str
    turn_id_hash: str
    title: str
    client: str = "codex"
    tool_name: str | None = None
    detail: str | None = None
    question_summary: str | None = None

    def payload(self) -> dict[str, Any]:
        schema = (
            "claude.notification.v1"
            if self.client == "claude_code"
            else "codex.notification.v1"
        )
        result: dict[str, Any] = {
            "schema": schema,
            "client": self.client,
            "notification_id": self.notification_id,
            "event": self.event,
            "occurred_at": self.occurred_at,
            "project_name": self.project_name,
            "project_id": self.project_id,
            "session_id_hash": self.session_id_hash,
            "turn_id_hash": self.turn_id_hash,
            "title": self.title,
        }
        if self.tool_name:
            result["tool_name"] = self.tool_name
        if self.detail:
            result["detail"] = self.detail
        if self.question_summary:
            result["question_summary"] = self.question_summary
        return result

    def render_text(self) -> str:
        client_name = "Claude Code" if self.client == "claude_code" else "Codex"
        kind = {
            "permission_request": "权限审批",
            "task_completed": "任务结束",
            "test": "配置测试",
        }.get(self.event, "通知")
        heading = (
            f"【{client_name} 任务结束】"
            if self.event == "task_completed"
            else f"【{client_name} 待确认】"
        )
        footer = (
            f"本次 {client_name} 任务已结束。"
            if self.event == "task_completed"
            else f"请返回 {client_name} 查看并操作；此消息不能用于授权。"
        )
        lines = [
            heading,
            f"项目：{self.project_name}",
            f"类型：{kind}",
            f"事项：{self.title}",
        ]
        if self.tool_name:
            lines.append(f"工具：{self.tool_name}")
        if self.detail:
            lines.append(f"说明：{self.detail}")
        if self.question_summary:
            lines.append(f"提问：{self.question_summary}")
        lines.extend(
            [
                f"时间：{self.occurred_at}",
                f"事件：{self.notification_id}",
                footer,
            ]
        )
        return "\n".join(lines)


@dataclass(frozen=True)
class ParseResult:
    event: NotificationEvent | None
    diagnostic: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _base_event(
    data: Mapping[str, Any],
    *,
    event_name: str,
    dedupe_material: str,
    title: str,
    project_name_mode: str,
    client: str,
    tool_name: str | None = None,
    detail: str | None = None,
    question_summary: str | None = None,
) -> NotificationEvent:
    cwd = str(data.get("cwd") or ".")
    project_name, project_id = project_identity(cwd, project_name_mode)
    session_id = str(data.get("session_id") or "unknown-session")
    turn_id = str(data.get("turn_id") or "unknown-turn")
    dedupe_source = {
        "client": client,
        "event": event_name,
        "session": session_id,
        "material": dedupe_material,
    }
    dedupe_source["turn"] = turn_id
    dedupe_key = canonical_hash(dedupe_source)
    return NotificationEvent(
        event=event_name,
        notification_id=f"cxn_{dedupe_key[:20]}",
        dedupe_key=dedupe_key,
        occurred_at=_now_iso(),
        project_name=project_name,
        project_id=project_id,
        session_id_hash=f"sha256:{sha256_short(session_id, 20)}",
        turn_id_hash=f"sha256:{sha256_short(turn_id, 20)}",
        title=sanitize_text(title, 120),
        client=client,
        tool_name=sanitize_text(tool_name, 80) if tool_name else None,
        detail=sanitize_text(detail, 200) if detail else None,
        question_summary=(
            sanitize_text(question_summary, 160) if question_summary else None
        ),
    )


def parse_hook_event(
    data: Mapping[str, Any],
    *,
    project_name_mode: str = "basename",
    include_permission_description: bool = False,
    client: str = "codex",
    question_summary: str | None = None,
    question_context_id: str | None = None,
) -> ParseResult:
    """Parse only native permission requests and task-ending Stop events."""

    hook_name = data.get("hook_event_name")
    if hook_name == "PermissionRequest":
        tool_name = str(data.get("tool_name") or "unknown")
        tool_input = data.get("tool_input")
        description: str | None = None
        if include_permission_description and isinstance(tool_input, Mapping):
            raw_description = tool_input.get("description")
            if isinstance(raw_description, str) and raw_description.strip():
                description = raw_description
        material = canonical_hash({"tool_name": tool_name, "tool_input": tool_input})
        return ParseResult(
            _base_event(
                data,
                event_name="permission_request",
                dedupe_material=material,
                title=(
                    "Claude Code 有一项操作等待审批"
                    if client == "claude_code"
                    else "Codex 有一项操作等待审批"
                ),
                project_name_mode=project_name_mode,
                client=client,
                tool_name=tool_name,
                detail=description,
            )
        )

    if hook_name == "Stop":
        material = canonical_hash({"question_context_id": question_context_id})
        return ParseResult(
            _base_event(
                data,
                event_name="task_completed",
                dedupe_material=material,
                title=(
                    "Claude Code 任务已结束"
                    if client == "claude_code"
                    else "Codex 任务已结束"
                ),
                project_name_mode=project_name_mode,
                client=client,
                question_summary=question_summary,
            )
        )

    return ParseResult(None)
