import json
import sys
from pathlib import Path

from deeptutor_link.assets.skill.scripts import deeptutor_link as skill_client
from deeptutor_link.installer import inspect_host, patch_host, setup_skill


def parser():
    p = skill_client.parser()
    p.description = "DeepTutor-Link server/Skill setup and standalone client commands."
    commands = next(a for a in p._actions if a.dest == "command")
    for action in ("server-check", "server-install", "server-uninstall"):
        cmd = commands.add_parser(action, help="Inspect/patch the pinned DeepTutor package.")
        cmd.add_argument("--host", type=Path, required=True)
        if action != "server-check":
            cmd.add_argument("--dry-run", action="store_true")
    for action in ("skill-install", "skill-uninstall"):
        cmd = commands.add_parser(action)
        cmd.add_argument("--target", type=Path, default=Path.home() / ".agents/skills")
        cmd.add_argument("--dry-run", action="store_true")
    return p


def execute(a):
    if a.command == "server-check":
        return inspect_host(a.host)
    if a.command.startswith("server-"):
        return patch_host(a.host, dry_run=a.dry_run, uninstall=a.command == "server-uninstall")
    if a.command.startswith("skill-"):
        return setup_skill(a.target, dry_run=a.dry_run, uninstall=a.command == "skill-uninstall")
    return skill_client.execute(a)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        print(json.dumps(execute(args), ensure_ascii=False, indent=2))
        return 0
    except skill_client.CommandError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
    except (ValueError, OSError, KeyError) as exc:
        message = (
            str(exc)
            if args.command.startswith(("server-", "skill-"))
            else "Check arguments, private config, authentication and server health."
        )
        print(json.dumps({"error": message}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
