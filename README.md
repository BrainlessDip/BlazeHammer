### **Blaze Hammer**  
**Launching Soon – Stay Tuned**

---

```json
{"username": "{faker.providers.internet.user_name}"}
↓
{"username": "kpeterson"}
```

**Blaze Hammer** is an asynchronous API stress testing tool built in Python, designed for API load testing with dynamic payload and header generation using placeholder injection. It supports both `GET` and `POST` methods, JSON and form data types, and provides powerful request customization and real-time visual feedback using `rich`.

**Key Features**:
- Asynchronous HTTP load testing with customizable concurrency and delay
- Dynamic placeholder parsing via Faker and custom placeholders
- Payload and header templating using JSON
- Live progress UI and request statistics
- Smart response, payload, and header preview with custom parsers in **utils/custom_parsers.py**
- JSON diffing for placeholder comparisons

Faker providers such as `{faker.providers.internet.email}`, `{faker.providers.address.city}`, and many others can be used in your payloads and headers to generate realistic data dynamically. **Blaze Hammer** ensures precision, speed, and control for API benchmarking, whether you’re simulating high traffic or testing edge cases.

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
  - **Description**: Print the payload contents for each request made during the test. This helps in debugging and checking the final payload.
  - **Example**: `--print-payload` enables payload printing.

- `--print-response`, `-pr`:  
  - **Description**: Print the response contents for each request made during the test.
  - **Example**: `--print-response` enables response printing.

- `--print-headers`, `-ph`:  
  - **Description**: Print the headers for each request made during the test.
  - **Example**: `--print-headers` enables header printing.

- `--json-diff`, `-jd` (optional):  
  - **Description**: Compare and show differences between multiple payload files after processing with the placeholder function. This is useful for testing and debugging payload transformations.
  - **Example**: `--json-diff payload1.json`

By defining custom parsers in `utils/custom_parsers.py`, you can easily modify how **Blaze Hammer** handles responses for specific HTTP status codes. This allows for better reporting, debugging, and response handling tailored to the specific needs of your stress testing and benchmarking.

### **Faker Provider Support in Payloads**:

You can utilize the full power of [Faker](https://github.com/joke2k/faker) in your payloads and headers by using its provider functions directly or even custom placeholders.

**Full list of Faker providers:**  
[https://faker.readthedocs.io/en/stable/providers.html](https://faker.readthedocs.io/en/stable/providers.html)

Here are a few examples:

- `{faker.name}` - random name
- `{faker.job}` - random job
- `{faker.custom(field=job,locale=bn_BD)}` - generate a custom field with a custom locale
- `{faker.providers.internet.email}`: Generates a random email address.
- `{faker.providers.address.city}`: Generates a random city name.
- `{faker.providers.date_time.date_this_year}`: Generates a random date in the current year.
- `{faker.providers.person.name}`: Generates a random name.

**Example**:
```json
{ 
  "name": "{faker.name}",
  "job": "{faker.job}",
  "job_bd": "{faker.custom(field=job,locale=bn_BD)}",
  "address_bd": "{faker.custom(field=address,locale=bn_BD)}",
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

These placeholders are dynamically replaced during each request, ensuring that every test run is unique and realistic
More payload examples in the `payload_example.json` file

---

### **Run Blaze Hammer**:
Once you've set up the arguments and payloads, you can run **Blaze Hammer** by executing the following command:

```bash
python main.py <target_url> -n 200 -c 10 -d 0.5 --method POST --payload payload.json --headers headers.json --print-payload --print-response
```

This will send 200 POST requests with a 0.5-second delay between each, printing both the payload and the response for each request.