import asyncio
import httpx
import json
import argparse
import time
import re
from utils.custom_parsers import custom_response_parsers, custom_payload_parsers, custom_headers_parsers
from utils.compare_json import compare_json
from utils.replace_placeholders import replace_placeholders
from rich_argparse import ArgumentDefaultsRichHelpFormatter
from datetime import datetime
from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, MofNCompleteColumn, TimeElapsedColumn, TimeRemainingColumn

console = Console()

async def make_request(client, url, method, payload=None, headers=None, post_type="json"):
  start = time.perf_counter()
  final_payload = {}
  final_headers = {}
  try:
    if method == "POST":
      final_payload = replace_placeholders(payload) if payload else {}
      final_headers = replace_placeholders(headers) if headers else {}
      if post_type == "json":
        response = await client.post(url, json=final_payload, headers=headers, timeout=10)
      else:
        response = await client.post(url, data=final_payload, headers=headers, timeout=10)
    else:
      response = await client.get(url, headers=headers, timeout=10)
    return {
      "success": True,
      "status_code": response.status_code,
      "response_time": time.perf_counter() - start,
      "error": None,
      "response": response,
      "final_payload": final_payload,
      "final_headers": final_headers
    }
  except httpx.RequestError as e:
    return {
      "success": False,
      "status_code": None,
      "response_time": time.perf_counter() - start,
      "error": str(e),
      "response": None,
      "final_payload": final_payload,
      "final_headers": final_headers 
    }

async def run_load_test(url, num_requests=100, concurrency=10, delay=0, method="GET", payload=None, headers=None, post_type="json",print_payload=False,print_headers=False,print_response=False):
  success_count = 0
  failure_count = 0
  status_codes = {}
  response_times = []
  errors = []
  completed = 0
  start_time = time.time()

  progress = Progress(
    MofNCompleteColumn(),
    TimeElapsedColumn(),
    TimeRemainingColumn(),
    transient=True,
    expand=True
  )
  task_id = progress.add_task("",total=num_requests)

  semaphore = asyncio.Semaphore(concurrency)

  async with httpx.AsyncClient() as client:
    async def worker(delay):
      await asyncio.sleep(delay)
      nonlocal success_count, failure_count, completed
      async with semaphore:
        result = await make_request(client, url, method, payload, headers, post_type)
        completed += 1
        if result["success"]:
          success_count += 1
          response_times.append(result["response_time"])
          code = result["status_code"]
          status_codes[code] = status_codes.get(code, 0) + 1
          timestamp = datetime.now().strftime("%H:%M:%S")
          if print_headers:
            if code in custom_headers_parsers or 'all' in custom_headers_parsers:
              parsed = custom_headers_parsers[code if code in custom_headers_parsers else 'all'](result["final_headers"])
              console.print(f"[[bold cyan]{timestamp}[/bold cyan] • [bold magenta]Headers[/bold magenta] • [bold green]{result['status_code']}[/bold green]]\n{parsed}",justify="left")
            else:
             console.print(f"[[bold cyan]{timestamp}[/bold cyan] • [bold magenta]Headers[/bold magenta] • [bold green]{result['status_code']}[/bold green]]\n- Not found in `custom_headers_parsers`",justify="left")
          if print_payload:
            if code in custom_payload_parsers or 'all' in custom_payload_parsers:
              parsed = custom_payload_parsers[code if code in custom_payload_parsers else 'all'](result["final_payload"])
              console.print(f"[{timestamp} • [yellow]Payload[/yellow] • {result['status_code']}]\n{parsed}",justify="left")
            else:
              console.print(f"[{timestamp} • [yellow]Payload[/yellow] • {result['status_code']}]\n- Not Found in 'custom_payload_parsers'",justify="left")
          if print_response:
            if code in custom_response_parsers or 'all' in custom_response_parsers:
              parsed = custom_response_parsers[code if code in custom_response_parsers else 'all'](result["response"])
              console.print(f"[{timestamp} • [yellow]Response[/yellow] • {result['status_code']}]\n{parsed}",justify="left")
            else:
              console.print(f"[{timestamp} • [yellow]Response[/yellow] • {result['status_code']}]\n- Not found in `custom_response_parsers`",justify="left")
          if any([print_response, print_payload, print_headers]):
            console.print("\n")
        else:
          failure_count += 1
          errors.append(result["error"])
        progress.advance(task_id)
        if delay > 0:
          await asyncio.sleep(delay)

    def render_ui():
      table = Table.grid(padding=2)
      table.add_column(justify="center", style="bold cyan")
      return Panel(Group(progress, table), title="⚡", border_style="bold magenta")

    console.print(f"[dim]Time:[/] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    with Live(render_ui(), refresh_per_second=60, console=console):
      tasks = []
      for i in range(num_requests):
        tasks.append(worker(delay))
      await asyncio.gather(*tasks)

  total_time = time.time() - start_time

  console.rule("[bold green] Final Report [/bold green]")
  console.print(f"[bold]Duration:[/] {total_time:.2f}s")
  console.print(f"[bold]Requests per second:[/] {num_requests / total_time:.2f}")
  console.print(f"[green]Successful:[/] {success_count}")
  console.print(f"[red]Failed:[/] {failure_count}")

  console.print("\n[bold cyan]Status Code Distribution:[/bold cyan]")
  sc_table = Table("Code", "Count", "Percentage")
  for code, count in sorted(status_codes.items()):
    percent = (count / success_count * 100) if success_count else 0
    sc_table.add_row(str(code), str(count), f"{percent:.1f}%")
  console.print(sc_table)

  if errors:
    console.print("\n[bold red]Sample Errors:[/bold red]")
    for err in list(set(errors))[:3]:
      console.print(f" - {err}")

def main():
  parser = argparse.ArgumentParser(prog="main.py",description="⚡ Blaze Hammer is an asynchronous API spamming tool built in Python. It is designed for stress testing APIs by generating dynamic payloads and headers through placeholder injection",epilog="\033[96mMade with 🧠 by Brainless Dip\033[0m",formatter_class=ArgumentDefaultsRichHelpFormatter)
  parser.add_argument("url",nargs="?", help="Target URL")
  parser.add_argument("-n", "--requests", type=int, default=100, help="Total number of requests")
  parser.add_argument("-c", "--concurrency", type=int, default=10, help="Concurrency level")
  parser.add_argument("-d", "--delay", type=float, default=0, help="Delay between requests")
  parser.add_argument("-m", "--method", choices=["GET", "POST"], default="GET", help="HTTP Method")
  
  parser.add_argument("--payload","-p", help="Path to JSON payload file", default="payload.json")
  parser.add_argument("--headers","--h", help="Path to JSON headers file", default="headers.json")
  parser.add_argument("--disable-headers", "-dh", action="store_true", help="Do not include the header file in the request")
  
  parser.add_argument("--post-type","-pt", choices=["json", "form"], default="json", help="Defines the body type for POST requests")
  
  parser.add_argument("--print-payload",'-pp' ,action="store_true", help="Print the payload contents")
  parser.add_argument("--print-response",'-pr' ,action="store_true", help="Print the response contents")
  parser.add_argument("--print-headers",'-ph' ,action="store_true", help="Print the headers contents")
  
  parser.add_argument("--json-diff", "-jd", nargs='+', metavar='FILE',help="Compare and show differences between json files after processing with the placeholder function")
  
  args = parser.parse_args()
  if not args.json_diff and not args.url:
    parser.error("the following argument is required: url (unless --payload-diff is used)")
  
  payload = None
  headers = None

  if args.payload:
    try:
      with open(args.payload, "r", encoding="utf-8") as f:
        payload = json.load(f)
    except Exception as e:
      console.print(f"[bold red]Failed to load payload:[/] {e}")
      exit(1)

  if args.headers and not args.disable_headers:
    try:
      with open(args.headers, "r", encoding="utf-8") as f:
        headers = json.load(f)
    except Exception as e:
      console.print(f"[bold red]Failed to load headers:[/] {e}")
      exit(1)
  
  if args.json_diff:
    for file in args.json_diff:
      try:
        with open(file, "r", encoding="utf-8") as f:
          payload = json.load(f)
          final_payload = replace_placeholders(payload)
          compare_json(payload, final_payload,file)
      except Exception as e:
        console.print(f"[bold red]Failed to load json:[/] {e}")
    exit(1)
  asyncio.run(run_load_test(
    url=args.url,
    num_requests=args.requests,
    concurrency=args.concurrency,
    delay=args.delay,
    method=args.method.upper(),
    payload=payload,
    headers=headers,
    post_type=args.post_type,
    print_payload=args.print_payload,
    print_headers=args.print_headers,
    print_response=args.print_response
  ))

if __name__ == "__main__":
  main()
