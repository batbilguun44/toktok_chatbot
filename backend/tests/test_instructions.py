"""Operation-team bot instructions: editable part of the system prompt with review workflow."""

import pytest

from app.cms.preview import preview_answer
from app.cms.service import CMSError, DraftInput
from app.container import build_container
from app.personas.prompts import DEFAULT_INSTRUCTIONS, INSTRUCTIONS_MAX_CHARS
from app.staff.auth import Principal
from app.staff.store import permissions_for
from tests.conftest import collect
from tests.helpers import ScriptedProvider

EDITOR = Principal("ed@toktok.mn", ["operator"], permissions_for(["operator"]), "dev")
APPROVER = Principal("lead@toktok.mn", ["admin"], permissions_for(["admin"]), "dev")

LOCKED_MARKERS = ["## Accuracy rules", "Never invent or estimate", "## Privacy and safety",
                  "Fixed rules for drivers", "the rules above always win"]


async def turn(c, token, text, sid=None):
    user = await c.services.gateway.resolve_user(token)
    events = await collect(c.chat.stream_turn(user, sid, text))
    return events[0]["data"].get("session_id")


async def publish(c, persona, body, author=EDITOR, approver=APPROVER):
    await c.prompts.save_draft(author, persona, DraftInput(persona, f"{persona} instructions", body, []))
    v = await c.prompts.submit(author, persona)
    return await c.prompts.approve(approver, persona, v.version, None)


async def test_defaults_are_seeded_and_locked_rules_always_present(settings):
    llm = ScriptedProvider()
    c = await build_container(settings, llm=llm)
    assert c.services.instructions["driver"] == DEFAULT_INSTRUCTIONS["driver"].strip()
    await turn(c, "demo-driver", "сайн уу")
    system = llm.calls[-1]["system"]
    assert all(m in system for m in LOCKED_MARKERS)
    assert "Хамгийн чухал үйлдлийг эхэнд хэл" in system
    await c.aclose()


async def test_publish_applies_to_new_sessions_only(settings):
    llm = ScriptedProvider()
    c = await build_container(settings, llm=llm)
    old_sid = await turn(c, "demo-driver", "сайн уу")

    await c.prompts.save_draft(EDITOR, "driver", DraftInput("driver", "t", "Хариулт бүрийн төгсгөлд АЮУЛГҮЙ ЯВААРАЙ гэж бич.", []))
    await turn(c, "demo-driver", "шинэ яриа")
    assert "АЮУЛГҮЙ ЯВААРАЙ" not in llm.calls[-1]["system"]  # a draft never reaches users

    v = await c.prompts.submit(EDITOR, "driver")
    await c.prompts.approve(APPROVER, "driver", v.version, "ok")
    await turn(c, "demo-driver", "шинэ яриа 2")
    new_system = llm.calls[-1]["system"]
    assert "АЮУЛГҮЙ ЯВААРАЙ" in new_system and all(m in new_system for m in LOCKED_MARKERS)

    await turn(c, "demo-driver", "үргэлжлүүлье", old_sid)
    assert "АЮУЛГҮЙ ЯВААРАЙ" not in llm.calls[-1]["system"]  # running conversation stays frozen

    await turn(c, "demo-client", "сайн уу")
    assert "АЮУЛГҮЙ ЯВААРАЙ" not in llm.calls[-1]["system"]  # persona-scoped
    await c.aclose()


async def test_four_eyes_reject_rollback_and_validation(settings):
    c = await build_container(settings, llm=ScriptedProvider())
    await c.prompts.save_draft(EDITOR, "client", DraftInput("client", "t", "Шинэ заавар нэг хоёр гурав", []))
    v = await c.prompts.submit(EDITOR, "client")
    self_approver = Principal("ed@toktok.mn", ["operator"], permissions_for(["operator"]), "dev")
    with pytest.raises(CMSError) as e:
        await c.prompts.approve(self_approver, "client", v.version, None)
    assert e.value.status == 403

    await c.prompts.reject(APPROVER, "client", v.version, "Дутуу")
    assert c.services.instructions["client"] == DEFAULT_INSTRUCTIONS["client"].strip()

    await publish(c, "client", "Хоёр дахь хувилбарын заавар")
    assert c.services.instructions["client"] == "Хоёр дахь хувилбарын заавар"
    rb = await c.prompts.rollback(APPROVER, "client", 1)
    assert rb.state == "published" and c.services.instructions["client"] == DEFAULT_INSTRUCTIONS["client"].strip()

    for persona, body in [("all", "Буруу хэрэглэгчийн төрөл"), ("driver", "x" * (INSTRUCTIONS_MAX_CHARS + 1)),
                          ("driver", "богино")]:
        with pytest.raises(CMSError):
            await c.prompts.save_draft(EDITOR, persona, DraftInput(persona, "t", body, []))
    with pytest.raises(CMSError):
        await c.prompts.set_archived(APPROVER, "driver", True)
    actions = [a["action"] for a in await c.staff.list_audit(50)]
    assert {"prompt.save_draft", "prompt.submit", "prompt.reject", "prompt.approve", "prompt.rollback"} <= set(actions)
    await c.aclose()


async def test_preview_uses_unsaved_text_without_publishing(settings):
    llm = ScriptedProvider()
    c = await build_container(settings, llm=llm)
    await preview_answer(c.services, await c.cms.published_docs(), "driver", "Сайн уу", None,
                         instructions="ТУРШИЛТЫН ЗААВАР")
    assert "ТУРШИЛТЫН ЗААВАР" in llm.calls[-1]["system"]
    assert "ТУРШИЛТЫН ЗААВАР" not in c.services.instructions["driver"]
    await c.aclose()


async def test_replicas_pick_up_published_instructions(settings):
    s = settings.model_copy(update={"kb_revision_check_seconds": 0})
    a = await build_container(s, llm=ScriptedProvider())
    b = await build_container(s, llm=ScriptedProvider())
    b.prompts.store = a.prompts.store  # shared database
    await publish(a, "driver", "Шинэ хүргэгчийн заавар v2")
    await b.ensure_fresh()
    assert b.services.instructions["driver"] == "Шинэ хүргэгчийн заавар v2"
    await a.aclose()
    await b.aclose()


def test_reply_language_is_an_operation_decision():
    from app.personas.prompts import locked_prompt, system_prompt

    for persona in ("driver", "client", "customer"):
        locked = locked_prompt(persona)
        assert "reply in English" not in locked and "Operation team instructions" in locked
        assert "- Хэл:" in DEFAULT_INSTRUCTIONS[persona]
    only_mn = system_prompt("driver", "- Хэл: зөвхөн монголоор хариул, англиар бичсэн ч монголоор.")
    assert "зөвхөн монголоор" in only_mn and "reply in English" not in only_mn


def test_two_roles_operator_drafts_admin_publishes():
    from app.staff.store import ROLES

    assert ROLES == ("operator", "admin")
    op, admin = permissions_for(["operator"]), permissions_for(["admin"])
    assert {"handoff", "kb.read", "kb.edit"} <= op and "kb.publish" not in op and "staff.manage" not in op
    assert {"handoff", "kb.publish", "staff.manage", "audit.read"} <= admin


async def test_admin_may_publish_own_change(settings):
    c = await build_container(settings, llm=ScriptedProvider())
    boss = Principal("boss@toktok.mn", ["admin"], permissions_for(["admin"]), "dev")
    v = await publish(c, "driver", "Админы өөрийн өөрчлөлт", author=boss, approver=boss)
    assert v.state == "published" and c.services.instructions["driver"] == "Админы өөрийн өөрчлөлт"
    await c.aclose()
