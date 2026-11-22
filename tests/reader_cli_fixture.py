"""Disposable reader fixture: real CLI subprocesses with synthetic model output."""
import json
import os
from pathlib import Path
import sys

SENTINEL = "DISTANT-PAPER-READ-7c92"
FAKE_CLI = r'''
import json, os, pathlib, sys
engine = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
if any(a in args for a in ("--version", "-V", "--list-models")):
    print({"codex":"codex-cli 0.169.0", "claude":"2.1.0", "pi":"pi 1.6.0"}[engine]);sys.exit()
prompt = sys.stdin.read()
root = pathlib.Path.cwd()
paper = json.loads((root / "paper.json").read_text())
sentinel = paper["blocks"][-1]["en"]
assert sentinel not in prompt, "fixture must prove file access, not an inline answer"
blocked = engine == "codex" and os.environ.get("READER_RESTRICTED") == "1" and "--dangerously-bypass-approvals-and-sandbox" not in args
if engine == "codex":
    assert "features.shell_tool=false" not in args
elif engine == "claude":
    tools = args[args.index("--tools")+1]
    assert tools == "default" or "Read" in tools.split(",")
else:
    assert "--no-tools" not in args
    if "--tools" in args: assert "read" in args[args.index("--tools")+1].split(",")
if blocked:
    answer = "Local file reading failed."
    print(json.dumps({"type":"item.completed","item":{"type":"command_execution","command":"cat paper.json",
        "exit_code":1,"aggregated_output":"bwrap: Failed to make / slave: Operation not permitted"}}),flush=True)
else:
    answer = "Read from paper.json: " + sentinel
with open(os.environ["READER_CLI_LOG"], "a") as log:
    log.write(json.dumps({"engine":engine,"argv":args,"cwd":str(root),"blocked":blocked,"inline":sentinel in prompt}) + "\n")
if engine == "codex":
    pathlib.Path(args[args.index("-o")+1]).write_text(answer)
    print(json.dumps({"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}))
elif engine == "claude":
    print(json.dumps({"type":"stream_event","event":{"delta":{"type":"text_delta","text":answer}}}),flush=True)
    print(json.dumps({"type":"result","subtype":"success","result":answer,"usage":{}}),flush=True)
else:
    print(json.dumps({"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":answer}],"stopReason":"stop"}}),flush=True)
    print(json.dumps({"type":"agent_settled","aborted":False}),flush=True)
'''


def prepare(base):
    base = Path(base)
    home, app, library, bin_dir = [base / name for name in ("home", "app", "library", "bin")]
    for directory in (home, app, library, bin_dir):
        directory.mkdir(parents=True, exist_ok=True)
    for engine in ("codex", "claude", "pi"):
        script = bin_dir / engine
        script.write_text("#!" + sys.executable + "\n" + FAKE_CLI)
        script.chmod(0o700)
    env = {"HOME":str(home), "CODEX_HOME":str(home / ".codex"), "CLAUDE_CONFIG_DIR":str(home / ".claude"),
           "PI_CODING_AGENT_DIR":str(home / ".pi"), "EASYREAD_HOME":str(app), "EASYREAD_LIBRARY":str(library),
           "EASYREAD_SYSTEM_LANG":"zh-CN", "EASYREAD_ACCESS_CODE":"", "READER_CLI_LOG":str(base / "cli.jsonl"), "READER_RESTRICTED":"1",
           "PATH":str(bin_dir) + os.pathsep + "/usr/bin:/bin"}
    os.environ.update(env)
    from easyread.library.store import Workspace, empty_reader, empty_discussion
    root = library / "reader-fixture"
    root.mkdir()
    paper = {"schema":2,"meta":{"title_en":"Reader CLI fixture","title_zh":"论文 CLI 读取样例","page_count":1},
             "translation":{"done_pages":[1],"gaps_checked":True},"blocks":[
                 {"id":f"p{i}","type":"para","page":1,"en":f"Local paragraph {i}.","zh":f"测试段落 {i}。"}
                 for i in range(16)]}
    paper["blocks"][-1]["en"] = SENTINEL
    for name, value in {"paper":paper,"reader":empty_reader(),"discussion":empty_discussion()}.items():
        (root / (name + ".json")).write_text(json.dumps(value, ensure_ascii=False))
    cfg = {"engine":"none","auto_translate":False,"check_updates":False,"library_dir":str(library),
           "qa_cli":{},
           "chat":{"default":"codex-test","models":[{"id":e+"-test","engine":e,"model":"fixture/model","name":e+" reader"}
                                                     for e in ("codex","claude","pi")]}}
    for engine in ("codex","claude","pi"):
        cfg[engine] = {"command":str(bin_dir / engine),"model":"fixture/model","timeout":20}
    (app / "config.json").write_text(json.dumps(cfg))
    return Workspace(root), cfg


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory(prefix="easyread-reader-cli-") as directory:
        prepare(directory)
        from easyread.app import native_cli_settings
        native_cli_settings.codex_effective = lambda *_args, **_kwargs: {}  # No configured native MCP in this fixture.
        from easyread.engines import netcheck
        netcheck.problem = lambda *_args, **_kwargs: None  # Synthetic CLIs have no provider to probe.
        print("READER_FIXTURE " + json.dumps({"root":directory,"log":os.environ["READER_CLI_LOG"]}),flush=True)
        from easyread.server.launch import serve
        serve(0)
