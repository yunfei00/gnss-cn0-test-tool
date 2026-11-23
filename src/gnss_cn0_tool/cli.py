import argparse
import sys
import threading
from pathlib import Path

from gnss_cn0_tool.adb import Adb, DeviceError
from gnss_cn0_tool.reporting import replay
from gnss_cn0_tool.run import RunControls, Runner, device_readiness, recover
from gnss_cn0_tool.store import Plan, RunStore
from gnss_cn0_tool.version import APP_VERSION


def main() -> int:
    parser = argparse.ArgumentParser(prog="gnss-cn0")
    parser.add_argument("--smoke", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("gui")
    offline = commands.add_parser("replay", help="分析现有运行目录")
    offline.add_argument("folder", type=Path)
    recovery = commands.add_parser("recover", help="从重新连接的手机回收已中断任务")
    recovery.add_argument("folder", type=Path)
    recovery.add_argument("--serial", required=True)
    recovery.add_argument("--adb", default="adb")
    recovery.add_argument("--test-apps", action="store_true")
    live = commands.add_parser("run", help="用指定的 ADB 设备运行测试计划")
    live.add_argument("plan", type=Path)
    live.add_argument("--serial", required=True)
    live.add_argument("--adb", default="adb")
    live.add_argument("--root", type=Path, default=Path("runs"))
    live.add_argument("--test-apps", action="store_true")
    check = commands.add_parser("preflight", help="检查当前手机是否满足所选计划的测试条件")
    check.add_argument("plan", type=Path)
    check.add_argument("--serial", required=True)
    check.add_argument("--adb", default="adb")
    check.add_argument("--test-apps", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "replay":
            replay(args.folder)
            print(args.folder / "reports" / "report.xlsx")
        elif args.command == "recover":
            recover(Adb(args.adb, args.serial, test_apps=args.test_apps), args.folder, args.folder.name)
            print(args.folder / "reports" / "report.xlsx")
        elif args.command == "preflight":
            plan = Plan.load(args.plan)
            _, _, _, checks = device_readiness(
                Adb(args.adb, args.serial, test_apps=args.test_apps),
                plan, automatic_apps=not args.test_apps,
                manual_background=any(s.mode == "manual" and s.scene_id != "screen_off" for s in plan.scenes),
            )
            for result in checks:
                print(result)
            return int(any(result.status == "失败" for result in checks))
        elif args.command == "run":
            controls = RunControls()
            plan = Plan.load(args.plan)

            def request_confirmation():
                try:
                    response = input("确认=c，跳过=s，取消=q > ").strip().lower()
                except EOFError:
                    controls.cancel.set()
                    return
                action = {"c": controls.manual, "s": controls.skip}.get(response, controls.cancel)
                action.set()

            def update(state, reason):
                print(state, reason, flush=True)
                if state in {"WAITING_MANUAL_CONFIRM", "WAITING_MANUAL_EXIT"}:
                    threading.Thread(target=request_confirmation, daemon=True).start()

            runner = Runner(
                Adb(args.adb, args.serial, test_apps=args.test_apps), RunStore(args.root),
                controls, update,
            )
            run_id, state = runner.run(plan)
            print(run_id)
            return 0 if state.startswith("COMPLETED") else 1
        else:
            from gnss_cn0_tool.gui import launch

            return launch(smoke=args.smoke)
    except (DeviceError, ValueError, OSError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
