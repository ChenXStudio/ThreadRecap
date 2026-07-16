from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "skills" / "thread-recap" / "SKILL.md"


def test_skill_defines_structured_incremental_recap_and_machine_marker() -> None:
    source = SKILL.read_text(encoding="utf-8")

    for heading in ("阶段目标", "已完成", "关键决定", "当前状态", "待办与下一步", "风险或阻塞"):
        assert heading in source
    assert "[thread-recap:summary:v1" in source
    assert "没有新增" in source
    assert "工具日志" in source
