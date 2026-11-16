"""Command-reference Q&A derived from NetSim's own ``?`` grammar (pillar 2).

Walks the declarative :data:`clihelp.SPEC` tree and turns every command path
into supervised Q&A: what a command does, what may follow it, and how to perform
a described action. Because it is generated from the same grammar the live CLI
uses, the answers are exactly correct for this simulator's command set across
every mode and device kind."""
from __future__ import annotations

from typing import Dict, List

from app.engine import clihelp

ARG, CR = clihelp.ARG, clihelp.CR

SYSTEM = (
    "You are NetSim Tutor, an expert on Cisco IOS commands inside a network "
    "simulator. Answer command questions precisely: give the exact command "
    "syntax, what it does, and the mode/device it applies to."
)

_MODE_LABEL = {
    clihelp.USER: ("user EXEC mode", "Router>"),
    clihelp.PRIV: ("privileged EXEC mode", "Router#"),
    clihelp.CONFIG: ("global configuration mode", "Router(config)#"),
    clihelp.CONFIG_IF: ("interface configuration mode", "Router(config-if)#"),
    clihelp.CONFIG_DHCP: ("DHCP-pool configuration mode", "Router(dhcp-config)#"),
    clihelp.CONFIG_ROUTER: ("router configuration mode", "Router(config-router)#"),
    clihelp.CONFIG_ACL: ("ACL configuration mode", "Router(config-acl)#"),
}


def _example(q: str, a: str, meta: Dict) -> Dict:
    return {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": q},
            {"role": "assistant", "content": a},
        ],
        "meta": meta,
    }


def _token(key: str, node: clihelp.Node) -> str:
    if key == ARG:
        return f"<{node.meta or 'arg'}>"
    return key


def _walk(children: Dict[str, clihelp.Node], prefix: List[str], mode: str,
          kinds_ctx, depth: int, out: List[Dict], seen: set) -> None:
    if depth > 6:
        return
    label, _prompt = _MODE_LABEL[mode]
    for key, node in children.items():
        if key == CR:
            continue
        tok = _token(key, node)
        path = prefix + [tok]
        path_str = " ".join(path)
        # propagate the nearest kinds restriction down the path
        node_kinds = node.kinds if node.kinds is not None else kinds_ctx
        kind_note = ""
        if node_kinds is not None:
            kind_note = f" (applies to: {', '.join(node_kinds)})"

        # 1) What does this command do? (two phrasings reinforce the same fact)
        if node.help and key != ARG:
            qkey = ("do", mode, path_str)
            if qkey not in seen:
                seen.add(qkey)
                out.append(_example(
                    f"In {label}, what does the command '{path_str}' do?",
                    f"'{path_str}' - {node.help}.{kind_note}",
                    {"pillar": "cli", "kind": "describe", "mode": mode, "path": path_str},
                ))
                out.append(_example(
                    f"Explain the '{path_str}' command in {label}.",
                    f"'{path_str}' - {node.help}.{kind_note}",
                    {"pillar": "cli", "kind": "describe", "mode": mode, "path": path_str},
                ))

        # 2) Reverse: how do I <action>? (only for completable commands).
        # Several phrasings of the same intent teach a small model to map a
        # described action to the *correct* command/mode instead of grabbing a
        # similar-looking one (the main CLI failure mode in eval).
        if node.help and key != ARG and CR in node.children:
            qkey = ("how", mode, node.help.lower())
            if qkey not in seen:
                seen.add(qkey)
                action = node.help[0].lower() + node.help[1:]
                answer = f"In {label}, use:\n{path_str}{kind_note}"
                for q in (
                    f"How do I {action}?",
                    f"Which command do I use to {action.rstrip('.')}?",
                    f"In {label}, what is the command to {action.rstrip('.')}?",
                ):
                    out.append(_example(
                        q, answer,
                        {"pillar": "cli", "kind": "howto", "mode": mode, "path": path_str},
                    ))

        # 3) What can follow this token? (context help)
        followers = [(k, c) for k, c in node.children.items() if k != CR]
        if followers:
            lines = []
            for k, c in followers:
                lines.append(f"  {_token(k, c):<18} {c.help}")
            also_cr = CR in node.children
            qkey = ("next", mode, path_str)
            if qkey not in seen:
                seen.add(qkey)
                tail = "\nYou may also press Enter to run it as-is (<cr>)." if also_cr else ""
                ans = "These keywords/arguments can follow:\n" + "\n".join(lines) + tail
                out.append(_example(
                    f"In {label}, after typing '{path_str}', what can come next?",
                    ans,
                    {"pillar": "cli", "kind": "next", "mode": mode, "path": path_str},
                ))
                out.append(_example(
                    f"In {label}, what are the valid options for '{path_str} ?'",
                    ans,
                    {"pillar": "cli", "kind": "next", "mode": mode, "path": path_str},
                ))

        _walk(node.children, path, mode, node_kinds, depth + 1, out, seen)


def _mode_overview(mode: str, out: List[Dict], seen: set) -> None:
    label, prompt = _MODE_LABEL[mode]
    children = clihelp.SPEC[mode]
    cmds = sorted(k for k in children if k not in (CR,))
    lines = [f"  {k:<14} {children[k].help}" for k in cmds]
    answer = "The top-level commands are:\n" + "\n".join(lines)
    out.append(_example(
        f"What commands are available in {label} ({prompt})?",
        answer,
        {"pillar": "cli", "kind": "overview", "mode": mode},
    ))
    out.append(_example(
        f"List the commands you can run in {label}.",
        answer,
        {"pillar": "cli", "kind": "overview", "mode": mode},
    ))


def build_cli_qa() -> List[Dict]:
    out: List[Dict] = []
    seen: set = set()
    for mode in clihelp.SPEC:
        _mode_overview(mode, out, seen)
        _walk(clihelp.SPEC[mode], [], mode, None, 0, out, seen)
    return out


if __name__ == "__main__":
    qa = build_cli_qa()
    print(f"{len(qa)} CLI Q&A examples")
    for e in qa[:3]:
        print("Q:", e["messages"][1]["content"])
        print("A:", e["messages"][2]["content"][:200])
        print()
