"""_maybe_diff helper produces unified diffs for fs_write edits."""

import agent_runtime


def test_maybe_diff_basic(tmp_path):
    bak = tmp_path / "f.txt.bak.1"
    cur = tmp_path / "f.txt"
    bak.write_text("line one\nline two\nline three\n")
    cur.write_text("line one\nLINE TWO\nline three\n")
    text, changed = agent_runtime._maybe_diff("fs_write", {"path": str(cur)}, str(bak))
    assert text and "-line two" in text and "+LINE TWO" in text
    assert changed == 2  # one +, one -


def test_maybe_diff_skips_non_fs_write(tmp_path):
    text, _ = agent_runtime._maybe_diff("execute_bash", {"path": "x"}, "y")
    assert text is None


def test_maybe_diff_no_backup():
    text, _ = agent_runtime._maybe_diff("fs_write", {"path": "/x"}, None)
    assert text is None


def test_maybe_diff_identical(tmp_path):
    bak = tmp_path / "a.bak"
    cur = tmp_path / "a"
    bak.write_text("same\n")
    cur.write_text("same\n")
    text, _ = agent_runtime._maybe_diff("fs_write", {"path": str(cur)}, str(bak))
    assert text is None


def test_action_log_with_diff(store):
    aid = store.log_action(
        "sd1",
        "fs_write",
        {"path": "/x"},
        ok=True,
        file="/x",
        backup="/x.bak",
        diff="--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y\n",
        tool_use_id="tu-1",
    )
    a = store.get_action(aid)
    assert a["diff"] is not None and a["tool_use_id"] == "tu-1"
    # list_actions defaults to no diff payload
    rows = store.list_actions(sid="sd1")
    assert all("diff" not in r for r in rows)
    rows2 = store.list_actions(sid="sd1", include_diff=True)
    assert any(r.get("diff") for r in rows2)
