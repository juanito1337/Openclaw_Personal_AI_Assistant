from __future__ import annotations

from typing import Any


def add_commands(sub: Any) -> None:
    resources = sub.add_parser("resources", help="Dynamische Ressourcen verwalten")
    res_sub = resources.add_subparsers(dest="resources_command", required=True)
    res_list = res_sub.add_parser("list")
    res_list.add_argument("--kind", default="")
    res_add = res_sub.add_parser("add")
    res_add.add_argument("--id", required=True)
    res_add.add_argument("--kind", required=True)
    res_add.add_argument("--connector", required=True)
    res_add.add_argument("--remote-id", default="")
    res_add.add_argument("--permissions", default="read")
    res_add.add_argument("--disabled", action="store_true")
    res_add.add_argument(
        "--approve-permissions",
        action="store_true",
        help="Interaktive Freigabe fuer neue/erweiterte Schreibrechte",
    )

    index = sub.add_parser("index", help="Lokalen Wissensindex aktualisieren")
    index_sub = index.add_subparsers(dest="index_command", required=True)
    index_sub.add_parser("mail")
    index_sub.add_parser("all")

    search = sub.add_parser("search", help="Schnelle lokale Hybrid-Grundsuche")
    search.add_argument("query")
    search.add_argument("--limit", type=int)
    search.add_argument("--source-type", default="")
    search.add_argument("--resource", default="")

    actions = sub.add_parser("actions", help="ActionPlan/Outbox verwalten")
    act_sub = actions.add_subparsers(dest="actions_command", required=True)
    act_list = act_sub.add_parser("list")
    act_list.add_argument("--status", default="")
    act_list.add_argument("--limit", type=int, default=100)
    upload = act_sub.add_parser("plan-upload")
    upload.add_argument("local_path")
    upload.add_argument("remote_path")
    upload.add_argument("--resource", default="nextcloud-files-main")
    upload.add_argument("--content-type", default="application/octet-stream")
    event = act_sub.add_parser("plan-event")
    event.add_argument("ics_file")
    event.add_argument("--uid", required=True)
    event.add_argument("--resource", required=True)
    task = act_sub.add_parser("plan-task")
    task.add_argument("ics_file")
    task.add_argument("--uid", required=True)
    task.add_argument("--resource", required=True)
    approve = act_sub.add_parser("approve")
    approve.add_argument("action_id")
    execute = act_sub.add_parser("execute")
    execute.add_argument("action_id")

    settings = sub.add_parser("settings", help="Kontrollierte, sichere Settings")
    set_sub = settings.add_subparsers(dest="settings_command", required=True)
    set_sub.add_parser("list")
    set_value = set_sub.add_parser("set")
    set_value.add_argument("key")
    set_value.add_argument("value")
