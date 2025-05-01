import json
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

def format_value(val):
    """Format dicts/lists as pretty JSON, else convert to string."""
    if isinstance(val, (dict, list)):
        return json.dumps(val, indent=2, ensure_ascii=False)
    return str(val)

def compare_json(before, after, file):
    console = Console()
    table = Table(title=f"JSON Differences - {file}", show_lines=True, expand=True)
    table.add_column("Key", style="bold cyan", no_wrap=True)
    table.add_column("Before", style="bold red", overflow="fold")
    table.add_column("After", style="bold green", overflow="fold")

    for key in after:
        before_val = before.get(key, "<missing>")
        after_val = after.get(key, "<missing>")

        if before_val != after_val:
            before_text = Text(format_value(before_val), style="red")
            after_text = Text(format_value(after_val), style="green")
            table.add_row(str(key), before_text, after_text)
        else:
           before_text = Text(format_value(before_val), style="green")
           after_text = Text("No differences", style="blue")
           table.add_row(str(key), before_text, after_text)
    console.print(table)