import string
import secrets
import random

def generate_random_email(prefix="Brainless_Dip+", length=10, domains=None):
  if domains is None:
    domains = ["gmail.com"]
  charset = string.ascii_letters + string.digits
  random_part = ''.join(secrets.choice(charset) for _ in range(length))
  domain = secrets.choice(domains)
  return f"{prefix}{random_part}@{domain}"

def generate_random_number(start="013", length=8):
  if len(start) > length:
    raise ValueError(f"The 'start' string has a length of {len(start)}, which exceeds the maximum allowed length of {length}")
  digits = "0123456789"
  rest = ''.join(secrets.choice(digits) for _ in range(length - len(start)))
  return f"{start}{rest}"

def generate_random_string(length=8):
  charset = string.ascii_letters + string.digits
  return ''.join(secrets.choice(charset) for _ in range(length))

def generate_random_float(min_val, max_val, precision=2):
  return str(round(random.uniform(min_val, max_val), precision))

def generate_password(length=12, uppercase=True, lowercase=True, digits=True, symbols=False):
  charset = ""
  if uppercase:
    charset += string.ascii_uppercase
  if lowercase:
    charset += string.ascii_lowercase
  if digits:
    charset += string.digits
  if symbols:
    charset += "!@#$%^&*()-_=+[]{}<>?/"

  if not charset:
    return "[Invalid password settings: no character sets enabled]"
  return ''.join(secrets.choice(charset) for _ in range(length))

# Global cache to store file content
cache = {}
def pick_line(file):
   if file not in cache:
     try:
       with open(file, 'r') as file:
         cache[file] = file.readlines()
     except Exception as e:
       return str(e)
   return random.choice(cache[file]).strip()