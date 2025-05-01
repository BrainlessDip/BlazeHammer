### **Blaze Hammer**  
**Launching Soon – Stay Tuned**

---

```json
{"username": "{faker.providers.internet.user_name}"}
↓
{"username": "kpeterson"}
```

**Blaze Hammer** is an asynchronous API spamming tool built in Python. It is designed for stress testing APIs by generating dynamic payloads and headers through placeholder injection. It supports both `GET` and `POST` methods, JSON and form data types, and offers powerful customization options. Real-time visual feedback is provided through `rich` for a better user experience.

**Key Features**:
- Asynchronous HTTP requests with customizable concurrency and delays.
- Dynamic placeholder parsing via Faker and custom placeholders.
- Payload and header templating using JSON.
- Live progress UI and request statistics.
- Smart response, payload, and header preview with custom parsers in **`utils/custom_parsers.py`**.
- JSON diffing for placeholder comparisons.
- Easily add custom Providers for Faker in **`utils/custom_providers.py`**.

Faker providers such as `{faker.providers.internet.email}`, `{faker.providers.address.city}`, and many others can be used in your payloads and headers to generate dynamic, realistic data. **Blaze Hammer** ensures precision, speed, and control for API benchmarking, whether you're simulating high traffic or spamming edge cases.

---

### **Help & Argument Details**:

#### **Arguments**:
- `url` (required):  
  - **Description**: The target API URL you wish to test.
  - **Example**: `http://example.com/api/endpoint`

- `-n`, `--requests` (default: 100):  
  - **Description**: The total number of requests to be sent during the test.
  - **Example**: `-n 200` will send 200 requests.

- `-c`, `--concurrency` (default: 10):  
  - **Description**: The number of concurrent requests that will be sent in parallel.
  - **Example**: `-c 5` will send 5 requests concurrently.

- `-d`, `--delay` (default: 0):  
  - **Description**: Delay in seconds between requests to simulate real-world traffic patterns.
  - **Example**: `-d 0.5` will introduce a 0.5-second delay between requests.

- `-m`, `--method` (default: `GET`):  
  - **Description**: HTTP method to use. Can be `GET` or `POST`.
  - **Example**: `-m POST` will use the POST method.

- `--payload`, `-p` (default: `payload.json`):  
  - **Description**: Path to the JSON payload file. The payload file can include placeholders like `{faker.providers.internet.email}` to generate dynamic data.
  - **Example**: `--payload payload.json`

- `--headers`, `--h` (default: `headers.json`):  
  - **Description**: Path to the JSON headers file. Similar to payloads, headers can include dynamic placeholders for custom headers.
  - **Example**: `--headers headers.json`

- `--post-type`, `-pt` (default: `json`):  
  - **Description**: Defines the body type for POST requests. Can be either `json` or `form`.
  - **Example**: `-pt form` will send data as `application/x-www-form-urlencoded` instead of JSON.

- `--print-payload`, `-pp`:  
  - **Description**: Print the payload contents for each request made during the test. This helps in debugging and verifying the final payload.
  - **Example**: `--print-payload` enables payload printing.

- `--print-response`, `-pr`:  
  - **Description**: Print the response contents for each request made during the test.
  - **Example**: `--print-response` enables response printing.

- `--print-headers`, `-ph`:  
  - **Description**: Print the headers for each request made during the test.
  - **Example**: `--print-headers` enables header printing.

- `--json-diff`, `-jd` (optional):  
  - **Description**: Compare and show differences between multiple payload files after processing with the placeholder function. Useful for spamming and debugging payload transformations.
  - **Example**: `--json-diff payload1.json`

Custom parsers in `utils/custom_parsers.py` allow you to modify how **Blaze Hammer** handles responses based on HTTP status codes. This provides better reporting, debugging, and response handling tailored to your stress tests.

---

### **Faker Provider Support in Payloads**:

You can fully utilize [Faker](https://github.com/joke2k/faker) in your payloads and headers by using its provider functions directly or even custom placeholders.

**Full list of Faker providers:**  
[https://faker.readthedocs.io/en/stable/providers.html](https://faker.readthedocs.io/en/stable/providers.html)

Some examples:

- `{faker.name}` - Generates a random name.
- `{faker.job}` - Generates a random job.
- `{faker.custom(field=job, locale=bn_BD)}` - Generates a custom field with a custom locale.
- `{faker.providers.internet.email}`: Generates a random email address.
- `{faker.providers.address.city}`: Generates a random city name.
- `{faker.providers.date_time.date_this_year}`: Generates a random date in the current year.
- `{faker.providers.person.name}`: Generates a random name.

Built-in placeholders:
- `{uuid}` - Generates a UUID.
- `{email(prefix=user_, length=10, domains=gmail.com*hotmail.com)}` - Generates a random email.
- `{number(start=019,length=11)}` - Generates a random number.
- `{str(length=16)}` - Generates a random dummy string.
- `{ip}` - Generates a random IP address.
- `{int(min=10, max=100)}` - Generates a random integer.
- `{float(min=1, max=5, precision=1)}` - Generates a floating-point value.
- `{choice(hey, hi, bye)}` - Chooses any item from the provided options.
- `{date}`, `{date(format='%A %d %B %Y %I:%M:%S %p')}` – Generates a date, formatted according to the specified format. You can customize the format using Python's [datetime format codes](https://docs.python.org/3/library/datetime.html#format-codes)
- `{timestamp}` - Current timestamp.
- `{password(length=10, digits=false,uppercase=true,lowercase=false,symbols=false)}` - Generates a random password.
- `{pick_line(file=path/to/file.txt)}` – Picks a random line from the specified file

**Example**:
```json
{ 
  "name": "{faker.name}",
  "job": "{faker.job}",
  "job_bd": "{faker.custom(field=job, locale=bn_BD)}",
  "address_bd": "{faker.custom(field=address, locale=bn_BD)}",
  "username": "{faker.providers.internet.user_name}",
  "email": "{faker.providers.internet.email}",
  "address": "{faker.providers.address.city}",
  "created_at": "{faker.providers.date_time.date_this_year}"
}
⬇️
{
  "name": "Terry Beck",
  "job": "Research scientist (maths)",
  "job_bd": "Job specific to Bangladesh",
  "address_bd": "Address specific to Bangladesh",
  "username": "kpeterson",
  "email": "kgrant@example.com",
  "address": "Daisy borough",
  "created_at": "2025-03-14"
}
```

These placeholders are dynamically replaced during each request, ensuring that every test run is unique and realistic. For more payload examples, see `payload_example.json`.

---

### **Run Blaze Hammer**:

Once you've set up the arguments and payloads, you can run **Blaze Hammer** with the following command:

```bash
python main.py <target_url> -n 200 -c 10 -d 0.5 --method POST --payload payload.json --headers headers.json --print-payload --print-response
```

This will send 200 POST requests with a 0.5-second delay between each, printing both the payload and the response for each request.

--- 
For detailed documentation on **Blaze Hammer**, you can visit the [DeepWiki](https://deepwiki.com/BrainlessDip/BlazeHammer) for in-depth information and instructions