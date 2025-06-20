import asyncio
import httpx
import json
import argparse
import time
from typing import Dict, Optional
from utils.custom_parsers import (
    custom_response_parsers,
    custom_payload_parsers,
    custom_headers_parsers,
)
from utils.compare_json import compare_json
from utils.replace_placeholders import replace_placeholders
from utils.custom_file_payload import attachments
from rich_argparse import ArgumentDefaultsRichHelpFormatter
from datetime import datetime
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.progress import (
    Progress,
    MofNCompleteColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

console = Console()


async def make_request(
    client: httpx.AsyncClient,
    url: str,
    method: str,
    payload: Optional[Dict] = None,
    headers: Optional[Dict] = None,
    post_type: str = "json",
    file_payload: bool = False,
) -> Dict:
    start = time.perf_counter()
    final_payload = {}
    final_headers = {}

    try:
        if method == "POST":
            final_payload = replace_placeholders(payload) if payload else {}
            final_headers = replace_placeholders(headers) if headers else {}

            request_args = {
                "headers": headers,
                "timeout": 30,
                **({"files": attachments} if file_payload else {}),
            }

            if post_type == "json":
                request_args["json"] = final_payload
            else:
                request_args["data"] = final_payload

            response = await client.post(url, **request_args)
        else:
            response = await client.get(url, headers=headers, timeout=30.0)

        return {
            "success": True,
            "status_code": response.status_code,
            "response_time": time.perf_counter() - start,
            "error": None,
            "response": response,
            "final_payload": final_payload,
            "final_headers": final_headers,
        }
    except (httpx.RequestError, asyncio.TimeoutError) as e:
        return {
            "success": False,
            "status_code": None,
            "response_time": time.perf_counter() - start,
            "error": str(e),
            "response": None,
            "final_payload": final_payload,
            "final_headers": final_headers,
        }


async def worker(
    client: httpx.AsyncClient,
    url: str,
    method: str,
    semaphore: asyncio.Semaphore,
    delay: float,
    payload: Optional[Dict],
    headers: Optional[Dict],
    file_payload: Optional[bool],
    post_type: str,
    print_payload: bool,
    print_headers: bool,
    print_response: bool,
    stats: Dict,
    progress: Progress,
    task_id: int,
) -> None:
    if delay > 0:
        await asyncio.sleep(delay)

    async with semaphore:
        result = await make_request(
            client, url, method, payload, headers, post_type, file_payload
        )

        stats["completed"] += 1
        if result["success"]:
            stats["success_count"] += 1
            stats["response_times"].append(result["response_time"])
            code = result["status_code"]
            stats["status_codes"][code] = stats["status_codes"].get(code, 0) + 1

            if any([print_response, print_payload, print_headers]):
                timestamp = datetime.now().strftime("%H:%M:%S")
                status_code = result["status_code"]

                if print_headers:
                    parser = custom_headers_parsers.get(
                        status_code, custom_headers_parsers.get("all")
                    )
                    output = (
                        parser(result["final_headers"])
                        if parser
                        else "- Not found in `custom_headers_parsers`"
                    )
                    console.print(
                        f"[[bold cyan]{timestamp}[/bold cyan] • [bold magenta]Headers[/bold magenta] • [bold green]{status_code}[/bold green]]\n{output}",
                        justify="left",
                    )

                if print_payload:
                    parser = custom_payload_parsers.get(
                        status_code, custom_payload_parsers.get("all")
                    )
                    output = (
                        parser(result["final_payload"])
                        if parser
                        else "- Not Found in 'custom_payload_parsers'"
                    )
                    console.print(
                        f"[{timestamp} • [yellow]Payload[/yellow] • {status_code}]\n{output}",
                        justify="left",
                    )

                if print_response:
                    parser = custom_response_parsers.get(
                        status_code, custom_response_parsers.get("all")
                    )
                    output = (
                        parser(result["response"])
                        if parser
                        else "- Not found in `custom_response_parsers`"
                    )
                    console.print(
                        f"[{timestamp} • [yellow]Response[/yellow] • {status_code}]\n{output}",
                        justify="left",
                    )

                console.print("\n")
        else:
            stats["failure_count"] += 1
            stats["errors"].add(result["error"])

        progress.advance(task_id)


async def run_load_test(
    url: str,
    num_requests: int = 100,
    concurrency: int = 100,
    delay: float = 0,
    method: str = "GET",
    payload: Optional[Dict] = None,
    headers: Optional[Dict] = None,
    file_payload: bool = False,
    post_type: str = "json",
    print_payload: bool = False,
    print_headers: bool = False,
    print_response: bool = False,
) -> None:
    stats = {
        "success_count": 0,
        "failure_count": 0,
        "status_codes": {},
        "response_times": [],
        "errors": set(),
        "completed": 0,
    }

    start_time = time.time()
    progress = Progress(
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        transient=True,
        expand=True,
    )
    task_id = progress.add_task("", total=num_requests)

    async with httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=concurrency * 2, max_keepalive_connections=concurrency
        ),
        http2=True,
    ) as client:
        semaphore = asyncio.Semaphore(concurrency)

        console.print(f"[dim]Time:[/] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

        tasks = [
            asyncio.create_task(
                worker(
                    client,
                    url,
                    method,
                    semaphore,
                    delay,
                    payload,
                    headers,
                    file_payload,
                    post_type,
                    print_payload,
                    print_headers,
                    print_response,
                    stats,
                    progress,
                    task_id,
                )
            )
            for _ in range(num_requests)
        ]

        with Live(
            Panel(progress, title="⚡ Blaze Hammer", border_style="bold magenta"),
            refresh_per_second=60,
            console=console,
        ):
            await asyncio.gather(*tasks)

    total_time = time.time() - start_time

    console.rule("[bold green] Final Report [/bold green]")
    console.print(f"[bold]Duration:[/] {total_time:.2f}s")
    console.print(f"[bold]Requests per second:[/] {num_requests / total_time:.2f}")
    console.print(f"[green]Successful:[/] {stats['success_count']}")
    console.print(f"[red]Failed:[/] {stats['failure_count']}")

    if stats["response_times"]:
        avg_time = sum(stats["response_times"]) / len(stats["response_times"])
        console.print(f"[bold]Average response time:[/] {avg_time:.3f}s")

    console.print("\n[bold cyan]Status Code Distribution:[/bold cyan]")
    sc_table = Table("Code", "Count", "Percentage")
    for code, count in sorted(stats["status_codes"].items()):
        percent = (
            (count / stats["success_count"] * 100) if stats["success_count"] else 0
        )
        sc_table.add_row(str(code), str(count), f"{percent:.1f}%")
    console.print(sc_table)

    if stats["errors"]:
        console.print("\n[bold red]Sample Errors:[/bold red]")
        for err in list(stats["errors"])[:3]:
            console.print(f" - {err}")


def main():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="⚡ Blaze Hammer is an asynchronous API spamming tool built in Python.",
        epilog="\033[96mMade with 🧠 by Brainless Dip\033[0m",
        formatter_class=ArgumentDefaultsRichHelpFormatter,
    )
    parser.add_argument("url", nargs="?", help="Target URL")
    parser.add_argument(
        "-n", "--requests", type=int, default=100, help="Total number of requests"
    )
    parser.add_argument(
        "-c", "--concurrency", type=int, default=100, help="Concurrency level"
    )
    parser.add_argument(
        "-d", "--delay", type=float, default=0, help="Delay between requests"
    )
    parser.add_argument(
        "-m", "--method", choices=["GET", "POST"], default="GET", help="HTTP Method"
    )
    parser.add_argument(
        "--payload", "-p", help="Path to JSON payload file", default="payload.json"
    )
    parser.add_argument(
        "--file-payload",
        "-fp",
        help="Include file attachments payload. This flag loads the payload from 'utils/custom_file_payload.py'",
        action="store_true",
    )
    parser.add_argument(
        "--headers", "--h", help="Path to JSON headers file", default="headers.json"
    )
    parser.add_argument(
        "--disable-headers",
        "-dh",
        action="store_true",
        help="Do not include the header file in the request",
    )
    parser.add_argument(
        "--post-type",
        "-pt",
        choices=["json", "form"],
        default="json",
        help="Body type for POST requests",
    )
    parser.add_argument(
        "--print-payload", "-pp", action="store_true", help="Print the payload contents"
    )
    parser.add_argument(
        "--print-response",
        "-pr",
        action="store_true",
        help="Print the response contents",
    )
    parser.add_argument(
        "--print-headers", "-ph", action="store_true", help="Print the headers contents"
    )
    parser.add_argument(
        "--json-diff", "-jd", nargs="+", metavar="FILE", help="Compare JSON files"
    )

    args = parser.parse_args()

    if not args.json_diff and not args.url:
        parser.error(
            "the following argument is required: url (unless --payload-diff is used)"
        )

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
                    compare_json(payload, final_payload, file)
            except Exception as e:
                console.print(f"[bold red]Failed to load json:[/] {e}")
        exit(1)

    asyncio.run(
        run_load_test(
            url=args.url,
            num_requests=args.requests,
            concurrency=args.concurrency,
            delay=args.delay,
            method=args.method.upper(),
            payload=payload,
            file_payload=args.file_payload,
            headers=headers,
            post_type=args.post_type,
            print_payload=args.print_payload,
            print_headers=args.print_headers,
            print_response=args.print_response,
        )
    )


if __name__ == "__main__":
    main()
