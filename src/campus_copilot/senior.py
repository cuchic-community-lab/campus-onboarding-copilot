"""师哥师姐问答：学生提问入队 + 师哥师姐答复 + 同设备历史。

存储：data/senior/questions.json（单一真值源）+ 可选人工回复目录
      senior_replies/<question_id>.md（人工维护，启动/轮询时导入）。

身份：前端 cookie 的 xh_device_id 作为 device_id，同设备可见自己的提问与答复。
管理：POST /api/senior/reply 需要 SENIOR_ADMIN_TOKEN；也可直接往
      senior_replies/ 目录放 <question_id>.md 文件，内容即答复正文。
"""

import json
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .config import SENIOR_ADMIN_TOKEN, SENIOR_DIR, SENIOR_REPLIES_DIR

_QUESTIONS_FILE = SENIOR_DIR / "questions.json"
_LOCK = threading.Lock()
_MAX_QUESTION_LEN = 500


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ensure_store() -> None:
    SENIOR_DIR.mkdir(parents=True, exist_ok=True)
    if not _QUESTIONS_FILE.exists():
        _QUESTIONS_FILE.write_text(json.dumps({"questions": []}, ensure_ascii=False, indent=2), encoding="utf-8")


def _read() -> List[Dict[str, object]]:
    _ensure_store()
    try:
        payload = json.loads(_QUESTIONS_FILE.read_text(encoding="utf-8"))
        return list(payload.get("questions", []))
    except (OSError, json.JSONDecodeError):
        return []


def _write(questions: List[Dict[str, object]]) -> None:
    _ensure_store()
    tmp = _QUESTIONS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"questions": questions}, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_QUESTIONS_FILE)


def _import_reply_files(questions: List[Dict[str, object]]) -> bool:
    """Import manually-maintained senior_replies/<question_id>.md files."""
    if not SENIOR_REPLIES_DIR.is_dir():
        return False
    changed = False
    by_id = {str(q.get("question_id")): q for q in questions}
    for reply_file in SENIOR_REPLIES_DIR.glob("*.md"):
        question_id = reply_file.stem
        question = by_id.get(question_id)
        if question is None or question.get("status") == "answered":
            continue
        text = reply_file.read_text(encoding="utf-8").strip()
        if not text:
            continue
        question["status"] = "answered"
        question["reply"] = {
            "text": text,
            "author": "师哥师姐",
            "replied_at": _now(),
        }
        changed = True
    return changed


def ask(device_id: str, question: str, nickname: str = "匿名") -> Dict[str, object]:
    question = question.strip()
    if not device_id:
        raise ValueError("device_id_required")
    if not question:
        raise ValueError("question_required")
    if len(question) > _MAX_QUESTION_LEN:
        raise ValueError("question_too_long")

    with _LOCK:
        questions = _read()
        question_id = "q_" + datetime.now().strftime("%Y%m%d") + "_" + uuid.uuid4().hex[:4]
        questions.append({
            "question_id": question_id,
            "device_id": device_id,
            "question": question,
            "nickname": nickname or "匿名",
            "status": "pending",
            "created_at": _now(),
            "reply": None,
        })
        _write(questions)
    return {
        "status": "ok",
        "question_id": question_id,
        "created_at": _now(),
        "status": "pending",
    }


def answers(device_id: str) -> Dict[str, object]:
    if not device_id:
        raise ValueError("device_id_required")
    with _LOCK:
        questions = _read()
        # Import any manually-written reply files first.
        if _import_reply_files(questions):
            _write(questions)
        mine = [
            {
                "question_id": q.get("question_id"),
                "question": q.get("question"),
                "nickname": q.get("nickname"),
                "status": q.get("status"),
                "created_at": q.get("created_at"),
                "reply": q.get("reply"),
            }
            for q in questions
            if str(q.get("device_id")) == device_id
        ]
        mine.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {"status": "ok", "device_id": device_id, "questions": mine}


def pending(token: str) -> Dict[str, object]:
    if token != SENIOR_ADMIN_TOKEN:
        raise PermissionError("token_required")
    with _LOCK:
        questions = _read()
        if _import_reply_files(questions):
            _write(questions)
        pending_list = [
            {
                "question_id": q.get("question_id"),
                "question": q.get("question"),
                "nickname": q.get("nickname"),
                "created_at": q.get("created_at"),
            }
            for q in questions
            if q.get("status") == "pending"
        ]
        pending_list.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {"status": "ok", "pending": pending_list}


def reply(token: str, question_id: str, text: str, author: str = "师哥师姐") -> Dict[str, object]:
    if token != SENIOR_ADMIN_TOKEN:
        raise PermissionError("token_required")
    text = text.strip()
    if not question_id:
        raise ValueError("question_required")
    if not text:
        raise ValueError("reply_text_required")
    with _LOCK:
        questions = _read()
        found = next((q for q in questions if q.get("question_id") == question_id), None)
        if found is None:
            raise LookupError("question_not_found")
        found["status"] = "answered"
        found["reply"] = {"text": text, "author": author or "师哥师姐", "replied_at": _now()}
        _write(questions)
    return {"status": "ok", "question_id": question_id, "status": "answered"}
