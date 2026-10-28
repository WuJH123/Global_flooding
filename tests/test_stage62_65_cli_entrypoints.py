"""Windows/CLI entrypoints must work without adding R34 to sys.path by hand."""
import subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_r35_gee_entrypoint_help():
    for name in ("62_gee_freeze_grid10.py","64_gee_context10_exports.py","65_gee_event_weather10.py"):
        p=ROOT/"pipelines"/"r35"/name
        done=subprocess.run([sys.executable,str(p),"--help"],
             cwd=ROOT,capture_output=True,text=True,timeout=30)
        assert done.returncode==0,(name,done.stdout,done.stderr)
