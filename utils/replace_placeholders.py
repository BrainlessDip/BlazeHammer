import re
import random
import uuid
import time
import ast
from datetime import datetime
from faker import Faker
from utils.custom_providers import SimpleExampleProvider, AdvancedExampleProvider
from utils.random_functions import (
    generate_random_email,
    generate_random_number,
    generate_random_string,
    generate_random_float,
    generate_password,
    pick_line,
)

PLACEHOLDER_PATTERN = re.compile(r"\{([^{}]+)\}")
ARGS_PATTERN = re.compile(r"(\w+)=([^,{}()]+)")
CHOICE_PATTERN = re.compile(r"choice\((.*?)\)")
FAKER_PATTERN = re.compile(r"faker\.((providers\.[\w\.]+)|[\w_]+)(\((.*?)\))?")
PROVIDER_PATTERN = re.compile(r"providers\.([\w\.]+)")

faker = Faker()
faker.add_provider(SimpleExampleProvider)
faker.add_provider(AdvancedExampleProvider)


def parse_args(content):
    return dict(ARGS_PATTERN.findall(content))


def replace_placeholders(obj, _depth=0):
    if _depth > 10:
        return obj

    if isinstance(obj, dict):
        return {k: replace_placeholders(v, _depth + 1) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [replace_placeholders(i, _depth + 1) for i in obj]
    elif not isinstance(obj, str):
        return obj

    if "{" not in obj or "}" not in obj:
        return obj

    def repl(match):
        content = match.group(1).strip()

        if content == "uuid":
            return str(uuid.uuid4())
        elif content == "timestamp":
            return str(int(time.time()))
        elif content == "bool":
            return str(random.choice([True, False]))
        elif content == "ip":
            return f"{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(0, 255)}"

        if content.startswith(
            ("email", "number", "str", "string", "int", "float", "password")
        ):
            args = parse_args(content)

            if content.startswith("email"):
                return generate_random_email(
                    prefix=args.get("prefix", "Human"),
                    length=int(args.get("length", 5)),
                    domains=args.get("domains", "gmail.com").split("*"),
                )
            elif content.startswith("number"):
                return generate_random_number(
                    args.get("start", "019"), int(args.get("length", 11))
                )
            elif content.startswith(("str", "string")):
                return generate_random_string(int(args.get("length", 8)))
            elif content.startswith("int"):
                return str(
                    random.randint(int(args.get("min", 1)), int(args.get("max", 100)))
                )
            elif content.startswith("float"):
                return generate_random_float(
                    float(args.get("min", 0)),
                    float(args.get("max", 1)),
                    int(args.get("precision", 2)),
                )
            elif content.startswith("password"):
                return generate_password(
                    length=int(args.get("length", 8)),
                    uppercase=args.get("uppercase", "true").lower() == "true",
                    lowercase=args.get("lowercase", "true").lower() == "true",
                    digits=args.get("digits", "true").lower() == "true",
                    symbols=args.get("symbols", "false").lower() == "true",
                )

        elif content.startswith("pick_line"):
            args = parse_args(content)
            return str(pick_line(args.get("file")))

        elif content.startswith("choice"):
            choices = CHOICE_PATTERN.search(content)
            if choices:
                return random.choice([x.strip() for x in choices.group(1).split(",")])

        elif content.startswith("date"):
            args = parse_args(content)
            return datetime.now().strftime(args.get("format", "%Y-%m-%d"))

        elif content.startswith("faker."):
            return handle_faker_content(content)

        return match.group(0)

    while True:
        new_obj = PLACEHOLDER_PATTERN.sub(repl, obj)
        if new_obj == obj:
            break
        obj = new_obj

    return obj


def handle_faker_content(content):
    """Handler for faker-related placeholders"""
    args = parse_args(content)
    locale = args.get("locale")
    fake = Faker(locale) if locale else faker

    if content.startswith("faker.profile"):
        field = args.get("field", "job")
        try:
            return str(fake.profile()[field])
        except KeyError:
            return f"[Invalid profile field: {field}]"

    if content.startswith("faker.custom"):
        field = args.get("field")
        if field and hasattr(fake, field):
            return str(getattr(fake, field)())
        return f"[Invalid custom field: {field}]"

    match = FAKER_PATTERN.match(content)
    if not match:
        return content

    full_path = match.group(1)
    arg_string = match.group(4)
    kwargs = {}

    if arg_string:
        for k, v in ARGS_PATTERN.findall(arg_string):
            try:
                kwargs[k] = ast.literal_eval(v)
            except (ValueError, SyntaxError):
                kwargs[k] = v

    if full_path.startswith("providers."):
        try:
            provider_match = PROVIDER_PATTERN.match(full_path)
            if provider_match:
                provider_path = provider_match.group(1).split(".")
                module = "faker.providers." + ".".join(provider_path[:-1])
                method_name = provider_path[-1]
                provider = __import__(module, fromlist=["Provider"]).Provider
                method = getattr(provider(fake), method_name)
                return str(method(**kwargs)) if callable(method) else str(method)
        except Exception as e:
            return f"[Provider error: {e}]"
    elif hasattr(fake, full_path):
        method = getattr(fake, full_path)
        return str(method(**kwargs)) if callable(method) else str(method)

    return f"[Invalid faker field: {full_path}]"
