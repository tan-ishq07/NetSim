"""``netsim-automation`` — a small CLI that drives a running NetSim instance.

Examples:

    # render configs from templates + vars, print them
    python -m automation.cli render automation/labs/two_router_ospf.yaml

    # deploy the whole definition and verify intended state (CI gate)
    python -m automation.cli deploy automation/labs/two_router_ospf.yaml \\
        --url http://localhost:8000

    # ad-hoc checks against whatever is currently loaded
    python -m automation.cli drift --url http://localhost:8000
    python -m automation.cli intent R1 --url http://localhost:8000
"""
import json
from typing import Optional

import typer

from .client import NetSimClient, NetSimError
from .netdevops import (DEFAULT_TEMPLATES_DIR, deploy_and_verify, format_report,
                        load_network_def, render_configs)

app = typer.Typer(add_completion=False, help="NetSim NetDevOps automation CLI.")


@app.command()
def render(
    netdef: str,
    templates: str = typer.Option(DEFAULT_TEMPLATES_DIR, "--templates", "-t", help="Templates dir."),
    device: Optional[str] = typer.Option(None, "--device", "-d", help="Only this device."),
):
    """Render device configs from a network definition (no server needed)."""
    rendered = render_configs(load_network_def(netdef), templates)
    for name, lines in rendered.items():
        if device and name != device:
            continue
        typer.echo(f"! ==== {name} ====")
        typer.echo("\n".join(lines))
        typer.echo("")


@app.command()
def deploy(
    netdef: str,
    url: str = typer.Option("http://localhost:8000", "--url", "-u", help="NetSim base URL."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace id."),
    templates: str = typer.Option(DEFAULT_TEMPLATES_DIR, "--templates", "-t", help="Templates dir."),
):
    """Apply topology + render/push config + validate intended state."""
    client = NetSimClient(url, workspace=workspace)
    try:
        report, ok = deploy_and_verify(client, load_network_def(netdef), templates)
    except NetSimError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    typer.echo(format_report(report))
    raise typer.Exit(0 if ok else 1)


@app.command()
def validate(
    netdef: str,
    url: str = typer.Option("http://localhost:8000", "--url", "-u", help="NetSim base URL."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace id."),
):
    """Run a definition's validation assertions against the current topology."""
    client = NetSimClient(url, workspace=workspace)
    net_def = load_network_def(netdef)
    result = client.validate(net_def.get("validate", []))
    typer.echo(json.dumps(result, indent=2))
    raise typer.Exit(0 if result.get("compliant") else 1)


@app.command()
def drift(
    url: str = typer.Option("http://localhost:8000", "--url", "-u", help="NetSim base URL."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace id."),
):
    """Show config drift vs the saved baseline."""
    client = NetSimClient(url, workspace=workspace)
    typer.echo(json.dumps(client.drift(), indent=2))


@app.command()
def intent(
    device: str,
    url: str = typer.Option("http://localhost:8000", "--url", "-u", help="NetSim base URL."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Workspace id."),
):
    """Print the structured (YANG-ish) intent for a device."""
    client = NetSimClient(url, workspace=workspace)
    typer.echo(json.dumps(client.get_intent(device), indent=2))


if __name__ == "__main__":
    app()
