import re
import string
import secrets
import uuid
import random
import json
import time
import ast
import inspect
from datetime import datetime
from faker import Faker
from utils.random_functions import generate_random_email, generate_random_number, generate_random_string, generate_random_float, generate_password

faker = Faker()

def parse_args(content):
    return dict(re.findall(r"(\w+)=([^,{}()]+)", content))

def replace_placeholders(obj):
    if isinstance(obj, dict):
        return {k: replace_placeholders(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [replace_placeholders(i) for i in obj]
    elif isinstance(obj, str):
        def repl(match):
            raw_content = match.group(1).strip()
            # Resolve nested placeholders first
            content = replace_placeholders(raw_content)
            
            # {uuid}
            if content == "uuid":
                return str(uuid.uuid4())
            
            # {email(prefix=user_, length=10, domains=gmail.com*hotmail.com*yahoo.com)}
            if content.startswith("email"):
                args = parse_args(content)
                return generate_random_email(prefix=args.get("prefix", "Human"), length=int(args.get("length", 5)), domains=args.get("domains", "gmail.com").split("*"))
            
            # {number(start=9,length=11)}
            if content.startswith("number"):
                args = parse_args(content)
                return generate_random_number(args.get("start", "019"), int(args.get("length", 11)))
            
            # {str(length=16)}
            if content.startswith("str") or content.startswith("string"):
                args = parse_args(content)
                return generate_random_string(int(args.get("length", 8)))
            
            # {ip}
            if content.startswith("ip"):
                return f"{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(0, 255)}"
            
            # {int(max=100)}
            if content.startswith("int"):
                args = parse_args(content)
                return str(random.randint(int(args.get("min", 1)), int(args.get("max", 100))))
            
            # {float(min=1, max=5, precision=1)}
            if content.startswith("float"):
                args = parse_args(content)
                return generate_random_float(float(args.get("min", 0)), float(args.get("max", 1)), int(args.get("precision", 2)))
            
            # {choice(hey,hi,bye)}
            if content.startswith("choice"):
                choices = re.findall(r"choice\((.*?)\)", content)
                if choices:
                    return random.choice([x.strip() for x in choices[0].split(",")])
            
            # {date} , {date(format=%A, %d %B %Y %I:%M:%S %p)} 
            if content.startswith("date"):
                args = parse_args(content)
                fmt = args.get("format", "%Y-%m-%d")
                return datetime.now().strftime(fmt)
            
            # {timestamp} - 
            if content.startswith("timestamp"):
                return str(int(time.time()))
            
            # {password(length=6,digits=false)} - 
            if content.startswith("password"):
                args = parse_args(content)
                return generate_password(
                    length=int(args.get("length", 8)),
                    uppercase=args.get("uppercase", "true").lower() == "true",
                    lowercase=args.get("lowercase", "true").lower() == "true",
                    digits=args.get("digits", "true").lower() == "true",
                    symbols=args.get("symbols", "false").lower() == "true")
            
            # --- FAKE DATA HANDLING ---
            if content.startswith("faker."):
                args = parse_args(content)
                locale = args.get("locale")
                fake = Faker(locale) if locale else faker

                # Support {faker.profile(field=name)}
                if content.startswith("faker.profile"):
                    field = args.get("field", "job")
                    try:
                        return str(fake.profile()[field])
                    except KeyError:
                        return f"[Invalid profile field: {field}]"

                # Support {faker.custom(field=name,locale=bn_BD)}
                if content.startswith("faker.custom"):
                    field = args.get("field")
                    if field and hasattr(fake, field):
                        return str(getattr(fake, field)())
                    return f"[Invalid custom field: {field}]"

                # Match {faker.field} or {faker.providers.module.method(...)}
                match_field = re.match(r"faker\.((providers\.[\w\.]+)|[\w_]+)(\((.*?)\))?", content)
                if match_field:
                    full_path = match_field.group(1)
                    arg_string = match_field.group(4)

                    # Parse arguments safely
                    kwargs = {}
                    if arg_string:
                        for k, v in re.findall(r"(\w+)=([^,]+)", arg_string):
                            try:
                                kwargs[k] = ast.literal_eval(v)
                            except:
                                kwargs[k] = v

                    # Handle providers like faker.providers.python.pyint()
                    if full_path.startswith("providers."):
                        try:
                            parts = full_path.split(".")
                            provider_module = ".".join(parts[1:-1])
                            method_name = parts[-1]
                            provider = __import__(f"faker.providers.{provider_module}", fromlist=["Provider"]).Provider
                            method = getattr(provider(fake), method_name)
                            if callable(method):
                                return str(method(**kwargs))
                            return method
                        except Exception as e:
                            return f"[Invalid provider call: {e}]"
                    else:
                        # Normal faker.field like faker.name or faker.email
                        if hasattr(fake, full_path):
                            method = getattr(fake, full_path)
                            if callable(method):
                                return str(method(**kwargs))
                            return method
                        return f"[Invalid faker field: {full_path}]"
            return match.group(0)

        # Recursively replace all placeholders
        while "{" in obj and "}" in obj:
            if "bool" in obj:
              return random.choice([True,False])
            new_obj = re.sub(r"\{([^{}]+)\}", repl, obj)
            if new_obj == obj:
                break
            obj = new_obj
        return obj
    return obj